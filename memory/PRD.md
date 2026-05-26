# SMIFS · Mackertich ONE Lead Wealth-Engagement Agent — PRD

## Original problem (Phase 6)
Replace the mock SMIFS001/year-of-birth/city verification with real OrgLens-backed
identity for three session types (Employee / Client / Visitor). Add PAN privacy,
session archive collection with consent-gated RAG ingestion, and a state-machine
auth flow.

## Architecture (current)
- FastAPI + React + MongoDB (Motor)
- Hub AI (`https://ai.superclue.io/api/v1`) for chat, embeddings, native tool-call routing
- OrgLens API (`https://orglens.pesmifs.com/api/v1`) for identity (`employees:pii`, `clients:pii`)
- PAN never persisted plaintext: HMAC-SHA256 fingerprint only. Log filter scrubs PAN.
- Atomic Mongo updates (`find_one_and_update`) for race-safe session state

## Personas
- **Visitor** — anonymous prospects, no auth challenge unless they request it
- **Employee** — `@smifs.com` email + PAN verification; auto-consent to RAG ingestion
- **Client** — UCC + PAN verification; explicit consent required for RAG ingestion

## Implemented (Phase 9 — Apr 2026)
- **SMIFS Knowledge API** (deck.pesmifs.com) as PRIMARY authoritative corpus. Full probe documented in `backend/SMIFS_KNOWLEDGE_CAPABILITIES.md`.
- Ingestion: `backend/knowledge_sync.py` with SHA-1 content-hash idempotency, delta + full modes, startup auto-sync if index empty. 1801 chunks ingested across 4 subsources (vehicle/document/academy/bedrock).
- Retrieval: `rag.search_weighted()` with source weights (smifs_knowledge 1.15 > seed 1.00 > upload 0.90 > archive 0.80). Product-topic queries hard-gated to {smifs_knowledge, seed} only.
- Grounding guardrails (`backend/guardrails.py`): refuse+escalate on product queries without strong KB coverage; post-gen claim flagging against citations; `hallucination_events` collection surfaces low-confidence KPI.
- Citation chips carry `source` + `is_official`; FE `TextBlock` renders a gold SMIFS Official badge when a chip is backed by SMIFS Knowledge.
- Admin tab: SMIFS Knowledge API status panel with 5 counters (SMIFS official / seed / uploaded / archives / low-conf 7d), Reachable badge, Delta + Full sync buttons.
- New admin endpoints: `POST /admin/knowledge/sync`, `GET /admin/knowledge/status`, `GET /admin/knowledge/hallucination_events`, `GET /admin/rag/debug`.
- Coverage: 19/20 product questions answered with SMIFS Knowledge as the primary citation source; 5/5 invented-product questions refused cleanly (no fabrication).
- Tests: 11 new cases in `test_phase9_knowledge.py`. Combined backend suite 63/63 green (Phase 6/7/8/8.1/9) when run per-file.

## Implemented (Phase 8.1 — Apr 2026)
- Comprehensive employee Q&A: any question that maps to any of the ~72 OrgLens fields is answerable.
- **USER_PROFILE injection** (`identity.employee_context_block`): full JSON dump of `identity.raw` (minus sensitive credentials) emitted into the chat LLM's system prompt each turn. Self-queries answered directly without any directory tool call.
- **Narrowed `_RAW_STRIP_FIELDS`**: only PAN / Aadhaar / bank / account stripped. Email / phone / DOB / hrbp_email / etc. stay in `raw` so USER_PROFILE can answer "what's my work email?" type questions. Persist-time PII scrub on `conversations.messages[].content` (user turns) remains the privacy boundary.
- **6 new directory tools** on top of Phase 8 (total 15): `directory_filter_by_status`, `directory_recent_joins`, `directory_upcoming_confirmations`, `directory_by_tenure`, `directory_aggregate`, `directory_field_value`. Pagination-aware (OrgLens caps /employees limit=500).
- Expanded `directory_search_employees` filter palette: employee_type, confirmation_status, business_unit, company, gender, on_notice, is_absconding, reports_to_name/email/user_id, hrbp_name.
- **Latent bug fixed in orchestrator**: role-trigger detection (`detect_role_intent`) was re-firing on VERIFIED sessions whenever the user mentioned "employee" or "client" in a self-query, silently resetting auth_state to AWAIT_IDENT. Now guarded behind `state == ANON`.
- `EMPLOYEE_FIELD_MAP.md` documents the full 72-field inventory + privacy contract.
- Coverage: 22/22 matrix rows green (12 self + 10 about-others). Pytest 42/42 green across Phase 7+8+8.1.

