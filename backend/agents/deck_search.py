"""Phase 18 — Deck Vector Engine fallback.

Lazy fall-through to the SMIFS deck's server-side semantic search endpoint.
Default OFF (`DECK_SEARCH_FALLBACK != "true"`) — the function short-circuits
to `[]` without making any HTTP call.

Design constraints (from Phase 18 brief):
  * Flag default-off so production has zero behavioural footprint until ops
    flips the env var.
  * Soft kill-switch: 10 consecutive `totalIndexed == 0` responses → suspend
    deck calls for `DECK_SEARCH_BACKOFF_SECONDS` (default 1h). Single
    `security_events` row logged per suspension; auto-resume after window.
  * **Conservative audience drop**: any deck hit whose `source ∈
    {sales_pitch, growth_insurance, growth_revenue}` is dropped for
    visitor / client sessions. No join key dependency — we use the
    `source` string the deck already returns. Drops are logged.
  * Per-call telemetry into `deck_search_calls` (capped @ 50k).
"""
from __future__ import annotations

import collections
import logging
import os
import time
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

DECK_BASE       = (os.environ.get("SMIFS_KNOWLEDGE_BASE_URL") or "").rstrip("/")
DECK_KEY        = os.environ.get("SMIFS_KNOWLEDGE_API_KEY") or ""
DEFAULT_MIN_SCORE = float(os.environ.get("DECK_SEARCH_MIN_SCORE", "0.30"))
DEFAULT_BACKOFF = int(os.environ.get("DECK_SEARCH_BACKOFF_SECONDS", "3600"))
EMPTY_RING_MAX  = 10
CAPPED_CALLS    = 50_000
DECK_TIMEOUT_S  = 30.0

# Phase 16 — employee-only subsources. Same canonical list used by the local
# retrieval audience gate; mirrored here so the deck fallback applies the
# same gate before any deck hit reaches the LLM context.
_EMPLOYEE_ONLY_SUBSOURCES = {"sales_pitch", "growth_insurance", "growth_revenue"}


def enabled() -> bool:
    """Read fresh on every call so an ops flag flip takes effect without a restart."""
    return (os.environ.get("DECK_SEARCH_FALLBACK", "").strip().lower() in ("1", "true", "yes", "on"))


# ---------- soft kill-switch state ----------
_empty_ring: collections.deque = collections.deque(maxlen=EMPTY_RING_MAX)
_suspended_until: float = 0.0   # monotonic clock
_last_call_log: collections.deque = collections.deque(maxlen=EMPTY_RING_MAX)
_call_counter_today = {"date": "", "count": 0, "audience_drops": 0}
_current_total_indexed: Optional[int] = None


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _bump_counters(audience_drops: int = 0) -> None:
    today = _today_iso()
    if _call_counter_today["date"] != today:
        _call_counter_today["date"] = today
        _call_counter_today["count"] = 0
        _call_counter_today["audience_drops"] = 0
    _call_counter_today["count"] += 1
    _call_counter_today["audience_drops"] += audience_drops


def status() -> Dict[str, Any]:
    """Status snapshot for the admin panel."""
    now = time.monotonic()
    suspended = now < _suspended_until
    return {
        "enabled": enabled(),
        "suspended": suspended,
        "suspended_until": (datetime.now(timezone.utc).timestamp() + (_suspended_until - now)) if suspended else None,
        "last_10_calls": list(_last_call_log),
        "total_calls_today": _call_counter_today["count"],
        "audience_drops_today": _call_counter_today["audience_drops"],
        "current_totalIndexed_seen": _current_total_indexed,
        "backoff_seconds": DEFAULT_BACKOFF,
        "min_score": DEFAULT_MIN_SCORE,
    }


def apply_audience_drop(results: List[Dict[str, Any]], session_type: Optional[str],
                        auth_state: Optional[str]) -> tuple[List[Dict[str, Any]], int]:
    """Pure function — testable in isolation.

    Drops any hit whose `source` lands in `_EMPLOYEE_ONLY_SUBSOURCES` when the
    requester is NOT a verified employee. Returns (kept, dropped_count).
    """
    is_employee_verified = (session_type == "employee" and auth_state == "verified")
    if is_employee_verified:
        return list(results), 0
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for h in results:
        src = (h.get("source") or "").strip()
        if src in _EMPLOYEE_ONLY_SUBSOURCES:
            dropped += 1
            continue
        kept.append(h)
    return kept, dropped


async def _log_telemetry(db, row: Dict[str, Any]) -> None:
    if db is None:
        return
    try:
        await db.deck_search_calls.insert_one(row)
        # Rough capped-collection emulation — prune by created_at if oversize.
        n = await db.deck_search_calls.estimated_document_count()
        if n > CAPPED_CALLS:
            # Drop oldest 500 to leave headroom
            old = db.deck_search_calls.find({}, {"_id": 1}).sort("created_at", 1).limit(500)
            ids = [d["_id"] async for d in old]
            if ids:
                await db.deck_search_calls.delete_many({"_id": {"$in": ids}})
    except Exception:
        logger.exception("deck_search telemetry insert failed (non-fatal)")


async def _log_audience_drop_event(db, query: str, dropped: int) -> None:
    if db is None or dropped == 0:
        return
    try:
        await db.security_events.insert_one({
            "kind": "kb_audience_dropped_deck_hit",
            "query_hash": hashlib.sha1(query.encode("utf-8")).hexdigest()[:12],
            "dropped_count": dropped,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "severity": "info",
        })
    except Exception:
        logger.exception("deck_search audience_drop event insert failed (non-fatal)")


