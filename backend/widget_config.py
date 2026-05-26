"""Widget config — single-document collection storing brand + theme + CORS allowlist.
Cached in-memory after first read; the cache is invalidated on PUT/reset.
Concurrency: a single asyncio.Lock guards the cache refresh; reads are lock-free
once the cache is populated."""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

COLLECTION = "widget_config"
DOC_ID = "current"

DEFAULT_CONFIG: Dict[str, Any] = {
    "brand_name": "Mackertich ONE",
    "subtitle": "Wealth Management · SMIFS Ltd",
    "welcome_message": "Welcome to Mackertich ONE. How may I assist you today?",
    "bubble_icon": "💬",
    "position": "bottom-right",
    "theme": {
        # Phase 14 — SMIFS.com palette (smifs deep-green spine, emerald accent).
        # See /app/backend/SMIFS_BRAND.md for the source probe.
        "primary": "#065B40",         # smifs secondary-800 (deep green)
        "accent": "#098C62",          # smifs primary-500 / lightgreen-500
        "background": "#FFFFFF",
        "user_bubble": "#065B40",
        "assistant_bubble": "#F1F5F2", # very faint green tint
        "text": "#191A15",            # smifs ink-900
        "header_bg": "#023726",       # smifs darkest CTA
        "header_text": "#FFFFFF",
    },
    "suggestion_chips": [
        "Tell me about Mackertich ONE",
        "What is an AIF?",
        "I'd like to invest in NCDs",
    ],
    "show_branding_footer": True,
    "allowed_origins": [],
}

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_VALID_POSITIONS = {"bottom-right", "bottom-left"}
_THEME_KEYS = set(DEFAULT_CONFIG["theme"].keys())

_cache: Optional[Dict[str, Any]] = None
_cache_lock = asyncio.Lock()
_db_handle = None  # set via bind_db()


def bind_db(db) -> None:
    global _db_handle
    _db_handle = db


def _strip_id(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in doc.items() if k != "_id"}


def _theme_version(cfg: Dict[str, Any]) -> str:
    """Short hash of theme + brand fields — used to bust iframe caches when the admin saves."""
    blob = json.dumps({k: cfg.get(k) for k in ("brand_name", "subtitle", "welcome_message",
                                                "bubble_icon", "position", "theme",
                                                "suggestion_chips", "show_branding_footer")},
                      sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]