## Implemented (Phase 8 — Apr 2026)
- Live OrgLens tool-calling for verified employees: 9 `directory_*` tools registered on the Router dynamically (only when session_type=employee & verified). Tools: `directory_lookup_employee`, `directory_search_employees`, `directory_my_team`, `directory_my_reporting_chain`, `directory_departments`, `directory_locations`, `directory_designations`, `directory_org_stats`, `directory_org_tree`. All dispatched via a single `DIRECTORY_QUERY` intent and executed by `agents/directory_agent.py` (5-min TTL cache).
- 4 new structured FE blocks: `DirectoryCardBlock`, `DirectoryListBlock`, `OrgStatsCardBlock`, `ReportingChainCardBlock`. Guardrail: non-employee sessions politely declined (no directory leakage).
- Persistence-time PII scrub extended from PAN-only to PAN + email + phone. `identity.redact_pii_in_text` applied to user messages before `conversations.history[]` insert. Archives inherit automatically.
- `GET /api/sessions/{sid}` now exposes top-level `lifecycle` field.
- Tests: `tests/test_phase8_directory.py` (12 cases) — all green.

## Implemented (Phase 7 — Apr 2026)
- `lifecycle.py` — strict 2-minute idle expiry + identity-keyed rehydration
- Sessions are **frozen** (`lifecycle="expired"`), not deleted; 30-day TTL on `sessions.updated_at_dt`
- New session UUID minted on expiry, inherits prior's identity hashes (`emp_id_hash`, `ucc_hash`, `pan_hash`, `email_hash`, `phone_hash`) onto a freshly-inserted row so rehydration candidates + resume work
- Endpoints: `GET /api/sessions/{sid}/rehydration_candidates`, `POST /api/sessions/{sid}/resume`, `POST /api/sessions/{sid}/decline_resume`
- Cross-user resume denied with HTTP 403 (HMAC-SHA256 hash-overlap check)
- Orchestrator prepends a `resume_offer` block + top-level `resume_offer` on the first turn after expiry
- Frontend: client-side 110s warning banner, 120s composer lockout with Resume button, `ResumeOfferBlock` renders offered prior session(s)
- Tests: `tests/test_phase7_lifecycle.py` (9 cases) + `tests/test_phase7_inheritance_regression.py` (2 cases) — 11/11 passing

## Implemented (Phase 10 — Apr 2026)
- **Role gateway**: fresh sessions land on `/` (and `/embed`) showing a 3-button gate — *I am a client* / *I am an Employee* / *I am new to the site* — no chat input until a role is picked. `POST /api/sessions/{sid}/select_role` seeds `session_type` + `auth_state` and the bot's first-turn prompt.
- **Session reset on role-pick**: signing out, starting a new session, or being auto-expired returns to the gate. `/api/sessions/{sid}` returns 404 for unknown sids so the FE falls back to the gate cleanly.
- **Knowledge gating (strict)**: `rag.search_weighted()` restricts non-verified-employee sessions to `seed` only — `smifs_knowledge` is NEVER retrievable for clients or visitors. Verified employees continue to see all 1801 `smifs_knowledge` chunks.
- **CLIENT_PROFILE injection**: verified clients' full OrgLens client record (60 fields, inventory at `backend/CLIENT_FIELD_MAP.md`) is compacted and injected into the system prompt. The bot answers self-queries (risk profile, RM, segments, branch, account status, etc.) directly from the profile.
- **Universal Wealth-Manager fallback** (`backend/fallback.py`): for verified clients, any question outside CLIENT_PROFILE (product specifics, NAVs, holdings, research recs) emits `intent=ESCALATION` + `escalation_card` with RM name/email/mobile + the canonical fallback text. For visitors, the same gap produces `intent=CALLBACK_REQUEST` + a `form` block targeting `/api/leads/callback`.
- **Safety net for product-topic detection**: widened `guardrails.is_product_topic` with a property-cue heuristic (NAV, returns, minimum, lock-in, tenure, expense ratio, scheme, portfolio…) so third-party fund names that miss the brand keyword list still trigger the WM short-circuit. Post-generation, `rag_agent._maybe_synthesize_wm_block` re-checks the final reply and synthesises the `escalation_card` if the LLM produced the verbatim fallback phrase and no block was emitted upstream.
- **Tests**: `tests/test_phase10_role_gateway.py` — 22 cases covering all 15 review requirements. **22 / 22 passing in 43.5 s** against live OrgLens + Hub AI + 1801-chunk KB.
- **Deliverables**: `/app/deliverables/phase10/` — role-gate screenshots (root + embed), verified-client chat screenshot (escalation + client_card + masked PAN), full transcripts for all three roles, sample injected CLIENT_PROFILE, reproducible generator script, and a README.

## Implemented (Phase 6 — Apr 2026)
- `identity.py` — OrgLens client, PAN regex/mask/HMAC-hash, role/email/UCC extractors
- New auth state machine (`auth_agent.py`): anonymous → awaiting_role → awaiting_identifier → awaiting_pan → verified | locked
- Orchestrator role-trigger detection (`@smifs.com` email, "I am a client + UCC", "verify me")
- PAN-redaction at persist time (`identity.redact_pan_in_text`) + log filter scrub
- `archives.py` + `session_archives` collection with consent-gated RAG ingestion
- Admin "Archives" tab (list, view transcript, toggle consent, dry-run/ingest)
- Frontend: `EmployeeCardBlock`, role-tinted verified chip, secure-entry hint when bot asks for PAN, client-side PAN masking
- Tests: `tests/test_identity_phase6.py` (10 passing) — privacy, role detection, full E2E flows w/ mocked OrgLens

