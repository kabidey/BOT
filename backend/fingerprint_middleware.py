"""Phase 22 — Silent-block FastAPI middleware for device-fingerprint fraud.

The middleware is registered for the whole `/api` surface but bypasses a
small allow-list:

  * `/api/admin/*`            (admins must never lock themselves out — and
                                 are also gated by X-Admin-Token already)
  * `/api/health`, `/api/docs`, `/api/openapi.json`, `/api/redoc`
  * `/api/widget/config`      (public bootstrap)
  * `/api/charts/*.png`        (static file serve)

Phase 22.1 — Fingerprint resolution is now a 3-step chain to close the
streaming-bypass discovered in the live preview sweep (some FE call sites
use `fetch()`/SSE and skipped axios's interceptor):

  1. `X-Client-Fingerprint` request header (preferred — full confidence).
  2. `sessions.fingerprint_hash` looked up by session_id (medium — the
     session previously presented a header on its first auth turn and we
     stamped it). This catches streaming POSTs that forgot the header.
  3. `ip_ua:<sha256(ip|ua)[:32]>` composite fallback (low confidence —
     keeps the control alive for new sessions on misconfigured clients).

Every resolution emits a `fingerprint_resolution_source` security event so
operators can verify on the Fraud Watch counter that the explicit-header
path is at 100 % in healthy traffic. The attacker can NEVER tell which
source resolved their fingerprint — silent-block responses are byte-identical.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

import fingerprint_guard as fg

logger = logging.getLogger("fingerprint_middleware")

# Paths the middleware skips entirely.
_BYPASS_PREFIXES = (
    "/api/admin/",       # admin console (token-gated separately)
    "/api/docs",
    "/api/redoc",
    "/api/openapi.json",
    "/api/health",
    "/api/widget/config",
    "/api/charts/",
)

# Pull `session_id` out of the URL path: `/api/sessions/<sid>/...`
_SESSION_PATH_RE = re.compile(r"^/api/sessions/([^/]+)/")


def _should_bypass(path: str) -> bool:
    return any(path.startswith(p) for p in _BYPASS_PREFIXES)


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return (request.client.host if request.client else "") or ""


def _extract_session_id_from_path(path: str) -> Optional[str]:
    m = _SESSION_PATH_RE.match(path)
    return m.group(1) if m else None


async def _maybe_read_body_session_id(request: Request) -> Optional[str]:
    """For POST /api/agent/turn, /api/chat, /api/agent/turn/stream the
    session_id lives in the JSON body. Starlette's `Request.body()` caches
    the bytes so the actual handler can still consume them."""
    try:
        body_bytes = await request.body()
        if not body_bytes:
            return None
        data = json.loads(body_bytes.decode("utf-8"))
        if isinstance(data, dict):
            sid = data.get("session_id")
            return str(sid) if isinstance(sid, str) and sid else None
    except Exception:
        pass
    return None


def _ip_ua_composite(ip: str, ua: str) -> str:
    """Last-resort fingerprint when the FE forgot to set the header AND
    no session-level FP exists yet. Lower confidence — flips on a new
    user-agent rev or a NAT IP change — but better than no enforcement
    at all. Tagged `ip_ua:` so the admin can tell it apart."""
    if not ip and not ua:
        return ""
    composite = f"{ip}|{ua}".encode("utf-8")
    return "ip_ua:" + hashlib.sha256(composite).hexdigest()[:32]


def _silent_response_for_path(path: str, session_id: Optional[str]) -> JSONResponse:
    """Pick the silent payload that best mimics a real soft failure for
    the given endpoint. Status code is ALWAYS 200 — no 403, no error envelope
    that would tip off the attacker."""
    if path == "/api/chat":
        return JSONResponse(content=fg.silent_block_legacy_chat(), status_code=200)
    if path.startswith("/api/agent/turn"):
        # /api/agent/turn AND /api/agent/turn/stream — same chat envelope
        # works for both because the SSE client will still parse a JSON body
        # if it doesn't see an `event:` prefix, and the FE always falls back
        # to render `.blocks`.
        return JSONResponse(content=fg.silent_block_chat_response(session_id),
                             status_code=200)
    if path.startswith("/api/sessions"):
        return JSONResponse(content=fg.silent_block_chat_response(session_id),
                             status_code=200)
    if path.startswith("/api/rag/search"):
        return JSONResponse(content={"hits": [], "query": "", "total": 0},
                             status_code=200)
    if path.startswith("/api/leads"):
        return JSONResponse(content={"lead_id": "pending",
                                       "message": "Thank you. We'll be in touch."},
                             status_code=200)
    if path.startswith("/api/handoff"):
        return JSONResponse(content={"handoff_id": "pending",
                                       "lead_id": "pending",
                                       "target_has_contact": False,
                                       "handoff_type": "email",
                                       "should_callback_form": True,
                                       "message_preview": ""},
                             status_code=200)
    return JSONResponse(content=fg.silent_block_empty_data(), status_code=200)


class FingerprintGuardMiddleware(BaseHTTPMiddleware):
    """Phase 22 — silent-block middleware with Phase 22.1 fallback chain."""

    def __init__(self, app, db) -> None:
        super().__init__(app)
        self.db = db

    async def _resolve_fingerprint(
        self,
        request: Request,
        sid: Optional[str],
        ip: str,
        ua: str,
    ) -> Tuple[str, str]:
        """Returns (fingerprint_hash, source) where source ∈
        {"header", "session", "ip_ua", ""}. Empty string means "no FP
        available at all" — the middleware will let the request through
        and skip enforcement (zero-trust without a key is meaningless)."""
        header_fp = (request.headers.get("x-client-fingerprint") or "").strip()
        if header_fp:
            return header_fp, "header"
        if sid:
            try:
                row = await self.db.sessions.find_one(
                    {"_id": sid},
                    {"_id": 0, "fingerprint_hash": 1,
                     "last_request_ctx": 1},
                ) or {}
                cached = (row.get("fingerprint_hash") or "").strip()
                if not cached:
                    rc = row.get("last_request_ctx") or {}
                    cached = (rc.get("fingerprint_hash") or "").strip()
                if cached:
                    return cached, "session"
            except Exception:
                logger.debug("session FP lookup failed", exc_info=True)
        composite = _ip_ua_composite(ip, ua)
        if composite:
            return composite, "ip_ua"
        return "", ""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path or ""
        if not path.startswith("/api/") or _should_bypass(path):
            return await call_next(request)

        tz = (request.headers.get("x-client-tz") or "").strip()[:60]
        screen = (request.headers.get("x-client-screen") or "").strip()[:24]
        ua = (request.headers.get("user-agent") or "").strip()[:240]
        ip = _client_ip(request)

        # Resolve session_id so we can fall back through the session row.
        sid: Optional[str] = _extract_session_id_from_path(path)
        if not sid and request.method.upper() == "POST":
            sid = await _maybe_read_body_session_id(request)

        fp_hash, source = await self._resolve_fingerprint(request, sid, ip, ua)
        if not fp_hash:
            # No fingerprint at all (probably a curl probe / health-check
            # missing a UA). Let it pass — same behaviour as Phase 22.0.
            return await call_next(request)

        # Telemetry — emit on every resolution so admins can see the
        # explicit-header rate trend. Failure is non-fatal.
        try:
            await self._record_resolution_source(fp_hash, source, path, sid)
        except Exception:
            logger.debug("resolution_source telemetry failed", exc_info=True)

        # Cheap signal upsert (last_seen, IP/UA/TZ variety arrays).
        try:
            await fg.record_request_signal(
                self.db, fp_hash, ip=ip, ua=ua, tz=tz, screen=screen,
            )
        except Exception:
            logger.debug("record_request_signal failed (non-fatal)", exc_info=True)

        # Stamp the FP onto the session row so subsequent turns can resolve
        # via the session-fallback even if their headers go missing again.
        # NB: many handlers (select_role, /api/chat with null sid, etc.)
        # create the session lazily, so the first attempt here will be a
        # no-op on `upsert=False`. We retry after `call_next` to catch
        # those just-created rows.
        async def _stamp_session(only_ctx: bool = False) -> None:
            if not sid:
                return
            update: Dict[str, Any] = {"last_request_ctx": {
                "fingerprint_hash": fp_hash,
                "ip": ip, "ua": ua, "tz": tz, "screen": screen,
            }}
            if source == "header" and not only_ctx:
                update["fingerprint_hash"] = fp_hash
            try:
                await self.db.sessions.update_one(
                    {"_id": sid}, {"$set": update}, upsert=False,
                )
            except Exception:
                logger.debug("session FP stamp failed", exc_info=True)

        await _stamp_session()

        # Block-check is the hot path — index-backed `find_one` on a single
        # field. <1ms typical.
        try:
            blocked = await fg.is_blocked(self.db, fp_hash)
        except Exception:
            logger.exception("is_blocked check failed (fail-open)")
            blocked = False

        if blocked:
            try:
                await fg.record_silent_block_served(self.db, fp_hash, path)
            except Exception:
                logger.debug("record_silent_block_served failed", exc_info=True)
            return _silent_response_for_path(path, sid)

        response = await call_next(request)
        # Re-stamp after the handler so just-created session rows pick up the
        # FP. This closes the Phase 22.1 session-fallback gap where the
        # session_id was minted by `select_role` / `/api/chat` after the
        # middleware's first stamp attempt no-op'd.
        if sid and source == "header":
            await _stamp_session()
        return response

    async def _record_resolution_source(
        self, fp_hash: str, source: str, path: str, sid: Optional[str],
    ) -> None:
        """Append a `fingerprint_resolution_source` security event. We sample
        100 % of `session` + `ip_ua` events (rare in healthy traffic) and
        1-in-50 of `header` events (high volume — full sampling would
        overwhelm `security_events`)."""
        if not source:
            return
        if source == "header":
            # Cheap sampler: hash + bucket. Deterministic per FP so a single
            # busy device shows up reliably.
            digest = int(hashlib.sha1(fp_hash.encode("utf-8")).hexdigest()[:6], 16)
            if digest % 50 != 0:
                return
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        await self.db.security_events.insert_one({
            "kind": "fingerprint_resolution_source",
            "session_id": sid,
            "role_state_value": None,
            "user_message": f"source={source} path={path}",
            "user_message_excerpt": f"source={source} path={path}",
            "path": path,
            "source": source,
            "action": "resolution",
            "ts": now_iso,
            "ts_dt": datetime.now(timezone.utc),
            "created_at": now_iso,
            "fingerprint_hash": fp_hash,
        })
