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
