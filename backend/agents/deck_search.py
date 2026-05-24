"""Phase 18 / 18.1 — Deck Vector Engine fallback.

Lazy fall-through to the SMIFS deck's server-side semantic search endpoint.
Default OFF (`DECK_SEARCH_FALLBACK != "true"`) — the function short-circuits
to `[]` without making any HTTP call.

Design constraints (Phase 18 brief + Phase 18.1 safety asks):
  * Flag default-off so production has zero behavioural footprint until ops
    flips the env var.
  * **Hard 2.5s latency budget** (`DECK_SEARCH_TIMEOUT_S`) via
    `asyncio.wait_for`. On timeout we abort, log a `security_events` row of
    kind `deck_search_timeout`, and return `[]` — never blocking the user.
  * **Slow-response warning** (`DECK_SEARCH_SLOW_RESPONSE_MS`, default 2000).
    Successful calls over the threshold log `security_events` of kind
    `deck_search_slow_response` (severity: warning) for trend visibility.
  * Soft kill-switch: 10 consecutive `totalIndexed == 0` responses → suspend
    deck calls for `DECK_SEARCH_BACKOFF_SECONDS` (default 1h). Single
    `security_events` row logged per suspension; auto-resume after window.
  * **Sources whitelist pre-filter**: non-employee sessions send
    `sources=["bedrock","vehicle","academy","sales_pitch","document"]` (omit
    `growth_*`). Verified employees see everything.
  * **Local join-back enrichment**: every deck hit is joined to
    `doc_chunks.smifs_id` to pull the 16 projected fields (audience,
    vehicle_id, vehicle_name, version_no, is_focused, is_active,
    updated_at_iso, subsource, doc_type, provider, language, …). This
    enables vehicle CTA + version badge + recency chip on deck citations.
  * **Belt-and-suspenders audience gate**: post-enrichment, drop deck hits
    whose enriched `audience == "employee_only"` OR (fallback) whose deck
    `source ∈ {sales_pitch, growth_*}` — for non-employee sessions only.
  * Per-call telemetry into `deck_search_calls` (capped @ 50k).
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

DECK_BASE = (os.environ.get("SMIFS_KNOWLEDGE_BASE_URL") or "").rstrip("/")
DECK_KEY = os.environ.get("SMIFS_KNOWLEDGE_API_KEY") or ""

# Env-driven knobs (read once at import; min_score is re-read per call so a
# threshold tune doesn't require a restart).
DEFAULT_BACKOFF = int(os.environ.get("DECK_SEARCH_BACKOFF_SECONDS", "3600"))
# Phase 18.2 — bumped 2.5s → 3.0s. p95 was 3.01s on 24-May post-bulk-load;
# the previous budget was timing out ~50% of otherwise-successful calls
# (see /app/deliverables/phase18c/deck_reprobe_delta.md §6). The slow-
# response WARNING threshold stays at 2.0s so trend visibility isn't lost.
DECK_TIMEOUT_S = float(os.environ.get("DECK_SEARCH_TIMEOUT_S", "3.0"))
SLOW_RESPONSE_MS = int(os.environ.get("DECK_SEARCH_SLOW_RESPONSE_MS", "2000"))
EMPTY_RING_MAX = 10
CAPPED_CALLS = 50_000
LATENCY_RING_MAX = 50

# Phase 16 — employee-only subsources. Same canonical list used by the local
# retrieval audience gate; mirrored here so the deck fallback applies the
# same gate before any deck hit reaches the LLM context.
_EMPLOYEE_ONLY_SUBSOURCES = {"sales_pitch", "growth_insurance", "growth_revenue"}

# Phase 18.1 — sources whitelist pre-filter. Saves a round-trip on chunks
# we'd just drop in audience gating.
_VISITOR_CLIENT_SOURCES = ["bedrock", "vehicle", "academy", "sales_pitch", "document"]

# Phase 18.2 — `documents_full` is a NEW deck-only source (added between 18b
# and 18c probes). 72% of deck hits, 0% join into our local doc_chunks, no
# audience metadata. Until the deck team confirms the corpus is universally
# safe-for-all (see DEPLOY_NOTES.md "documents_full relaxation criteria"),
# we hard-block this source for visitor / client sessions. Verified employees
# see them unchanged (they're cleared for broader content).
_DOCUMENTS_FULL_SOURCE = "documents_full"


def _current_min_score() -> float:
    """Read DECK_SEARCH_MIN_SCORE fresh on every call so an ops tune takes
    effect without a restart. Default bumped from 0.30 → 0.45 (Phase 18b
    histogram justifies it — see `/app/deliverables/phase18b/score_histogram.md`)."""
    try:
        return float(os.environ.get("DECK_SEARCH_MIN_SCORE", "0.45"))
    except (TypeError, ValueError):
        return 0.45


def enabled() -> bool:
    """Read fresh on every call so an ops flag flip takes effect without a restart."""
    return (os.environ.get("DECK_SEARCH_FALLBACK", "").strip().lower()
            in ("1", "true", "yes", "on"))


# ---------- soft kill-switch + telemetry ring state ----------
_empty_ring: collections.deque = collections.deque(maxlen=EMPTY_RING_MAX)
_suspended_until: float = 0.0   # monotonic clock
_last_call_log: collections.deque = collections.deque(maxlen=EMPTY_RING_MAX)
_latency_ring: collections.deque = collections.deque(maxlen=LATENCY_RING_MAX)
_call_counter_today = {
    "date": "", "count": 0, "audience_drops": 0,
    "timeouts": 0, "slow_responses": 0,
    "documents_full_blocks": 0,    # Phase 18.2
}
_current_total_indexed: Optional[int] = None


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _bump_counters(*, audience_drops: int = 0,
                   timeout: bool = False, slow: bool = False,
                   documents_full_blocks: int = 0) -> None:
    today = _today_iso()
    if _call_counter_today["date"] != today:
        _call_counter_today["date"] = today
        _call_counter_today["count"] = 0
        _call_counter_today["audience_drops"] = 0
        _call_counter_today["timeouts"] = 0
        _call_counter_today["slow_responses"] = 0
        _call_counter_today["documents_full_blocks"] = 0
    _call_counter_today["count"] += 1
    _call_counter_today["audience_drops"] += audience_drops
    _call_counter_today["documents_full_blocks"] += documents_full_blocks
    if timeout:
        _call_counter_today["timeouts"] += 1
    if slow:
        _call_counter_today["slow_responses"] += 1


def _p50_latency_ms_last_50() -> Optional[int]:
    if not _latency_ring:
        return None
    sorted_lat = sorted(_latency_ring)
    return int(sorted_lat[len(sorted_lat) // 2])


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
        "timeouts_today": _call_counter_today["timeouts"],
        "slow_responses_today": _call_counter_today["slow_responses"],
        "documents_full_blocks_today": _call_counter_today["documents_full_blocks"],
        "p50_latency_ms_last_50": _p50_latency_ms_last_50(),
        "current_totalIndexed_seen": _current_total_indexed,
        "backoff_seconds": DEFAULT_BACKOFF,
        "min_score": _current_min_score(),
        "timeout_s": DECK_TIMEOUT_S,
        "slow_response_ms": SLOW_RESPONSE_MS,
    }


def _sources_for(session_type: Optional[str], auth_state: Optional[str]) -> Optional[List[str]]:
    """Phase 18.1 pre-filter. Verified employees see everything; everyone else
    gets the conservative whitelist so we don't even pay the bandwidth for
    chunks we'd just drop in audience gating."""
    if session_type == "employee" and auth_state == "verified":
        return None
    return list(_VISITOR_CLIENT_SOURCES)


