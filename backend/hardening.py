"""Pre-deploy hardening utilities — log scrubbers, admin-token strength check,
in-process rate limiter, CORS resolution.

Imported once by server.py; intentionally side-effect-free at import time
(no global mutation until the install_*_filters() / check_admin_token_strength()
helpers are explicitly called by the FastAPI app).
"""
from __future__ import annotations
import logging
import os
import re
import time
from collections import deque
from threading import Lock
from typing import Deque, Dict, Iterable, Optional, Tuple

import identity as id_mod

logger = logging.getLogger(__name__)

DEV_DEFAULT_ADMIN_TOKEN = "smifs-admin-2026"


# ===================================================================
# Secret scrub log filter
# ===================================================================
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9\-\._~+/]+=*", re.I)
_APIKEY_HEADER_RE = re.compile(r"(X-API-Key\s*[:=]\s*)['\"]?[A-Za-z0-9\-_]+['\"]?", re.I)
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]{2,})([A-Za-z0-9._%+-]*)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")


def _mask_email(m: re.Match) -> str:
    head = m.group(1)[:2]
    domain = m.group(3)
    return f"{head}***@{domain}"


class SecretScrubFilter(logging.Filter):
    """Scrubs Bearer tokens, X-API-Key headers, the configured Hub/OrgLens keys,
    and (at INFO/DEBUG only) email addresses. WARN/ERROR levels keep emails intact
    so on-call engineers can correlate user reports.
    """

    def __init__(self, literal_secrets: Optional[Iterable[str]] = None) -> None:
        super().__init__()
        # Filter to non-empty literals at least 8 chars long (avoid scrubbing short envs)
        self._literals = tuple(s for s in (literal_secrets or []) if s and len(s) >= 8)

    def _scrub(self, text: str, level: int) -> str:
        if not text:
            return text
        text = _BEARER_RE.sub("Bearer ***", text)
        text = _APIKEY_HEADER_RE.sub(r"\1***", text)
        for lit in self._literals:
            if lit in text:
                text = text.replace(lit, "***")
        if level <= logging.INFO:
            text = _EMAIL_RE.sub(_mask_email, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        try:
            if isinstance(record.msg, str):
                record.msg = self._scrub(record.msg, record.levelno)
            if record.args:
                record.args = tuple(
                    self._scrub(a, record.levelno) if isinstance(a, str) else a
                    for a in record.args
                )
        except Exception:  # never break logging
            pass
        return True


class PanScrubFilter(logging.Filter):
    """Scrubs PAN-shaped tokens regardless of log level."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = id_mod.sanitize_for_log(record.msg)
            if record.args:
                record.args = tuple(
                    id_mod.sanitize_for_log(a) if isinstance(a, str) else a
                    for a in record.args
                )
        except Exception:
            pass
        return True


def install_log_filters() -> None:
    """Attach PanScrubFilter + SecretScrubFilter to root, uvicorn, httpx, and
    every `agents.*` / app-local logger. Idempotent — safe to call repeatedly."""
    literals = [
        os.environ.get("LLMHUB_API_KEY", ""),
        os.environ.get("ORGLENS_API_KEY", ""),
        os.environ.get("ADMIN_TOKEN", ""),
    ]
    pan = PanScrubFilter()
    sec = SecretScrubFilter(literal_secrets=literals)

    targets = [
        "",  # root
        "uvicorn", "uvicorn.access", "uvicorn.error",
        "httpx", "httpcore",
        "agents", "agents.llm", "agents.router", "agents.rag_agent",
        "agents.orchestrator", "agents.auth_agent", "agents.api_agent", "agents.form_agent",
        "server", "admin", "rag", "identity", "archives", "widget_config",
        "cost_ledger", "hardening",
    ]
    for name in targets:
        lg = logging.getLogger(name)
        if not any(isinstance(f, PanScrubFilter) for f in lg.filters):
            lg.addFilter(pan)
        if not any(isinstance(f, SecretScrubFilter) for f in lg.filters):
            lg.addFilter(sec)
        # Also attach to existing handlers so messages emitted via the handler
        # path (uvicorn) are scrubbed even if propagate=False.
        for h in lg.handlers:
            if not any(isinstance(f, PanScrubFilter) for f in h.filters):
                h.addFilter(pan)
            if not any(isinstance(f, SecretScrubFilter) for f in h.filters):
                h.addFilter(sec)
    # Root handler too (basicConfig already added one).
    for h in logging.getLogger().handlers:
        if not any(isinstance(f, PanScrubFilter) for f in h.filters):
            h.addFilter(pan)
        if not any(isinstance(f, SecretScrubFilter) for f in h.filters):
            h.addFilter(sec)


# ===================================================================
# Admin token strength check
# ===================================================================
def check_admin_token_strength() -> str:
    """Returns one of: 'missing' | 'weak' | 'dev_default' | 'ok'.
    Emits a prominent WARNING log line for the first three states.
    """
    token = os.environ.get("ADMIN_TOKEN", "")
    if not token:
        logger.warning("⚠️  ADMIN_TOKEN is NOT SET — admin endpoints will reject all calls.")
        return "missing"
    if token == DEV_DEFAULT_ADMIN_TOKEN:
        logger.warning("⚠️  WEAK ADMIN_TOKEN — rotate via env before production traffic "
                       "(currently using the dev default).")
        return "dev_default"
    if len(token) < 16:
        logger.warning("⚠️  WEAK ADMIN_TOKEN — rotate via env before production traffic "
                       "(token is shorter than 16 chars).")
        return "weak"
    return "ok"


# ===================================================================
# CORS resolution
# ===================================================================
def resolve_cors_origins() -> Tuple[list, str]:
    """Returns (allow_origins, mode) where mode ∈ {'prod', 'permissive', 'legacy'}.

    Resolution order:
      1. CORS_PROD_ORIGINS (csv) — strict prod allowlist
      2. CORS_ORIGINS (csv) — legacy var, used if not '*'
      3. ['*'] permissive (preview default)
    """
    prod = (os.environ.get("CORS_PROD_ORIGINS") or "").strip()
    if prod:
        origins = [o.strip() for o in prod.split(",") if o.strip()]
        return origins, "prod"
    legacy = (os.environ.get("CORS_ORIGINS") or "").strip()
    if legacy and legacy != "*":
        origins = [o.strip() for o in legacy.split(",") if o.strip()]
        return origins, "legacy"
    return ["*"], "permissive"


# ===================================================================
# In-process rate limiter
# ===================================================================
class RateLimiter:
    """Sliding-window per-key counter. Thread-safe via a lock; fine for FastAPI's
    single-process async event loop. If we ever scale to multiple uvicorn workers,
    swap this for Redis (each worker has its own counter today — see DEPLOY_NOTES.md).
    """

    def __init__(self) -> None:
        self._buckets: Dict[Tuple[str, str], Deque[float]] = {}
        self._lock = Lock()

    def check(self, scope: str, key: str, limit: int, window_s: int = 60) -> Tuple[bool, int]:
        """Return (allowed, retry_after_seconds). retry_after is 0 when allowed."""
        if not key:
            return True, 0
        now = time.monotonic()
        cutoff = now - window_s
        bucket_key = (scope, key)
        with self._lock:
            dq = self._buckets.get(bucket_key)
            if dq is None:
                dq = deque()
                self._buckets[bucket_key] = dq
            # Drop old timestamps
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= limit:
                # Time until the oldest entry leaves the window
                retry = max(1, int(window_s - (now - dq[0])) + 1)
                return False, retry
            dq.append(now)
            return True, 0


_GLOBAL_LIMITER = RateLimiter()


def get_limiter() -> RateLimiter:
    return _GLOBAL_LIMITER


def client_ip_from(request) -> str:
    """Best-effort client IP extraction respecting X-Forwarded-For (Kubernetes ingress)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    if request.client:
        return request.client.host
    return "unknown"
