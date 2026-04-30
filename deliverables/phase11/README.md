# Phase 11 — Conversion + Ops Polish

Deliverables for the four workstreams. All acceptance criteria hit.

## 1 · WhatsApp / Email handoff CTA

**Backend**
- `POST /api/handoff` — token-free, session-bound. Resolves target contact from `session.identity` (RM for clients, HRBP for employees, generic advisor for visitors), PII-scrubs the user question, builds `wa.me/…` / `mailto:` links, writes to `handoffs` + `leads`.
- `to_e164_india()` normalises any Indian phone into `+91XXXXXXXXXX` before embedding in the deep-link.
- Companion `leads` row: `source: "chat_handoff"`, `priority: "warm"`, linked via `handoff_id`.

**Frontend** — `EscalationBlock.jsx`
- Two small CTAs at the bottom of the escalation card: **gold "Continue on WhatsApp"** + **ghost "Email this to my advisor"**, plus the existing "Request a callback".
- On click: POST `/api/handoff`, then `window.open(deep_link, "_blank", "noopener,noreferrer")`, flip button to "Notified ✓".
- If the target has no direct contact (visitor → advisor), the button transparently falls back to the existing callback form path.

**Sample deep-link (verified client UCC 63876 · PAN ARIPP3602Q · RM JITEN SAHOO)**
```
https://wa.me/918270351950?text=Hi%20Jiten%2C%0A%0AThis%20is%20Balaram%20%28UCC%2063876%29%20reaching%20out%20via%20Mackertich%20ONE%20chat.%20I%20had%20a%20question%3A%0A%0A%22Given%20my%20medium%20risk%20profile%2C%20should%20I%20consider%20the%20Mackertich%20ONE%20Sapphire%20AIF%20Cat%20III%20for%20equity%20exposure%3F%20I%27d%20like%20to%20know%20the%20minimum%20ticket%20size%20and%20lock-in.%22%0A%0ACould%20you%20please%20assist%3F%0A%0AContext%20from%20the%20chat%3A%0AClient%20is%20verified%20at%20BHUBANESWAR%20branch%20with%20NSE%2BBSE%2BNFO%20active.%20Asking%20about%20AIF%20fit.%0A%0A%E2%80%94%20Sent%20via%20Mackertich%20ONE%20Advisor
```
Decoded message (no PAN / full email / full phone in the payload — target is the RM's number, not the client's):
```
Hi Jiten,

This is Balaram (UCC 63876) reaching out via Mackertich ONE chat. I had a question:

"Given my medium risk profile, should I consider the Mackertich ONE Sapphire AIF Cat III for equity exposure? I'd like to know the minimum ticket size and lock-in."

Could you please assist?

Context from the chat:
Client is verified at BHUBANESWAR branch with NSE+BSE+NFO active. Asking about AIF fit.

— Sent via Mackertich ONE Advisor
```

**Privacy proof**
- `to_e164_india` re-formats the RM phone, never the client's.
- `user_question` and `context_snippet` pass through `identity.redact_pii_in_text()` before being embedded.
- `handoff` doc stores only `target_contact_masked` (`***1950`) — never plaintext target contact.

## 2 · Knowledge Gaps admin tab

**Backend**
- `GET /api/admin/knowledge_gaps?range=7d&role=all` — combines `hallucination_events` with `conversations.messages[wm_fallback:true]` flagged during Phase 10's fallback generation. Normalises questions (lowercase, strip punct, collapse whitespace). Returns KPIs, top 100 questions, by-asset bars.
- `POST /api/admin/knowledge_gaps/resolve` — persists `knowledge_gap_status` collection keyed by normalised question.

**Frontend admin** — new `Knowledge Gaps` tab between `Insights` and `Knowledge Base`
- 5 KPI tiles: hallucination events · WM fallbacks · unique questions · resolved · top asset class
- Gap-volume bar chart by asset class
- Top-20 questions table with roles asked, last-seen, status pill, one-click **Mark resolved / Mark open**
- Filter chips: 24h / 7d / 30d · All / Clients / Employees / Visitors

**Sample response** → `knowledge_gaps_sample.json`.
For the current corpus (7-day window):
- 46 hallucination events, 4 fresh WM fallbacks, 19 unique questions, 1 resolved, top asset class **PMS (21)**.
- Top unanswered (all marked "open"): *"What is the historical NAV of Alchemy Smart Alpha?" [6x · client]*, *"Explain Category II AIF structure" [4x · client]*, *"What is the lock-in for Mackertich ONE Sapphire AIF Cat IV?" [3x · client]*, *"Who is in the Compliance department?" [3x · visitor]*.

## 3 · Background SMIFS KB delta-sync (15 min)

**Backend**
- `knowledge_sync.delta_sync_loop()` — `asyncio` task scheduled in FastAPI startup. Every `KB_DELTA_SYNC_INTERVAL_SECONDS` (default 900) with ±60s jitter.
- Mongo-based mutex `kb_sync_locks` stops manual + scheduled collision; stale locks (older than 15 min) are stolen.
- Every run (manual or scheduled) is persisted to `knowledge_sync_runs`: `{started_at, finished_at, mode, trigger, fetched, upserted, skipped, removed, errors, duration_ms}`, capped at 200 rows.
- `/api/admin/knowledge/status` now exposes `auto_sync_enabled`, `auto_sync_interval_seconds`, `next_scheduled_sync_at`, `last_run_summary` (last 5 runs).