def apply_audience_drop(results: List[Dict[str, Any]], session_type: Optional[str],
                        auth_state: Optional[str]) -> tuple[List[Dict[str, Any]], int]:
    """Pure function — testable in isolation.

    Drops any hit whose `source` lands in `_EMPLOYEE_ONLY_SUBSOURCES` when the
    requester is NOT a verified employee. This is the *belt* part of the
    belt-and-suspenders gate — Phase 18.1 also enforces the same gate on the
    enriched `audience` field returned by the local join-back (the
    *suspenders*).
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


# ----- Phase 18.1: local join-back enrichment fields -----
_ENRICHMENT_FIELDS = (
    "audience", "vehicle_id", "vehicle_name", "vehicle_type",
    "version_no", "version_major", "is_focused", "is_active",
    "updated_at_iso", "source_updated_at", "subsource", "doc_type",
    "provider", "language", "sales_pitch_ready", "category", "vertical",
)


async def _enrich_with_local(db, raw_hits: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Look up each deck hit's `id` in `doc_chunks.smifs_id` and return a
    `{smifs_id: enrichment_dict}` map. Empty map on any DB error (the caller
    falls back to source-name-only audience gating)."""
    if db is None or not raw_hits:
        return {}
    ids = [h.get("id") for h in raw_hits if h.get("id")]
    if not ids:
        return {}
    proj = {f: 1 for f in _ENRICHMENT_FIELDS}
    proj["smifs_id"] = 1
    proj["_id"] = 0
    out: Dict[str, Dict[str, Any]] = {}
    try:
        cur = db.doc_chunks.find({"smifs_id": {"$in": ids}}, proj)
        async for row in cur:
            sid = row.get("smifs_id")
            if sid:
                out[sid] = {k: row.get(k) for k in _ENRICHMENT_FIELDS if row.get(k) is not None}
    except Exception:
        logger.exception("deck_search local enrichment query failed (non-fatal)")
    return out