## Implemented earlier
- Phase 0–4: chat, RAG, multi-agent, admin (cost ledger, leads, KB, insights)
- Phase 5: embeddable widget, theming UI, concurrency hardening
- Phase 6–10: real OrgLens identity (employee/client/visitor), PAN HMAC, role gateway
- Phase 11: WhatsApp/Email handoff CTAs, Knowledge Gaps tab, 15-min KB delta-sync, Stop-generating button
- Phase 12: OrgLens OpenAPI re-probe + 6 client tools (portfolio/ledger/trades/deposits/MF folios/SIPs); alphanumeric UCC fix; PII strip in `identity.raw`
- **Phase 13 (Feb 2026)**: Resilient bot — always-reply envelope on every endpoint, SSE heartbeat→10s + hard 60s cap, adversarial-input short-circuit (24 injection patterns + recommendation/off-topic/profanity), self-healing UCC/PAN/email/phone parsing, role-aware graceful messages, new `errors` + `security_events` collections, 30-row acceptance matrix 30/30.
- **Phase 14 (Feb 2026)**: smifs.com theme match (deep-green `#065B40` + emerald `#098C62` + Libre Baskerville) — all bot/admin surfaces re-skinned; Sales-Ops Bridge — verified employees get role choice → product picker → product-specific form for MF/AIF/PMS/FD/Insurance; `POST /api/sales` persists to `sales_entries` (PAN-hashed + plaintext for ops) with graceful-no-op SMTP relay via `aiosmtplib`; new admin Sales Pipeline tab with KPIs, row drawer, status workflow, resend email.

## Backlog
- P1: Persistent device tokens for re-auth (skip PAN on same device)
- P2: Admin Insights widget for `security_events` (injection attempts over time)
- P2: Surface low-confidence router intents in admin Insights tab
- P2: Per-employee compensation / HR queries (would need careful scope review)

---
## Phase 16 — Knowledge API upgrade (May 24, 2026)
- Step 1 (delta probe) shipped earlier — see `/app/deliverables/phase16/knowledge_api_delta.md`.
- Steps 2–5 shipped today:
  - **knowledge_sync.py**: `_project_metadata()` projector + PII scrub (`updatedBy`). Persists 16 new top-level fields on `doc_chunks` (vehicle_id/name/type, is_focused/active, sales_pitch_ready, version_no, kind, language, provider, category, vertical, updated_at_iso, audience). `phase16_backfill_if_needed()` runs one-time `mode=full` at startup; flag-gated.
  - **rag.py**: `_load_index_from_db` carries the new fields. `search_weighted()` accepts `restrict_audiences`, hard-drops `is_active=False`, boosts bedrock (+0.05), focused (+0.03), recency (+0.02 within 90d).
  - **agents/rag_agent.py**: client/visitor retrieval gets `restrict_audiences=["all"]` so `sales_pitch`/`growth_*` never leak. LLM chunk preamble (`[Type] [Vehicle] [Version] [Updated]`) injected. Citations carry new metadata additively.
  - **TextBlock.jsx + Chat.jsx popover**: chip shows `Updated DD MMM YYYY` + `v<n>`; CTA chip "Open the vehicle factsheet · <name>" gated on `authState === 'verified'` AND `vehicle_id` present. Popover meta line shows vehicle/version/updated.
  - **knowledge_gaps.py + KnowledgeGapsTab.jsx**: new `by_role` counter strip showing hallu/WM/unique per client/employee/visitor.
- **Deliverable**: `/app/deliverables/phase16/kb_matrix.md` — 23-row regression matrix.
- **Verified live**: Phase 16 backfill ran successfully → 1977 chunks upserted, 84 audience=employee_only (sales_pitch + growth_*). Retrieval gating + bedrock boost verified in-process.

---
## Phase 18 — Deck-search fallback + multilingual (Feb 24, 2026)

### Workstream A — Deck Vector Engine fallback (default OFF)
- **`backend/agents/deck_search.py`** (new): lazy fall-through to
  `POST https://deck.pesmifs.com/api/knowledge/search`. `enabled()` reads the
  `DECK_SEARCH_FALLBACK` env flag fresh on every call so ops can flip it
  without a restart. Soft kill-switch: 10 consecutive `totalIndexed==0`
  responses → 1h backoff (one `security_events` row per suspension).
- **Audience drop**: deck hits with `source ∈ {sales_pitch, growth_insurance,
  growth_revenue}` are dropped for non-employee sessions (mirrors the local
  retrieval audience gate). Drops are logged.
