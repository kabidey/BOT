"""Phase 20 — Two-tier tool-call cache.

Tier 1: in-process LRU (size 512), zero-latency.
Tier 2: Mongo `tool_call_cache` collection, TTL-bound, survives restart.

Key: `(tool_name, params_hash, role_scope)`. The role_scope is included so a
visitor cache row never serves PII to an unauthenticated caller (the row
would have been stripped by the adapter anyway, but defence-in-depth).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_LRU_MAX = 512
_DEFAULT_TTL = 90
_lru: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()


def _hash_key(tool_name: str, params: Dict[str, Any], role: str) -> str:
    canon = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
    raw = f"{tool_name}|{role}|{canon}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


async def get(db, *, tool_name: str, params: Dict[str, Any], role: str) -> Optional[Any]:
    key = _hash_key(tool_name, params, role)
    # Tier 1
    hit = _lru.get(key)
    if hit and hit[0] > time.time():
        _lru.move_to_end(key)
        return {"value": hit[1], "tier": "lru"}
    if hit:
        _lru.pop(key, None)
    # Tier 2
    if db is None:
        return None
    try:
        doc = await db.tool_call_cache.find_one({"_id": key}, {"_id": 0, "value": 1, "expires_at": 1})
    except Exception:
        return None
    if doc and doc.get("expires_at", 0) > time.time():
        _lru[key] = (doc["expires_at"], doc["value"])
        _lru.move_to_end(key)
        _trim()
        return {"value": doc["value"], "tier": "mongo"}
    return None


async def put(db, *, tool_name: str, params: Dict[str, Any], role: str,
              value: Any, ttl_seconds: int = _DEFAULT_TTL) -> None:
    key = _hash_key(tool_name, params, role)
    exp = time.time() + max(1, int(ttl_seconds))
    _lru[key] = (exp, value)
    _lru.move_to_end(key)
    _trim()
    if db is None:
        return
    try:
        await db.tool_call_cache.replace_one(
            {"_id": key},
            {"_id": key, "tool_name": tool_name, "role": role,
             "value": value, "expires_at": exp, "cached_at": time.time()},
            upsert=True,
        )
    except Exception:
        logger.exception("tool_call_cache write failed (non-fatal)")


def _trim() -> None:
    while len(_lru) > _LRU_MAX:
        _lru.popitem(last=False)


def clear() -> None:
    _lru.clear()