def _belt_and_suspenders_audience_drop(enriched_hits: List[Dict[str, Any]],
                                       session_type: Optional[str],
                                       auth_state: Optional[str]) -> tuple[List[Dict[str, Any]], int]:
    """Phase 18.1 — drop hits whose ENRICHED `audience == "employee_only"` for
    non-employee sessions. If the enrichment lookup found nothing (hit not in
    local DB), we keep the source-name check from `apply_audience_drop()` as
    the belt — i.e. we WILL NOT serve any `sales_pitch`/`growth_*` source
    name to a non-employee even if the enriched audience is missing.
    """
    is_employee_verified = (session_type == "employee" and auth_state == "verified")
    if is_employee_verified:
        return list(enriched_hits), 0
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for h in enriched_hits:
        # Suspenders: enriched audience signal.
        if (h.get("audience") or "").strip() == "employee_only":
            dropped += 1
            continue
        # Belt: source-name fallback (covers enrichment-miss).
        if (h.get("subsource") or h.get("source_raw") or "").strip() in _EMPLOYEE_ONLY_SUBSOURCES:
            dropped += 1
            continue
        kept.append(h)
    return kept, dropped


async def _log_telemetry(db, row: Dict[str, Any]) -> None:
    if db is None:
        return
    try:
        await db.deck_search_calls.insert_one(row)
        n = await db.deck_search_calls.estimated_document_count()
        if n > CAPPED_CALLS:
            old = db.deck_search_calls.find({}, {"_id": 1}).sort("created_at", 1).limit(500)
            ids = [d["_id"] async for d in old]
            if ids:
                await db.deck_search_calls.delete_many({"_id": {"$in": ids}})
    except Exception:
        logger.exception("deck_search telemetry insert failed (non-fatal)")


async def _log_security_event(db, kind: str, payload: Dict[str, Any], severity: str = "info") -> None:
    if db is None:
        return
    try:
        await db.security_events.insert_one({
            "kind": kind,
            "severity": severity,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **payload,
        })
    except Exception:
        logger.exception("deck_search %s event insert failed (non-fatal)", kind)


async def _log_audience_drop_event(db, query: str, dropped: int) -> None:
    if db is None or dropped == 0:
        return
    await _log_security_event(db, kind="kb_audience_dropped_deck_hit", payload={
        "query_hash": hashlib.sha1(query.encode("utf-8")).hexdigest()[:12],
        "dropped_count": dropped,
    })


async def _log_suspended_event(db) -> None:
    await _log_security_event(db, kind="deck_search_suspended_empty_index", payload={
        "consecutive_empty_responses": EMPTY_RING_MAX,
        "backoff_seconds": DEFAULT_BACKOFF,
    })


async def _post_deck(client: httpx.AsyncClient, body: Dict[str, Any]) -> httpx.Response:
    """Inner HTTP coroutine — wrapped in `asyncio.wait_for` by the caller."""
    return await client.post(
        f"{DECK_BASE}/api/knowledge/search",
        headers={"X-API-Key": DECK_KEY, "Content-Type": "application/json"},
        json=body,
    )