- **Telemetry**: per-call rows into `deck_search_calls` (auto-prunes at 50k).
- **`rag_agent._retrieve`** triggers `deck_search()` only when local cosine
  retrieval returns no above-threshold hit. Hits are returned in the same
  shape as `rag.search_weighted` so `_hits_to_chunks` / `_build_citations`
  consume them unchanged. Citation rows tagged `source_engine: deck_search`.
- **`GET /api/admin/deck_search/status`** (admin-token): returns flag state,
  suspension window, in-memory ring buffer, recent telemetry slice.

### Workstream B — Multilingual UX (English / Hindi / Tamil)
- **`POST /api/agent/locale`**: persists `locale` on the `sessions` row
  (validated against `^(en|hi|ta)$`). `GET /api/sessions/{id}` returns it.
- **`orchestrator.locale_instruction()`** + **`_maybe_inject_context(...,
  locale=...)`**: appends the strict instruction
  `"Respond entirely in <Hindi|Tamil>. Use Devanagari/Tamil script. Keep
  technical terms (PAN, UCC, NAV, AUM, ARN, SIP, NCD) in English where
  they are proper nouns."` to every system prompt for hi/ta sessions.
  English (default) is a no-op.
- **`rag_agent._build_messages` + `answer`/`stream_answer`** accept a
  `locale` kwarg and append the same instruction so RAG-grounded answers
  honour the chosen language.
- **`LocaleChoiceBlock.jsx`** (new): two variants —
  - `variant="block"` chip row rendered inline immediately after the role
    pick (so first-time users always see the language toggle).
  - `variant="popover"` rendered from the header globe trigger for
    mid-session switching.
- **Header globe** in `Chat.jsx` + click-outside / Escape handling.
- **Forms + structured data stay in English** — only chat prose localises.

### Tests
- `backend/tests/test_phase18_deck_search.py` — 8 tests (flag short-circuit,
  audience drop matrix, end-to-end with mocked HTTP).
- `backend/tests/test_phase18_locale.py` — 9 tests (instruction wording,
  inject_context override rules, unknown-locale safety).

### Verified live
- Hindi reply: `नमस्ते! मैं मैकेर्टिच वन का वेल्थ-एंगेजमेंट एजेंट…`
- Tamil reply: `வணக்கம், AIF என்பது Alternative Investment Fund ஆகும்…`
- Admin status snapshot: `enabled=false, suspended=false, calls=0`.

### Backlog (deferred to Phase 18.1+)
- Bengali (bn) / Gujarati (gu) / Marathi (mr) — currently a `(en|hi|ta)`
  whitelist on the API.
- Hybrid local+deck merge ranker (deck hits currently appended after local
  candidates).
- Translation of the role-gate / static welcome card prose.

## Phase 19 — Live Office 365 SMTP relay + hierarchy-aware CC routing (2026-05-24, DONE)

**What shipped**
- `email_relay.send_sale_notification` now derives TO/CC dynamically:
  TO = submitting employee; CC = OrgLens manager chain (≤10 levels, 1h cache) + fixed Sales-Ops list (`CC_OPS_FIXED`).
- Four `email_status` values: `sent`, `draft_only`, `smtp_auth_disabled`, `failed_with_fallback`.
  All failure modes write an HTML draft to `/app/deliverables/phase14/email_drafts/`.
- New admin endpoints `/api/admin/email_relay/status` and `/api/admin/email_relay/resolve_chain/{employee_id}` (token-gated).
- Admin UI: drawer renders the full TO/CC chain; KnowledgeBase tab gets an Email Relay Status card.
- Security events: `email_relay_hierarchy_unresolved`, `email_relay_send_failed`, `email_relay_basic_auth_disabled`.
- Password never logged; raw exception text is scrubbed before persistence.

**Live-send checkpoint**
- `SALE-2026-0018` (submitter `SMWM-25031054`): delivered to 1 TO + 7 CC via `smtp.office365.com` (`reason=sent`).
- Wrong-password regression: `reason=smtp_auth_disabled`, fallback draft written, security event row inserted.

---

## Phase 20 — Dynamic OrgLens Tool Registry (2026-05-25)

### Status: Pipeline GREEN, Cutover Gate NOT MET (35/50 PASS = 70%, target 45/50)

### Implemented
- 24-tool manifest registry (`backend/orglens_tools/manifest.yaml`) loaded + validated at boot.
- Generic adapter with role gate, session clamping (UCC/PAN/RM), employee RM-book check, PII masking, Mongo + in-memory cache.
- Question Analyzer (gpt-4o-mini) — 90.7% analyzer→tool hit rate on the matrix.
- Multi-round Hub AI tool-calling orchestrator (gpt-4o, 4-round cap, dedup guard).
- Frontend block renderers (TableBlock, ChartBlock, ImageBlock, DownloadBlock).
- Admin Tools tab (`/api/admin/tools/status` + `ToolsTab.jsx`).
- Telemetry: `tool_calls`, `question_analyzer_calls` collections (90-day TTL).
- Feature flag `PHASE_20_TOOLS_ENABLED=true` in preview ONLY.