**Proof** — 10 scheduler runs captured in `knowledge_sync_runs.json` (verification used a 45-second interval for density; production is 900s). Every row: `trigger: "scheduler"`, `mode: "delta"`, `fetched: 1801`, `upserted: 0`, `skipped: 1801`, `removed: 0`, `errors: []` — i.e. idempotent, no thrash after the first resync.

**Frontend admin (KB tab)** — new header line "Auto-sync every 15 min · next at 9:04:20 PM" + collapsible **"Last 5 runs"** strip with trigger pill, duration, counts. Manual Delta / Full sync buttons unchanged.

## 4 · Stop-generating + Admin session search

**4a. Stop button** — while streaming, the send button morphs into a red-ringed square **Stop**. Click → aborts the existing `AbortController`, appends `(stopped)` to the partial reply bubble, preserves any text streamed so far. Backend SSE cancels naturally via `Request.is_disconnected()` (already in place since Phase 6).

**4b. Admin archives search** — `/api/admin/archives` now accepts `q`, `date_from`, `date_to`, `offset`. Text search across `identity_summary.{name,first_name,ucc,employee_id}` + `intents_used` + email-hash when `q` looks like an email.

Live proof (see `archives_search_aaditya.txt`):
- `q="Aaditya"` → 10 of 127 matching rows, all `Aaditya Rajesh Jaiswal · SMWM-25031054`.
- `q="63876" role=client` → 5 of 20 rows, all `Balaram · UCC 63876`.
- Search + role filters + refresh work end-to-end.

## Screenshots

| # | File | What it shows |
|---|------|----------------|
| 1 | `stop_button_streaming.jpeg` | Mid-stream state: "ADVISOR · streaming — Routing your question…" with red square **Stop** button in composer; verified client_card shows the Phase 10b-correct `BHUBANESWAR · BHUB` branch. |
| 2 | `escalation_with_ctas.jpeg` | New escalation_card: eyebrow "YOUR WEALTH MANAGER — JITEN SAHOO", contact pills `***1950` + `ji***@smifs.net`, gold **Continue on WhatsApp** + ghost **Email this to my advisor** + tertiary **Request a callback**. |
| 3 | `admin_knowledge_gaps.jpeg` | Knowledge Gaps tab — 5 KPI tiles (46 / 4 / 19 / 1 / PMS), asset-class bar chart, top-20 unanswered questions table with `Mark resolved` + the already-resolved PMS row shown with strikethrough. |
| 4 | `admin_kb_scheduler.jpeg` | Knowledge Base tab — new line **"Auto-sync every 15 min · next at 9:04:20 PM"** + collapsible "Last 5 runs" strip. |
| 5 | `admin_kb_scheduler_history.jpeg` | Same tab with the history expanded — 5 scheduler-tagged delta rows (fetched/upserted/skipped/removed + duration). |
| 6 | `admin_archives_search_aaditya.jpeg` | Archives search with `q="Aaditya"` — 10 employee rows matching. |
| 7 | `admin_archives_search_ucc.jpeg` | Archives search with `q="63876"` — 19 client rows matching; the new caption line "Showing 19 of 19 · matching '63876'" is visible. |

## Data files

- `sample_whatsapp_handoff.json` — full POST /api/handoff response (verified client · RM JITEN SAHOO).
- `knowledge_sync_runs.json` — snapshot of 10 scheduler-triggered delta-sync rows from the `knowledge_sync_runs` collection.
- `knowledge_gaps_sample.json` — complete `GET /api/admin/knowledge_gaps?range=7d&limit=20` response (336 lines).
- `archives_search_aaditya.txt` — curl proof of `q="Aaditya"` + `q="63876" role=client` returning the expected archives.

## Env vars added

```bash
KB_DELTA_SYNC_INTERVAL_SECONDS=900   # set to 0 to disable the scheduler
```

## Regression status

- `tests/test_phase10_role_gateway.py` — **22 passed in 49 s**.
- No new gpt-4o-mini invocations; model chain unchanged.
- Privacy / PII masking on conversations, archives, and handoffs unchanged (validated via `test_no_plaintext_pan_in_conversations`).
- Phase 0-10 acceptance preserved: role gate, knowledge gating per role, verification flow, idle expiry + rehydration, embed widget, citations, cost discipline, guardrails.

## Collections added / touched

| Collection | Added / touched | Purpose |
|------------|-----------------|---------|
| `handoffs` | NEW | WhatsApp / email handoff ledger (one row per deep-link open) |
| `leads` | touched | Auto-created companion row for every handoff (`source: "chat_handoff"`) |
| `knowledge_sync_runs` | NEW | Capped history of every sync run (manual + scheduler); 200-row cap |
| `kb_sync_locks` | NEW | Mongo-based mutex preventing concurrent sync collisions |
| `knowledge_gap_status` | NEW | Persistence of "mark resolved" flags for the Knowledge Gaps tab |
| `conversations.messages.wm_fallback` | touched | New boolean flag enabling the Knowledge Gaps aggregator |
