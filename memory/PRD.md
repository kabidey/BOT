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