### Matrix result (50 questions, mixed roles)
- PASS=35, PARTIAL=14, BLOCKED=1, FAIL=0
- Gate: NOT MET (needs 45/50). Honest scoring per user mandate.
- Deliverables: `/app/deliverables/phase20/{matrix_results.{json,md},matrix_run.md,orglens_bo_crm_scope_request.md}`

### What blocks the 45/50 gate (PARTIAL breakdown)
- **bo-crm endpoint gaps (4 rows)**: NAV history, SIP-collection trend, ledger time-series. Scope request submitted.
- **Visitor parent-orchestrator deflection (3 rows)**: role-trigger fires before Phase 20 for visitor aggregate questions.
- **LLM composition gaps (5 rows)**: Tool data gathered, prompt nudges insufficient to force chart/table block; needs few-shot examples.
- **Scoring/safety (2 rows)**: H2 cross-UCC clamp not surfaced as refusal; H4 deflection not recognised by refusal-marker scorer.

### Next tasks (P0/P1/P2)
- P0: Bypass parent-orchestrator role-trigger for visitor analyzer-aggregate intents → unlocks C4/C7/H4 (+3 PASS expected).
- P0: Surface adapter clamp events to LLM (`clamped:true`) so it can refuse on cross-UCC attempts → H2 (+1 PASS).
- P1: 4-6 few-shot examples in system prompt for chart/table composition → A5, D1, D4, D5, F3 (+5 PASS).
- P1: Phase 21 — delete legacy `_branch_directory` / `_branch_client_query` once flag is on in prod for 2 weeks.
- P2: bo-crm endpoints land → unlocks E1, E2, E3, G2 (+4 PASS expected to reach 48/50).

### Non-regressions confirmed
Phase 16/17/18/19 untouched. SMTP relay still healthy. No router-vocabulary changes.

### Phase 20 V2 re-run (2026-05-25 11:11 UTC)
- **PASS = 34/50 (68%)** · PARTIAL = 15 · FAIL = 0 · BLOCKED = 1 · Gate NOT MET
- Three fixes shipped: visitor parent-orchestrator bypass, adapter clamp surfacing, few-shot composition (decision tree + 5 worked examples).
- Net vs V1: flat (-1). Won A5, A9, A10, C7, C8, D2, D5, E4, H4; lost B1, B5, B6, F2 (LLM became more conservative or emitted JSON-as-text).
- Files: `matrix_results_v2.{json,md}`, `matrix_run_v2.md`. Bo-crm one-paragraph TL;DR added at top of `orglens_bo_crm_scope_request.md`.
- Honest assessment: gpt-4o on Hub AI looks to plateau around 68-70% PASS on the matrix shape. Path forward: (a) bo-crm endpoints land → +4 PASS, (b) tighter list-shape few-shot → +3-4 PASS, (c) try `claude-sonnet-4-5` as the composer model → unknown but worth a probe. 45/50 is reachable with (a)+(b).

### Phase 20 V3 (2026-05-25 ~11:55 UTC)
- **PASS = 39/50 (78%)**, PARTIAL = 10, BLOCKED = 1. In-house gate accepted at 39/50; full 45/50 gate deferred to post bo-crm.
- Shipped: HARD RULE synthesis prompt + response_builder hard gates (clamp + shape) + composer probe.
- Composer probe outcome: gpt-4o and sonnet tied 5/5 on the subset; kept gpt-4o (no swap per ≥3 rule).
- Files: `matrix_results_v3.json`, `matrix_run_v3.md`, `_v3_probe_{gpt4o,sonnet}.json`.
- Net vs V2: +5 PASS (B4, C4, D1, E2, H2). H2 ← clamp gate fired and emitted refusal text matching scorer markers.

### Phase 20 V3.1 hotfix (2026-05-25 PM)
- P0 fix: KNOWLEDGE-intent fallback to legacy RAG when Phase 20 returns text-only refusal/no-tool — restored Phase 16 `vehicle_cta` emission for NCD/MF/PMS queries. Telemetry row written to `tool_calls` as `phase20_fallback_to_rag` for audit.
- P0 fix: card payload normalization. `employee_card`/`client_card` now ship with `data:{...}` wrap matching FE convention. Idempotent — applies to both LLM-emitted and programmatic-fallback cards. `verified:true` defaulted (adapter-sourced = attested).
- Verified: PURPLE STYLE LABS NCD → `vehicle_cta` with `vehicle_id=cc602b11-9fc2-4bbd-b6af-df529f3bf719`. Client snapshot question → `client_card` with populated `data.ucc/client_name/pan/branch/state/rm_name`. Clamp gate still emits localised refusal. Phase 20 tool-shape questions still route through `TOOLS_PIPELINE` (no over-fallback).

