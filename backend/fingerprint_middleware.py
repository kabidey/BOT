"""Phase 22 — Silent-block FastAPI middleware for device-fingerprint fraud.

The middleware is registered for the whole `/api` surface but bypasses a
small allow-list:

  * `/api/admin/*`            (admins must never lock themselves out — and
                                 are also gated by X-Admin-Token already)
  * `/api/health`, `/api/docs`, `/api/openapi.json`, `/api/redoc`
  * `/api/widget/config`      (public bootstrap)
  * `/api/charts/*.png`        (static file serve)

For every other request it:

  1. Reads the three client headers (`X-Client-Fingerprint`,
     `X-Client-Tz`, `X-Client-Screen`).
  2. Best-effort upserts the fingerprint row + bumps IP / UA / TZ / screen
     signal counters.
  3. Stashes the request context onto the session document so the auth
     agent can later call `record_identity_binding` with the same hash.
  4. If the fingerprint is blocked (and not admin-trusted), returns a
     route-shape-specific silent response (NEVER a 403). Otherwise the
     request proceeds normally.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, Dict, Optional

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

# Identity-verification routes — silent-block returns a generic "couldn't
# verify" payload that is indistinguishable from a wrong PAN.
_VERIFY_PATHS = {
    "/api/sessions",   # session_id POST/PUT for select_role / resume use chat shape
}

# Pull `session_id` out of the URL path: `/api/sessions/<sid>/...`
_SESSION_PATH_RE = re.compile(r"^/api/sessions/([^/]+)/")


def _should_bypass(path: str) -> bool:
    if any(path.startswith(p) for p in _BYPASS_PREFIXES):
        return True
    return False


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
    session_id lives in the JSON body. We peek at the body (cached for the
    actual handler to consume it again — starlette's `Request.body()` is
    idempotent)."""
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


def _silent_response_for_path(path: str, session_id: Optional[str]) -> JSONResponse:
    """Pick the silent payload that best mimics a real soft failure for
    the given endpoint. Status code is ALWAYS 200 — no 403, no error envelope
    that would tip off the attacker."""
    if path == "/api/chat":
        return JSONResponse(content=fg.silent_block_legacy_chat(), status_code=200)
    if path.startswith("/api/agent/turn"):
        return JSONResponse(content=fg.silent_block_chat_response(session_id),
                             status_code=200)
    if path.startswith("/api/sessions"):
        # select_role / resume / signout / get — same chat-shape envelope is
        # fine because the FE always renders `.blocks` if present.
        return JSONResponse(content=fg.silent_block_chat_response(session_id),
                             status_code=200)
    if path.startswith("/api/rag/search"):
        return JSONResponse(content={"hits": [], "query": "", "total": 0},
                             status_code=200)
    if path.startswith("/api/leads"):
        # Returns a fake-success envelope so the attacker doesn't suspect a
        # rejection. The lead is NOT persisted (request never reaches handler).
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
    # Generic data endpoint fallback.
    return JSONResponse(content=fg.silent_block_empty_data(), status_code=200)


class FingerprintGuardMiddleware(BaseHTTPMiddleware):
    """Phase 22 — silent-block middleware. The constructor takes the Motor
    `db` handle so the middleware doesn't have to know about FastAPI's app
    state plumbing."""

    def __init__(self, app, db) -> None:
        super().__init__(app)
        self.db = db

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path or ""
        if not path.startswith("/api/") or _should_bypass(path):
            return await call_next(request)

        fp_hash = (request.headers.get("x-client-fingerprint") or "").strip()
        tz = (request.headers.get("x-client-tz") or "").strip()[:60]
        screen = (request.headers.get("x-client-screen") or "").strip()[:24]
        ua = (request.headers.get("user-agent") or "").strip()[:240]
        ip = _client_ip(request)

        # No fingerprint → request still proceeds. We don't want to break old
        # browsers / curl probes. Old admin tools and tests have no FP either.
        if not fp_hash:
            return await call_next(request)

        # Cheap signal upsert (last_seen, IP/UA/TZ variety arrays).
        try:
            await fg.record_request_signal(
                self.db, fp_hash, ip=ip, ua=ua, tz=tz, screen=screen,
            )
        except Exception:
            logger.debug("record_request_signal failed (non-fatal)", exc_info=True)

        # Resolve session_id so the auth_agent can later read it back from
        # the session row (`last_request_ctx`).
        sid: Optional[str] = _extract_session_id_from_path(path)
        if not sid and request.method.upper() == "POST":
            sid = await _maybe_read_body_session_id(request)

        if sid:
            try:
                await self.db.sessions.update_one(
                    {"_id": sid},
                    {"$set": {"last_request_ctx": {
                        "fingerprint_hash": fp_hash,
                        "ip": ip, "ua": ua, "tz": tz, "screen": screen,
                    }}},
                    upsert=False,
                )
            except Exception:
                logger.debug("session last_request_ctx write failed", exc_info=True)

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

        return await call_next(request)
