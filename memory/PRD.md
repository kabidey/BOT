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