### Phase 21 — Sales-Ops field cleanup + SIF + extended ARN/APRN (2026-05-25 PM)
- Field removals across MF / AIF / PMS / FD / Insurance / NCD per user walk-through (13 fields dropped in total).
- Insurance: `product_type` flipped radio → free-text; added `premium_paying_term_years` + `premium_amount_inr`.
- New product `sif` (Specialised Investment Fund) with vehicle-locked identity, conditional frequency, optional lock-in.
- ARN Transfer simplified (dropped existing/new ARN codes + transfer date) and extended to AIF + SIF.
- New PMS APRN Transfer subtype (`aprn_transfer`); admin pipeline filter went boolean → 3-way enum.
- Old sales rows preserved; admin drawer surfaces dropped keys under "Legacy fields" collapsible.
- BE acceptance: all 8 curl tests pass (catalog has 7 buckets including `sif`; MF+legacy fields silently drops; AIF/SIF/PMS transfer subtypes route correctly; SIF lumpsum+frequency rejected 422; Insurance free-text accepted; Insurance missing PPT rejected 422).
- Docs: `SALES_OPS_PRODUCTS.md` Phase 21 section appended.

### Phase 22 — Device-fingerprint fraud detection (2026-05-25 EVE)
- Backend: `fingerprint_guard.py` (scoring + admin actions), `fingerprint_middleware.py` (silent-block + header capture + identity-binding context stash). Indexes auto-created on startup.
- Scoring axes (7-day half-life decay): rapid burst (+25/UCC after 1st), 24h saturation (+15/UCC after 2nd), lifetime-no-RM (decayed cap 10), IP /16 jump within 10 min (+50), UA rotation in 24h (+10). Mitigators: RM linkage (-20 if ≥50% of bound clients name a same-device employee), single network (-10).
- Auto-block at score ≥ 75 (env: `FPRINT_BLOCK_SCORE`); flag-only at ≥ 40. All thresholds env-tunable without redeploy.
- Silent-block: blocked FPs receive HTTP **200** envelopes shaped like real soft-failures — `/api/chat` → "We're currently unable to process your request…", `/api/agent/turn` → `intent: SOFT_ERROR`, `/api/rag/search` → empty hits, `/api/leads` → "Thanks, we'll be in touch" stub (NOT persisted). NEVER a 403, no `blocked: true`, no error banner.
- Admin Fraud Watch tab + REST: `/api/admin/fingerprint/{summary,list,{hash},block,unblock,trust,untrust,note}`. `/api/admin/*` is bypassed by the middleware → operators never lock themselves out.
- Frontend: `@fingerprintjs/fingerprintjs@5.2.0` (silent), `frontend/src/lib/fingerprint.js` caches visitorId in `_smifs_dvc`, sets `axios.defaults.headers.common` → `X-Client-Fingerprint`, `X-Client-Tz`, `X-Client-Screen` for every `/api/*` call. Zero UI surface — no banners, no popups, fully invisible.
- Auth hook: `_finalise_verified` in `auth_agent.py` calls `record_identity_binding(...)` after a PAN match → score recomputed live; identity_key masked in audit (`12***90`).
- Privacy: fingerprint never tied to plaintext PAN/email/phone; audit trail uses masked identity keys; 90-day TTL on security_events, 180-day TTL on device_fingerprint_audit.
- Testing: 27/27 acceptance tests pass — 7 pure scoring (time decay, RM-linkage, IP jump, response shapes, identity masking) + 20 integration (block → silent /api/chat, admin bypass, trust clears block, audit trail, regression APIs). Tests in `/app/backend/tests/test_fingerprint_guard.py` and `/app/backend/tests/test_phase22_fingerprint_integration.py`.
- Docs: `/app/backend/SECURITY_FINGERPRINTING.md` — threat model, scoring axes, silent-block contract, false-positive recovery, env tuning matrix.
- Minor fix during testing: `/api/admin/security_events` now surfaces `fingerprint_hash` and `path` for `fingerprint_silent_block_served` rows (field-name mismatch — writer→reader contract fixed).

### Phase 22.1 — Streaming-endpoint bypass hotfix (2026-05-25 EVE)
- P0 from independent sweep: `/api/agent/turn/stream` was bypassing the Phase 22 silent-block because the FE used native `fetch()` which doesn't inherit axios's default headers. Blocked devices could still stream.
- Fix Layer 1 (FE): new `getFingerprintHeaders()` helper in `lib/fingerprint.js`; `Chat.jsx` streaming POST + `EscalationBlock.jsx` handoff POST both now explicitly inject `X-Client-Fingerprint`, `X-Client-Tz`, `X-Client-Screen`.
- Fix Layer 2 (BE belt-and-suspenders): middleware resolves FP via a 3-step chain — (1) explicit header → (2) `sessions.fingerprint_hash` looked up by session_id → (3) `ip_ua:<sha256(ip|ua)[:32]>` composite fallback. Session stamp now runs post-handler so just-created session rows (e.g. `select_role`) still get the FP.
- Fix Layer 3 (telemetry): every resolution emits a `fingerprint_resolution_source` security event (header @ 1-in-50 sample, session/ip_ua @ 100%). `/api/admin/fingerprint/summary` returns `resolution_source_24h: {header, session, ip_ua}`. Fraud Watch tab renders the trio in amber/red when fallbacks fire.
- Verified live: Playwright Network capture against the preview frontend shows `POST /api/agent/turn/stream` now carries all three headers. Curl proof: a blocked synthetic FP returns the `intent: "SOFT_ERROR"` envelope on the SSE `event: result` frame even with NO `X-Client-Fingerprint` header when the session_id is reused (Layer 2 session-fallback fires). Silent-block payload byte-identical regardless of resolution source.
- 27/27 unit + integration tests still pass; no regression to the 4 already-verified invariants (silent shape, admin bypass, trust recovery, admin block).
- Doc: `SECURITY_FINGERPRINTING.md` Phase 22.1 section appended.

