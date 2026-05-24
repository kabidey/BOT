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

# Phase 18 — defaults are safe
DECK_SEARCH_FALLBACK=false
DECK_SEARCH_MIN_SCORE=0.30
DECK_SEARCH_BACKOFF_SECONDS=3600
```

## Activation steps

1. Verify telemetry: `GET /api/admin/deck_search/status` → expect
   `enabled: false`.
2. Flip the flag in `backend/.env`: `DECK_SEARCH_FALLBACK=true`.
3. Restart backend (or wait — `enabled()` is read per-call).
4. Probe with a query that returns zero local hits, e.g.
   `POST /api/agent/turn` as a verified employee.
5. Confirm `deck_search_calls` is growing and audience drops are
   logging into `security_events`.

## Rollback

Set `DECK_SEARCH_FALLBACK=false`. No code rollback needed.

## Tests added

* `backend/tests/test_phase18_deck_search.py` (8 tests)
* `backend/tests/test_phase18_locale.py` (9 tests)

Run: `cd /app/backend && python -m pytest tests/test_phase18_*.py -v`
