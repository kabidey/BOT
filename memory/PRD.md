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

## Backlog
- P1: Persistent device tokens for re-auth (skip PAN on same device)
- P1: Per-page widget suggestion presets + WhatsApp handoff CTA
- P2: Surface low-confidence router intents in admin Insights tab
- P2: Per-employee compensation / HR queries (would need careful scope review)