async def _log_suspended_event(db) -> None:
    if db is None:
        return
    try:
        await db.security_events.insert_one({
            "kind": "deck_search_suspended_empty_index",
            "consecutive_empty_responses": EMPTY_RING_MAX,
            "backoff_seconds": DEFAULT_BACKOFF,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "severity": "info",
        })
    except Exception:
        logger.exception("deck_search suspension event insert failed (non-fatal)")


async def deck_search(query: str, *, top_k: int = 8, db=None,
                      session_type: Optional[str] = None,
                      auth_state: Optional[str] = None,
                      locale: str = "en") -> List[Dict[str, Any]]:
    """Phase 18 fallback retrieval.

    Returns a list of hit dicts shaped to match `rag.search_weighted` rows so
    `rag_agent._hits_to_chunks` and `_build_citations` can consume them
    unchanged. Returns `[]` (no HTTP) when the flag is OFF or the soft
    kill-switch has suspended calls.
    """
    global _suspended_until, _current_total_indexed
    if not enabled():
        return []
    if not (DECK_BASE and DECK_KEY):
        return []
    now = time.monotonic()
    if now < _suspended_until:
        # Suspended — short-circuit silently.
        return []

    started = time.monotonic()
    body: Dict[str, Any] = {"q": query, "top_k": top_k, "min_score": DEFAULT_MIN_SCORE}
    try:
        async with httpx.AsyncClient(timeout=DECK_TIMEOUT_S) as client:
            r = await client.post(
                f"{DECK_BASE}/api/knowledge/search",
                headers={"X-API-Key": DECK_KEY, "Content-Type": "application/json"},
                json=body,
            )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if r.status_code != 200:
            _last_call_log.append({"ts": datetime.now(timezone.utc).isoformat(),
                                    "status": r.status_code, "elapsed_ms": elapsed_ms,
                                    "results_count_raw": 0})
            await _log_telemetry(db, {"created_at": datetime.now(timezone.utc).isoformat(),
                                       "query_hash": hashlib.sha1(query.encode("utf-8")).hexdigest()[:12],
                                       "top_k": top_k, "status": r.status_code,
                                       "elapsed_ms": elapsed_ms, "locale": locale})
            return []
        payload = r.json() or {}
    except Exception as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _last_call_log.append({"ts": datetime.now(timezone.utc).isoformat(),
                                "status": "exc", "exc": f"{type(e).__name__}",
                                "elapsed_ms": elapsed_ms})
        await _log_telemetry(db, {"created_at": datetime.now(timezone.utc).isoformat(),
                                   "query_hash": hashlib.sha1(query.encode("utf-8")).hexdigest()[:12],
                                   "top_k": top_k, "status": "exc",
                                   "exc": f"{type(e).__name__}", "elapsed_ms": elapsed_ms,
                                   "locale": locale})
        return []

    total_indexed = int(payload.get("totalIndexed") or 0)
    _current_total_indexed = total_indexed
    raw_results = payload.get("results") or []

    # Soft kill-switch: extend the empty-ring on every totalIndexed==0 response.
    if total_indexed == 0:
        _empty_ring.append(time.monotonic())
        if len(_empty_ring) == EMPTY_RING_MAX:
            _suspended_until = time.monotonic() + DEFAULT_BACKOFF
            _empty_ring.clear()
            await _log_suspended_event(db)
    else:
        _empty_ring.clear()

    # Conservative audience drop.
    kept, dropped = apply_audience_drop(raw_results, session_type, auth_state)
    if dropped:
        await _log_audience_drop_event(db, query, dropped)

    _last_call_log.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": 200, "elapsed_ms": elapsed_ms,
        "totalIndexed_seen": total_indexed,
        "results_count_raw": len(raw_results),
        "results_count_post_audience": len(kept),
        "locale": locale,
    })
    _bump_counters(audience_drops=dropped)
    await _log_telemetry(db, {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "query_hash": hashlib.sha1(query.encode("utf-8")).hexdigest()[:12],
        "top_k": top_k, "status": 200,
        "elapsed_ms": elapsed_ms, "totalIndexed_seen": total_indexed,
        "results_count_raw": len(raw_results),
        "results_count_post_audience": len(kept),
        "audience_drops": dropped, "locale": locale,
    })

    # Mint local-shaped hit rows so `_hits_to_chunks` + `_build_citations` can
    # consume deck hits unchanged. Fields the deck does NOT return stay None.
    out_hits: List[Dict[str, Any]] = []
    for h in kept:
        source = h.get("source") or "deck_fallback"
        title = h.get("title") or h.get("source") or "(deck result)"
        score = float(h.get("score") or 0.0)
        out_hits.append({
            "doc_id":   h.get("id") or f"deck::{title}",
            "doc_title": title,
            "section":  h.get("section") or "deck",
            "text":     h.get("content") or h.get("snippet") or "",
            "source":   "smifs_knowledge",
            "subsource": source,
            # Deck-only hits are intentionally scored just below local
            # threshold so a future hybrid merge ranks local hits first
            # while still surfacing deck candidates when local is empty.
            "score":    score,
            "raw_score": score,
            "audience": "all",
            "doc_type": source,
            # Phase 18 — flag the engine of origin so the citation chip can
            # render a subtle differentiator (FE consumes `source_engine`).
            "source_engine": "deck_search",
        })
    return out_hits


# Test helpers (exposed for unit tests)
def _reset_state() -> None:
    """Reset module-level singletons. Test-only."""
    global _suspended_until, _current_total_indexed
    _empty_ring.clear()
    _last_call_log.clear()
    _suspended_until = 0.0
    _current_total_indexed = None
    _call_counter_today["date"] = ""
    _call_counter_today["count"] = 0
    _call_counter_today["audience_drops"] = 0