def _public_view(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The shape /api/widget/config returns — strips internal/admin fields."""
    import os
    out = _strip_id(cfg).copy()
    # Don't leak the allowlist or admin metadata to public widget consumers.
    out.pop("allowed_origins", None)
    out.pop("updated_at", None)
    out.pop("updated_by", None)
    out["theme_version"] = _theme_version(cfg)
    # Phase 26a — citation chip visibility is server-driven (env flag).
    # Default false: hide chips from end users. Admin trace/Conversations view
    # ignores this flag and always shows citations.
    out["show_citations_to_user"] = (
        os.environ.get("CHAT_SHOW_CITATIONS_TO_USER", "false").lower() == "true"
    )
    return out


async def _load_from_db() -> Dict[str, Any]:
    db = _db_handle
    if db is None:
        return dict(DEFAULT_CONFIG)
    doc = await db[COLLECTION].find_one({"_id": DOC_ID}, {"_id": 0})
    if not doc:
        # Seed defaults atomically (idempotent: $setOnInsert ensures we don't trample concurrent inits)
        seed = dict(DEFAULT_CONFIG)
        seed["_id"] = DOC_ID
        seed["updated_at"] = datetime.now(timezone.utc).isoformat()
        seed["updated_by"] = "auto-seed"
        await db[COLLECTION].update_one({"_id": DOC_ID}, {"$setOnInsert": seed}, upsert=True)
        doc = await db[COLLECTION].find_one({"_id": DOC_ID}, {"_id": 0})
    # Merge against defaults so newly-introduced fields are populated even on legacy docs.
    merged = dict(DEFAULT_CONFIG)
    merged.update(doc or {})
    if "theme" in (doc or {}):
        merged["theme"] = {**DEFAULT_CONFIG["theme"], **(doc.get("theme") or {})}
    return merged


async def get(force_refresh: bool = False) -> Dict[str, Any]:
    """Returns the active config. Reads from cache; refreshes from DB if cold or forced."""
    global _cache
    if _cache is not None and not force_refresh:
        return _cache
    async with _cache_lock:
        if _cache is None or force_refresh:
            _cache = await _load_from_db()
        return _cache


async def get_public() -> Dict[str, Any]:
    return _public_view(await get())


def origin_allowed(origin: Optional[str], cfg: Dict[str, Any]) -> bool:
    """Empty allowlist = allow any origin. Otherwise exact-match (case-insensitive)."""
    allow = cfg.get("allowed_origins") or []
    if not allow:
        return True
    if not origin:
        return False
    return origin.lower() in {o.strip().lower() for o in allow if o and o.strip()}


# ---------------- Validation + admin mutations ----------------
def _validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Body must be a JSON object.")
    out: Dict[str, Any] = {}

    if "brand_name" in payload:
        v = (payload["brand_name"] or "").strip()
        if not v or len(v) > 80:
            raise ValueError("brand_name must be 1-80 chars.")
        out["brand_name"] = v
    if "subtitle" in payload:
        out["subtitle"] = str(payload.get("subtitle") or "").strip()[:120]
    if "welcome_message" in payload:
        v = (payload["welcome_message"] or "").strip()
        if not v or len(v) > 500:
            raise ValueError("welcome_message must be 1-500 chars.")
        out["welcome_message"] = v
    if "bubble_icon" in payload:
        v = str(payload.get("bubble_icon") or "💬").strip()[:8]
        out["bubble_icon"] = v or "💬"
    if "position" in payload:
        if payload["position"] not in _VALID_POSITIONS:
            raise ValueError(f"position must be one of {_VALID_POSITIONS}")
        out["position"] = payload["position"]
    if "show_branding_footer" in payload:
        out["show_branding_footer"] = bool(payload["show_branding_footer"])
    if "theme" in payload:
        theme = payload["theme"]
        if not isinstance(theme, dict):
            raise ValueError("theme must be an object")
        cleaned = {}
        for k, v in theme.items():
            if k not in _THEME_KEYS:
                continue
            if not isinstance(v, str) or not _HEX.match(v.strip()):
                raise ValueError(f"theme.{k} must be a hex colour like #C9A86A")
            cleaned[k] = v.strip()
        out["theme"] = cleaned
    if "suggestion_chips" in payload:
        chips = payload["suggestion_chips"] or []
        if not isinstance(chips, list) or len(chips) > 5:
            raise ValueError("suggestion_chips must be a list of at most 5 strings")
        out["suggestion_chips"] = [str(c).strip()[:120] for c in chips if str(c).strip()]
    if "allowed_origins" in payload:
        ao = payload["allowed_origins"] or []
        if isinstance(ao, str):
            ao = [s.strip() for s in ao.split(",") if s.strip()]
        if not isinstance(ao, list):
            raise ValueError("allowed_origins must be a list or comma-separated string")
        cleaned: List[str] = []
        for s in ao:
            s = str(s).strip()
            if not s:
                continue
            if not (s.startswith("http://") or s.startswith("https://")) or len(s) > 200:
                raise ValueError(f"allowed_origins entries must be full http(s) origins; bad: {s}")
            cleaned.append(s.rstrip("/"))
        out["allowed_origins"] = cleaned
    return out


async def update(payload: Dict[str, Any], updated_by: str = "admin") -> Dict[str, Any]:
    db = _db_handle
    if db is None:
        raise RuntimeError("widget_config DB not bound")
    valid = _validate(payload)
    valid["updated_at"] = datetime.now(timezone.utc).isoformat()
    valid["updated_by"] = updated_by
    # Single $set — atomic against any concurrent reader/writer.
    await db[COLLECTION].update_one({"_id": DOC_ID}, {"$set": valid}, upsert=True)
    return await get(force_refresh=True)


async def reset(updated_by: str = "admin") -> Dict[str, Any]:
    db = _db_handle
    if db is None:
        raise RuntimeError("widget_config DB not bound")
    fresh = dict(DEFAULT_CONFIG)
    fresh["_id"] = DOC_ID
    fresh["updated_at"] = datetime.now(timezone.utc).isoformat()
    fresh["updated_by"] = updated_by + " (reset)"
    await db[COLLECTION].replace_one({"_id": DOC_ID}, fresh, upsert=True)
    return await get(force_refresh=True)


def admin_token_fingerprint(token: str) -> str:
    """Last 8 chars of sha256(token) — opaque identifier for `updated_by` field."""
    if not token:
        return "anon"
    return "tok-" + hashlib.sha256(token.encode("utf-8")).hexdigest()[-8:]
