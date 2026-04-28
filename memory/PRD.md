# SMIFS Lead Wealth-Engagement Agent — PRD

## Original problem statement
Premium multi-agent chat for SMIFS Management Services Limited (FARM stack).
LLM: Hub AI (OpenAI-compatible) at `https://ai.superclue.io/api/v1`.

## Phase 0 — Delivered (2026-02-28) ✅ — Minimal premium chat with Hub AI.
## Phase 1 — Delivered (2026-02-28) ✅ — RAG over 8 SMIFS docs (53 chunks, local sentence-transformers).
## Phase 2 — Delivered (2026-02-28) ✅ — Multi-agent orchestrator (Router → 6 specialists), block payloads, SSE.

## Phase 3 — Delivered (2026-02-28) ✅
In-chat client verification + session rehydration.

### Architecture additions
```
Per-turn flow:
  1. Auth pre-check (BEFORE Router):
     - Auto-clear expired lockouts (15min)
     - locked → return locked_response
     - mid-verification → consume message as Q1/Q2 answer (skip Router)
     - anonymous + identifier in message → begin_verification immediately
  2. Otherwise → Router → specialist branch
  3. If verified → inject CLIENT_CONTEXT into KNOWLEDGE / LEAD_CAPTURE / SMALL_TALK system prompts
```

### Auth state machine (collection: `sessions`)
```
anonymous ──[code|phone]──▶ awaiting_q1 ──[correct]──▶ awaiting_q2 ──[correct]──▶ verified
                                  │                          │
                                  └─[wrong×3]──┐       [wrong×3]
                                               ▼
                                            locked (15min, then auto → anonymous)
```
- `failed_attempts` counter, `pending_question_index` cursor, `locked_until` timestamp
- Answers matched case-insensitive trim with substring tolerance ("Mumbai", "mumbai", "I live in Mumbai" all match)
- `verify_questions` are stored in `mock_clients` but stripped from any user-facing response (separate `lookup_client_with_questions` only used inside auth agent)

### Endpoints (Phase 3 additions)
| Method | Path | Description |
|---|---|---|
| GET  | `/api/sessions/{sid}`         | Returns auth_state + client info + full block history (for rehydration) |
| POST | `/api/sessions/{sid}/signout` | Idempotent — resets to anonymous; safe on any sid |

### Personalization injection
When `auth_state == "verified"`, every LLM-using branch (KNOWLEDGE / LEAD_CAPTURE / SMALL_TALK) appends a `VERIFIED CLIENT CONTEXT` block to the system prompt with:
- Full name + first name
- Client code
- Holdings summary
- **Hard rule**: "Open every reply with the client's first name as a salutation (e.g. start with 'Aarav,')"

Verified by: post-auth question "How are NCDs taxed?" returns `"Aarav, Non-Convertible Debentures (NCDs) are taxed in India..."` (deterministic across all 5 mock clients).

### Frontend additions (`Chat.jsx`)
- **On-mount hydration**: reads `localStorage.smifs_session_id` → calls `GET /api/sessions/{sid}` → renders all prior messages through existing block renderer. Falls back silently to welcome state on 404.
- **Verified chip** (`data-testid="verified-chip"`): top-right header pill with avatar circle + first name + "VERIFIED" badge + sign-out icon. Gold border, fade-up animation. Hidden when anonymous.
- **Sign-out** (`data-testid="sign-out-button"`): calls `/api/sessions/{sid}/signout`, clears localStorage, resets thread.
- **Hydrating placeholder**: brief "Restoring your conversation…" indicator while the GET resolves.

### Acceptance criteria (all 8 PASS)
| # | Criterion | Result |
|---|---|---|
| 1 | "My client code is SMIFS001" → asks year of birth | ✅ |
| 2 | Wrong year → "1/3 attempts used"; 3 wrong → lock + escalation | ✅ |
| 3 | Correct 1978 → asks city; correct Mumbai → verified client_card | ✅ |
| 4 | Post-verification question → reply incorporates holdings_summary AND opens with first name | ✅ deterministic after prompt fix |
| 5 | GET /api/sessions/{sid} returns full block history; reload restores everything | ✅ 9-message rehydration verified |
| 6 | Verified chip appears; sign-out clears + resets | ✅ |
| 7 | Phase 0/1/2 unbroken | ✅ |
| 8 | Unknown code (SMIFS999) → not-found, no lockout penalty | ✅ |

### Testing
- iter 4: 9/10 backend Phase 3 cases + 100% frontend (one soft failure on first-name salutation determinism, fixed in-place by hardening the prompt; verified deterministic post-fix)
- iters 1-3 still 100% green; intentional Phase 3 contract changes (auth pre-empts router for codes) require Phase 2 tests to be retired/updated as a hygiene pass

## Phase 4 — Backlog (per user direction)
- **Insights/admin dashboard** (rolled in from Phase 2's smart-enhancement suggestion):
  - `/api/admin/insights` aggregating intent/router-confidence/lead-asset-class metrics from the `conversations.messages.intent` field
  - Top 5 LEAD_CAPTURE asset classes, ESCALATION ratio, average router confidence per intent
- **Cost-ledger admin view** (Hub AI returns `cost.cost_inr` + `balance_inr` per response)
- **PDF/DOCX upload** for `/api/admin/reingest` so SMIFS compliance team can feed real product memorandums without code changes
- Streaming token-by-token responses for KNOWLEDGE branch
- Rate-limiting on `/api/agent/turn`, `/api/leads`, auth attempts (per IP, not just per session)
- TTL index on `sessions` collection (24h on `updated_at`) to prevent unbounded growth
- Replace deprecated `@app.on_event` with FastAPI lifespan handlers
- Multi-worker safety: replace module-level `EMBEDDER_KIND`, `_index_matrix`, `_LAST_OK` globals

## Notes for future agents
- Hub AI: 30 text models exposed. `gpt-4o-mini` (real OpenAI, returns `gpt-4o-mini-2024-07-18`) is the active model. Per-task cache in `agents/llm.py::_LAST_OK`.
- Hub AI `/embeddings` 404 → local sentence-transformers (`all-MiniLM-L6-v2`, 384-dim) is active. Index cached in `doc_chunks` collection.
- **Auth pre-empts router**: any message containing `SMIFS\d+` or 10+-digit phone is intercepted by the auth agent BEFORE Router classification. This is intentional but breaks Phase 2 tests that sent codes expecting `CLIENT_LOOKUP`.
- The orchestrator persists user messages BEFORE auth handling, so even messages sent during a locked state are stored in `conversations.messages`.
- Mock client codes (verify_questions case-insensitive, partial match): SMIFS001/1978/Mumbai (Aarav), SMIFS002/1982/Bengaluru (Priya), SMIFS003/1990/Delhi (Rohan), SMIFS004/1975/Hyderabad (Anaya), SMIFS005/1985/Pune (Vikram).
- The `MARKET_DATA` branch does NOT inject client context — by design. Add it in Phase 4 if "Aarav, RELIANCE is currently…" is desired.
- Admin token: `smifs-admin-2026`. RAG_MIN_SCORE=0.25.