async def deck_search(query: str, *, top_k: int = 8, db=None,
                      session_type: Optional[str] = None,
                      auth_state: Optional[str] = None,
                      locale: str = "en") -> List[Dict[str, Any]]:
    """Phase 18 / 18.1 fallback retrieval.

    Returns a list of hit dicts shaped to match `rag.search_weighted` rows so
    `rag_agent._hits_to_chunks` and `_build_citations` can consume them
    unchanged. Returns `[]` (no HTTP) when the flag is OFF, the soft
    kill-switch has suspended calls, or the 2.5s latency budget is exceeded.
    """
    global _suspended_until, _current_total_indexed
    if not enabled():
        return []
    if not (DECK_BASE and DECK_KEY):
        return []
    now = time.monotonic()
    if now < _suspended_until:
        return []

    started = time.monotonic()
    min_score = _current_min_score()
    body: Dict[str, Any] = {"q": query, "top_k": top_k, "min_score": min_score}
    sources_filter = _sources_for(session_type, auth_state)
    if sources_filter is not None:
        body["sources"] = sources_filter

    query_hash = hashlib.sha1(query.encode("utf-8")).hexdigest()[:12]
    base_row = {"created_at": datetime.now(timezone.utc).isoformat(),
                "query_hash": query_hash, "top_k": top_k, "locale": locale,
                "sources_filter": sources_filter}

    # --- Phase 18.1: hard latency budget (Phase 18.2: 2.5s → 3.0s). NEVER block the user. ---
    try:
        # httpx's own 30s timeout is the inner guard; asyncio.wait_for is the
        # hard outer budget enforced even if httpx hangs on connect/TLS.
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await asyncio.wait_for(_post_deck(client, body), timeout=DECK_TIMEOUT_S)
        elapsed_ms = int((time.monotonic() - started) * 1000)
    except asyncio.TimeoutError:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _last_call_log.append({"ts": datetime.now(timezone.utc).isoformat(),
                                "status": "timeout", "elapsed_ms": elapsed_ms})
        _latency_ring.append(elapsed_ms)
        _bump_counters(timeout=True)
        await _log_security_event(db, kind="deck_search_timeout", payload={
            "query_hash": query_hash, "top_k": top_k, "elapsed_ms": elapsed_ms,
            "timeout_s": DECK_TIMEOUT_S,
        })
        await _log_telemetry(db, {**base_row, "status": "timeout", "elapsed_ms": elapsed_ms})
        return []
    except Exception as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _last_call_log.append({"ts": datetime.now(timezone.utc).isoformat(),
                                "status": "exc", "exc": f"{type(e).__name__}",
                                "elapsed_ms": elapsed_ms})
        _latency_ring.append(elapsed_ms)
        await _log_telemetry(db, {**base_row, "status": "exc",
                                   "exc": f"{type(e).__name__}", "elapsed_ms": elapsed_ms})
        return []

    _latency_ring.append(elapsed_ms)
    if r.status_code != 200:
        _last_call_log.append({"ts": datetime.now(timezone.utc).isoformat(),
                                "status": r.status_code, "elapsed_ms": elapsed_ms,
                                "results_count_raw": 0})
        await _log_telemetry(db, {**base_row, "status": r.status_code,
                                   "elapsed_ms": elapsed_ms})
        return []
    payload = r.json() or {}

    # --- slow-response warning (successful 200 over the budget) ---
    slow = elapsed_ms >= SLOW_RESPONSE_MS
    if slow:
        await _log_security_event(db, kind="deck_search_slow_response", severity="warning",
                                  payload={"query_hash": query_hash, "top_k": top_k,
                                           "elapsed_ms": elapsed_ms,
                                           "slow_threshold_ms": SLOW_RESPONSE_MS})

    total_indexed = int(payload.get("totalIndexed") or 0)
    _current_total_indexed = total_indexed
    raw_results = payload.get("results") or []

    # Soft kill-switch.
    if total_indexed == 0:
        _empty_ring.append(time.monotonic())
        if len(_empty_ring) == EMPTY_RING_MAX:
            _suspended_until = time.monotonic() + DEFAULT_BACKOFF
            _empty_ring.clear()
            await _log_suspended_event(db)
    else:
        _empty_ring.clear()

    # --- Phase 18.1: local join-back enrichment ---
    enrichment_map = await _enrich_with_local(db, raw_results)

    # Mint local-shaped hit rows. The score-floor enforced by `min_score` is
    # already applied server-side by the deck (per param-honour matrix).
    out_hits: List[Dict[str, Any]] = []
    for h in raw_results:
        deck_source = (h.get("source") or "deck_fallback").strip()
        title = h.get("title") or h.get("source") or "(deck result)"
        score = float(h.get("score") or 0.0)
        sid = h.get("id") or f"deck::{title}"
        enriched = enrichment_map.get(sid) or {}
        # Per-hit shape — same as rag.search_weighted rows.
        out_hits.append({
            "doc_id":   sid,
            "doc_title": title,
            "section":  h.get("section") or "deck",
            "text":     h.get("content") or h.get("snippet") or "",
            "source":   "smifs_knowledge",
            # Prefer enriched subsource (canonical) but fall back to the deck's
            # `source` field so audience gating still has SOMETHING to check
            # when the join misses (e.g. brand-new deck chunk not yet sync'd
            # to local Mongo).
            "subsource":     enriched.get("subsource") or deck_source,
            "source_raw":    deck_source,
            "score":         score,
            "raw_score":     score,
            # Phase 18.1 — explicit relevance for FE/debug surfaces.
            "relevance":     score,
            "audience":      enriched.get("audience") or "all",
            "doc_type":      enriched.get("doc_type") or deck_source,
            "vehicle_id":    enriched.get("vehicle_id"),
            "vehicle_name":  enriched.get("vehicle_name"),
            "vehicle_type":  enriched.get("vehicle_type"),
            "version_no":    enriched.get("version_no"),
            "version_major": enriched.get("version_major"),
            "is_focused":    enriched.get("is_focused"),
            "is_active":     enriched.get("is_active"),
            "updated_at_iso": enriched.get("updated_at_iso") or enriched.get("source_updated_at"),
            "provider":      enriched.get("provider"),
            "language":      enriched.get("language"),
            # Phase 18 — flag the engine of origin so the citation chip can
            # render a subtle differentiator (FE consumes `source_engine`).
            "source_engine": "deck_search",
            # Phase 18.2 — FE renders `documents_full` hits with a muted-grey
            # accent + tooltip so reps know it's a broad PDF text dump rather
            # than a curated bedrock/vehicle chunk. Only set true for the
            # specific deck source.
            "is_full_document_scan": (deck_source == _DOCUMENTS_FULL_SOURCE),
        })

    # --- belt-and-suspenders audience gate post-enrichment ---
    kept, dropped = _belt_and_suspenders_audience_drop(out_hits, session_type, auth_state)
    if dropped:
        await _log_audience_drop_event(db, query, dropped)

    # --- Phase 18.2: defensive `documents_full` guard for non-employees ---
    # `documents_full` is a deck-only source (added between 18b and 18c
    # probes). 72% of deck hits but 0% join into doc_chunks, so we have NO
    # local audience metadata to verify what's in these chunks. Until the
    # deck team confirms the corpus is universally safe-for-all, block this
    # source for visitor/client sessions. Verified employees see them
    # unchanged. See DEPLOY_NOTES.md "documents_full relaxation criteria".
    documents_full_blocks = 0
    is_employee_verified = (session_type == "employee" and auth_state == "verified")
    if not is_employee_verified:
        kept_after_full_guard: List[Dict[str, Any]] = []
        for h in kept:
            if h.get("source_raw") == _DOCUMENTS_FULL_SOURCE:
                documents_full_blocks += 1
                await _log_security_event(
                    db, kind="kb_documents_full_blocked_for_role",
                    severity="info",
                    payload={
                        "session_type": session_type or "anonymous",
                        "auth_state": auth_state or "anonymous",
                        "hit_title_redacted": (h.get("doc_title") or "")[:40],
                        "query_hash": query_hash,
                    },
                )
                continue
            kept_after_full_guard.append(h)
        kept = kept_after_full_guard

    _last_call_log.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": 200, "elapsed_ms": elapsed_ms, "slow": slow,
        "totalIndexed_seen": total_indexed,
        "results_count_raw": len(raw_results),
        "results_count_post_audience": len(kept),
        "documents_full_blocks": documents_full_blocks,
        "enrichment_hits": len(enrichment_map),
        "locale": locale,
    })
    _bump_counters(audience_drops=dropped, slow=slow,
                   documents_full_blocks=documents_full_blocks)
    await _log_telemetry(db, {
        **base_row,
        "status": 200, "elapsed_ms": elapsed_ms, "slow": slow,
        "totalIndexed_seen": total_indexed,
        "results_count_raw": len(raw_results),
        "results_count_post_audience": len(kept),
        "audience_drops": dropped,
        "documents_full_blocks": documents_full_blocks,
        "enrichment_hits": len(enrichment_map),
    })

    return kept


# Test helpers (exposed for unit tests)
def _reset_state() -> None:
    """Reset module-level singletons. Test-only."""
    global _suspended_until, _current_total_indexed
    _empty_ring.clear()
    _last_call_log.clear()
    _latency_ring.clear()
    _suspended_until = 0.0
    _current_total_indexed = None
    _call_counter_today["date"] = ""
    _call_counter_today["count"] = 0
    _call_counter_today["audience_drops"] = 0
    _call_counter_today["timeouts"] = 0
    _call_counter_today["slow_responses"] = 0
    _call_counter_today["documents_full_blocks"] = 0