### Phase 23 — Device-aware responsive embed (2026-05-25 LATE)
- Rewrote `frontend/public/widget.js` with 4 responsive breakpoints + lazy iframe + backdrop blur + reduced-motion contract:
  * Mobile portrait (≤640px) → 100dvw × 100dvh full-screen slide-up sheet, partner page scroll-locked, bubble hidden.
  * Mobile landscape (≤950px landscape) → compact 80dvh × min(520,96vw) bottom-sheet.
  * Tablet (641-1024px) → floating min(420,90vw) × 85dvh panel right-side, 18px rounded.
  * Desktop (>1024px) → floating 420×720 panel (unchanged).
  * Narrow ≤360px (Galaxy Fold cover) → smaller 52px bubble.
- `iframe.src` is unset until the user clicks the launcher (lazy-load) — partner Lighthouse scores stay clean.
- Backdrop sibling element fades in at 180ms; iframe slide+scale 220ms `cubic-bezier(.22,1,.36,1)`; vertical slide-up 240ms on mobile.
- `prefers-reduced-motion: reduce` suppresses ALL transitions/animations including the pulse halo on the bubble.
- `App.css` updates: `min-height: 100vh → 100svh` on shell; `height: 100vh → 100dvh` on `.smifs-shell--embed` + `.smifs-popover`; `env(safe-area-inset-top)` on embed header; `env(safe-area-inset-bottom)` + new `--smifs-kb-h` CSS var on sticky composer-wrap; fluid `font-size: clamp(14px, 0.875rem + 0.25vw, 16px)` on embed; `(pointer: coarse)` → 44px min touch-target on send/links/buttons + 16px input to disable iOS zoom; `(max-width: 480px)` shrinks header avatar 44→32px and lets vehicle-CTA names wrap to 2 lines.
- Fixed pre-existing bug: orphan `}` was incorrectly closing `@keyframes smifs-caret-blink` — restructured.
- `Chat.jsx`: new `visualViewport` listener writes `--smifs-kb-h` (in px) onto `.smifs-shell--embed` so the sticky composer floats above the soft keyboard; textarea `onFocus` now also scrolls thread to bottom for mobile keyboard visibility.
- `index.html`: viewport meta now includes `viewport-fit=cover`; added `color-scheme: light dark` so OS-level dark mode doesn't force-invert the light embed surface.
- Verified live across 4 viewports via Playwright: iPhone portrait wrap=375×812@(0,0) edge-to-edge, iPhone landscape 520×245@(284,122) compact, iPad portrait 420×720@(324,284) floating, Desktop 420×720@(996,80) unchanged. Main `/` dark shell renders correctly on both desktop and mobile — no regression.
- `DEPLOY_NOTES.md` — Phase 23 section appended (full breakpoint contract + supported viewport floor of 320px).

### Phase 23.3 — Multi-signal mobile breakpoint resolver (2026-05-25 LATE+)
- P0 from real-device screenshot: blocked Android Chrome users at smifs.com got a small floating panel in the corner instead of full-screen sheet. Root cause: `@media (max-width: 640px) and (orientation: portrait)` — Android Chrome briefly reports `landscape` during URL-bar collapse on initial load, so the rule never fires; the request falls through to the landscape MQ which produces a small bottom-right panel.
- Fix in `frontend/public/widget.js`: introduced JS-level `resolveBreakpoint()` that combines 4 independent signals:
  1. `(pointer: coarse) and (hover: none)` (touch device)
  2. `navigator.userAgentData.mobile` ∪ UA-string regex (uaMobile)
  3. `screen.width ≤ 640` (screenSmall)
  4. `innerWidth ≤ 640` (viewportSmall)
