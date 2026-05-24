# Phase 18 — Deploy Notes (SMIFS Knowledge Bot)

## What changed

### Workstream A — Deck Vector Engine Fallback
* New module `backend/agents/deck_search.py`. **Default OFF**:
  `DECK_SEARCH_FALLBACK=false` short-circuits to `[]` with **zero** HTTP
  traffic to `deck.pesmifs.com`. Flag is read fresh on every call so an
  ops flag flip takes effect without a restart.
* Soft kill-switch: 10 consecutive `totalIndexed==0` responses suspend
  deck calls for `DECK_SEARCH_BACKOFF_SECONDS` (default 1h).
  Suspension logged once to `security_events`.
* Strict audience gate mirrors the local retrieval audience gate —
  any deck hit whose `source ∈ {sales_pitch, growth_insurance,
  growth_revenue}` is dropped for non-employee sessions. Drops are
  logged to `security_events`.
* Per-call telemetry in `deck_search_calls` (auto-prunes at 50k).
* Admin read surface: `GET /api/admin/deck_search/status`
  (X-Admin-Token required) returns flag state, suspension window,
  last 10 in-memory call rows + most recent telemetry slice.
* Local cosine retrieval (`rag_agent._retrieve`) is unchanged; the deck
  fallback only fires when `not grounded` (no local hit ≥ 0.30). Deck
  hits are flagged with `source_engine: "deck_search"` on the citation
  payload (FE consumes this field but renders identically for now).

### Workstream B — Multilingual UX
* New endpoint `POST /api/agent/locale` body
  `{session_id, locale: "en"|"hi"|"ta"}` — persists to `sessions`.
* `GET /api/sessions/{id}` now returns `locale`.
* Orchestrator + RAG agent append a strict locale instruction to the
  LLM system prompt for hi/ta:
  > "Respond entirely in Hindi/Tamil. Use Devanagari script for
  >  Hindi, Tamil script for Tamil. Keep technical terms (PAN, UCC,
  >  NAV, AUM, ARN, SIP, NCD) in English where they are proper nouns."
* Forms + structured data stay in English by design — only chat prose
  is localised.
* New FE component `LocaleChoiceBlock.jsx`:
  - Inline block (`type: locale_choice`) rendered automatically after
    the role pick.
  - Header globe toggle for mid-session switching.
  - Locale is persisted in `localStorage` and pushed to the backend
    on every change.

## Required env

```env
# Already present
SMIFS_KNOWLEDGE_BASE_URL=https://deck.pesmifs.com
SMIFS_KNOWLEDGE_API_KEY=…

# Phase 18.1 — flag now ON after Phase 18b probe + safety asks.
DECK_SEARCH_FALLBACK=true
DECK_SEARCH_MIN_SCORE=0.45          # was 0.30 — histogram-justified (Phase 18b)
DECK_SEARCH_BACKOFF_SECONDS=3600
DECK_SEARCH_TIMEOUT_S=2.5           # hard latency budget (Phase 18.1 safety ask A)
DECK_SEARCH_SLOW_RESPONSE_MS=2000   # successful-but-slow warning threshold
```

## Phase 18.1 — added safety behaviours

1. **Hard 2.5s latency budget** via `asyncio.wait_for` — any deck call
   exceeding the budget aborts cleanly, logs `security_events.kind =
   deck_search_timeout`, and returns `[]`. The user response NEVER waits
   on the deck.
2. **Slow-response warning** — successful 200 responses over
   `DECK_SEARCH_SLOW_RESPONSE_MS` emit a `kind=deck_search_slow_response`
   (severity: warning) event for trend visibility. The call still
   returns its hits.
3. **Local-threshold guard** — `rag_agent._retrieve` falls through to
   the deck only when local has NO hit ≥ 0.10 (LOCAL_FLOOR). Any
   semi-relevant local candidate suppresses the deck call. This is the
   `academy`/`document` regression guard (the deck does not index those
   subsources, so a sub-threshold local hit is strictly better than a
   deck round-trip).
   * **Note on floor**: the user's brief said "any hit in [0.20, 0.30]",
     but our actual `RAG_MIN_SCORE` is 0.15 (not 0.30). LOCAL_FLOOR is
     set to 0.10 so the band `[0.10, 0.15)` of borderline local hits
     suppresses fallback. This preserves the user's intent.
4. **Local join-back enrichment** — every deck hit is looked up in
   `doc_chunks.smifs_id` (100% match rate per Phase 18b probe) to pull
   `audience, vehicle_id, vehicle_name, vehicle_type, version_no,
   is_focused, is_active, updated_at_iso, subsource, doc_type, provider,
   language`. Deck citations now carry the same projected metadata as
   local citations — vehicle CTA + version badge + recency chip surfaces
   work uniformly.
5. **Belt-and-suspenders audience gate** — for non-employee sessions,
   drop any hit whose enriched `audience == "employee_only"` OR (fallback)
   whose deck `source` is in `{sales_pitch, growth_insurance, growth_revenue}`.
   The source-name fallback catches enrichment misses for brand-new deck
   chunks not yet sync'd to local Mongo.
6. **Sources whitelist pre-filter** — non-employee sessions send
   `sources=["bedrock","vehicle","academy","sales_pitch","document"]`
   (omits `growth_*`). Saves a round-trip on chunks we'd drop anyway.
   Verified employees see everything (no filter).
7. **Citation contract** — every citation row now carries
   `source_engine: "local_cosine" | "deck_search"` and `relevance: float`
   for FE/admin debug surfaces.
8. **Admin panel** — `GET /api/admin/deck_search/status` surfaces
   `enabled, suspended, total_calls_today, timeouts_today,
   slow_responses_today, audience_drops_today, p50_latency_ms_last_50,
   current_totalIndexed_seen, recent_telemetry[]`. Knowledge Base tab in
   the admin console renders the panel.

## Activation steps

1. Verify telemetry: `GET /api/admin/deck_search/status` → expect
   `enabled: true, min_score: 0.45, timeout_s: 2.5`.
2. Smoke test (in-process probe):
   `python3 /app/deliverables/phase18_1/live_probe.py`
3. Open the Knowledge Base tab in the admin console; confirm the deck
   panel renders with non-stale counters.
4. Monitor `timeouts_today` daily for one week. If sustained > 5% of
   `total_calls_today`, lower `DECK_SEARCH_TIMEOUT_S` further or open
   ticket with deck-engine ops.

## Rollback

Set `DECK_SEARCH_FALLBACK=false`. No code rollback needed.

## Tests added

* `backend/tests/test_phase18_deck_search.py` (8 tests) — Phase 18
* `backend/tests/test_phase18_locale.py` (9 tests) — Phase 18
* `backend/tests/test_phase18_1_deck_safety.py` (8 tests) — Phase 18.1

Run: `cd /app/backend && python -m pytest tests/test_phase18_*.py -v`
Total: 25 tests, all passing.