- Bias toward mobile when any 2 signals agree. Then sub-route: phone-in-landscape (shortSide ≤ 480) → `mobile-landscape`; touch device width 720-1024 → `tablet`; else width breakpoints.
- Writes a `.m1-bp-{desktop,tablet,mobile,mobile-landscape}` class onto the iframe-wrap + a `data-bp` attribute for live DevTools inspection. CSS class rules (with `!important` on the mobile-portrait dimensions) carry strictly higher specificity than the legacy width media queries → JS always wins.
- Removed the unreliable `(orientation: portrait)` gate from the mobile MQ. Width-only fallback now correctly catches Android Chrome regardless of orientation flap.
- Re-runs on `resize` + `orientationchange` + every `open()` call so device rotations during a closed-panel session still resolve correctly.
- 9/9 emulated device profiles pass (iPhone 12 portrait, Pixel 5, Samsung Galaxy 360px, Galaxy Fold 280px, iPhone landscape, iPad portrait, iPad landscape, Desktop 1440, Desktop 1920).
- Preview widget.js has 43 Phase 23.3 markers; production widget.js has 0 — user must redeploy to bot.pesmifs.com to push the fix live.

---

## Phase 26 — Multi-Agent + Forms + Persona (partial)

### Landed in this session (verified end-to-end on chat surface)

**26a — User-facing citations hidden** ✅
- `CHAT_SHOW_CITATIONS_TO_USER=false` env flag wired into `/api/widget/config`
- `TextBlock.jsx` + `BmiaFundamentalsCard.jsx` gate chip strip + "Source: BMIA" footer + grounded badge behind the flag
- Citations still emitted in `/api/agent/turn` payload so admin tools see them
- Verified via screenshot: KYC reply shows 0 chip elements, 0 grounded badges; API payload retains 3 citations

**26d — Persona-aware composer** ✅
- New `agents/composer_prompts.py` exposing `CLIENT_PREAMBLE`, `EMPLOYEE_PREAMBLE`, `VISITOR_PREAMBLE`
- `persona_preamble(session_type)` prepended onto `BASE_PROMPT` in `rag_agent._build_messages` and onto `SMALL_TALK_PROMPT` in `orchestrator._branch_small_talk`
- Per-persona form thresholds (`FORM_THRESHOLDS`) and per-persona fan-out eligibility (`FANOUT_ELIGIBILITY`) wired

**26c — Dynamic forms** ✅
- New `agents/dynamic_forms.py` — 5 schemas (demand / referral / feedback / complaint / callback) + deterministic regex trigger detection with confidence scoring
- New `forms_email.py` — dedicated SMTP sender to `FORMS_INBOX_EMAIL` (default brand@smifs.com) with priority-aware subject prefix `[URGENT-COMPLAINT]`
- 5 email templates in `backend/templates/forms/*.html`
- Backend endpoints: `POST /api/forms/submit`, `GET /api/admin/forms/submissions`, `POST /api/admin/forms/{id}/retry` (bearer + legacy header tolerant)
- New `forms_submissions` MongoDB collection with conversation excerpt (last 8 turns), persona, email_status, priority
- Frontend `components/blocks/DynamicFormBlock.jsx` — handles text, email, tel, select, textarea, rating (1–5 stars), urgent variant
- Orchestrator post-turn hook `_maybe_attach_dynamic_form` appends a `dynamic_form` block when trigger ≥ persona-threshold; respects cooldowns (referral 7d, feedback 1/session, demand 1-per-3-turns)
- **Acceptance tests passed (visitor persona):**
  - Test 5 (demand_capture): unknown question → anti-bluff → form rendered with 5 fields → submit → success card with reference id; SMTP relay returned `status: sent` to brand@smifs.com
  - Test 6 (complaint_capture): "This is ridiculous, I want to file a complaint" → 6-field complaint form with `priority: high` and urgent CSS variant; submit dispatched with `[URGENT-COMPLAINT]` subject
  - Test 7 (feedback_capture): substantive turn + "thanks!" → 4-field feedback form including 1–5 star rating widget

### Deferred to Phase 26.1 (out of this session)

**26b — Multi-agent parallel fan-out** ⏳
- Not built. The reactive Phase 20 path remains. Persona eligibility hooks (`composer_prompts.fanout_allowed`) are in place for when the fanout_orchestrator module is wired.
- Suggested next-build order: Event 2 (ticker fan-out) first — easiest to verify as visitor since BMIA + RAG already exist as building blocks. Then Event 3 (product fan-out), then Event 1 (PAN/UCC verified bundle).

**26e — Admin Forms tab UI** ⏳
- Backend endpoint `GET /api/admin/forms/submissions` is live and tested via curl (returns `{rows, counts}` with bearer auth). The retry endpoint is also wired.
- Frontend `FormSubmissionsTab.jsx` not yet built; should mirror `LeadsTab.jsx` pattern.
- Extension of `ArchivesTab` ("Conversations" view) to show which forms fired + which fan-out events fired is also pending.

### Known regressions / caveats
- The form-trigger classifier is regex-only (deterministic). The brief mentioned a gpt-4o-mini classifier — that's deferred for a follow-on if false-positives become an issue in production telemetry. The regex layer is fast, free, and easy to debug.
- For employee persona, lead-capture forms (demand, callback, referral) are disabled via threshold=1.01 in `FORM_THRESHOLDS`. Complaint + feedback remain enabled.

### Production redeploy required
- `bot.pesmifs.com` still serves the pre-26 build. User must redeploy from preview to push 26a/26c/26d live.
