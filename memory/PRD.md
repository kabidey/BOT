# SMIFS Lead Wealth-Engagement Agent — PRD

## Original problem statement
Premium multi-agent chat system for SMIFS Management Services Limited, a wealth
management firm. Stack: FARM (FastAPI + React + MongoDB). Future phases will add a
Router → RAG / API / Form / Auth orchestrator. LLM provider: Hub AI (OpenAI-compatible)
at `https://ai.superclue.io/api/v1/chat/completions`.

## Phase 0 — Delivered (2026-02-28)
Goal: prove Hub AI integration works end-to-end with a minimal premium chat.

### Architecture
- **Backend (`/app/backend/server.py`)** — FastAPI on port 8001 (supervisor-managed).
  All routes prefixed with `/api`. `httpx.AsyncClient` used for Hub AI calls (30s timeout).
- **Frontend (`/app/frontend/src/pages/Chat.jsx` + `App.css`)** — React on port 3000.
  Premium navy (#0B1B2B) + gold (#C9A86A) theme. Cormorant Garamond serif heading,
  Manrope body, JetBrains Mono session id.
- **MongoDB collection** `conversations` keyed by `session_id` with `messages: [{role, content, ts}]`.

### Hub AI integration notes
- Provider permissions on the supplied key block `openai` and `anthropic` (HTTP 403).
- Working route: `model: "auto"` → resolves to `gemma-4-E4B` via provider `gemma4-local`.
- Fallback chain: `["auto", "gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet"]`.
- Last-successful model is cached at module level to skip wasted 403 attempts on subsequent calls (saves ~600-800ms per request).

### Endpoints
| Method | Path | Description |
|---|---|---|
| GET  | `/api/`                          | service banner |
| GET  | `/api/health`                    | tiny ping; returns `{status, llm_reachable, model, detail}` |
| POST | `/api/chat`                      | `{session_id?, message}` → `{session_id, reply, model}` |
| GET  | `/api/conversations/{session_id}`| persisted thread |
| GET  | `/api/docs`                      | FastAPI Swagger UI |
| GET  | `/api/openapi.json`              | OpenAPI spec |

### Acceptance criteria (all PASS)
1. ✅ `/api/health` returns `llm_reachable: true` (model: `gemma-4-E4B`)
2. ✅ `/api/chat` with `{message:"Hello"}` returns coherent reply + session_id
3. ✅ Same `session_id` continues conversation (history sent to LLM)
4. ✅ Frontend at `/` lets user chat; refresh preserves thread via localStorage
5. ✅ `/api/docs` lists endpoints

### Testing
- `/app/backend/tests/test_smifs_chat.py` — 8/8 pytest cases pass
- Playwright UI tests — 12/12 assertions pass
- Verified: thread memory, 422 on empty message, 404 on unknown session

## Phase 1 — Backlog (P0)
- Multi-agent orchestrator: Router → RAG / API / Form / Auth
- RAG over SMIFS knowledge base (products, fee structure, KYC docs)
- Lead-capture forms surfaced inline in chat
- Auth: identify prospects vs. existing clients

## Phase 2+ — Backlog (P1/P2)
- Streaming responses (SSE)
- Conversation rehydration on refresh (call `/api/conversations/{sid}` on mount)
- Lifespan handlers (replace deprecated `@app.on_event`)
- Rate-limit / abuse protection on `/api/chat`
- Admin dashboard: conversation transcripts, lead scoring
- WhatsApp / SMS hand-off

## Notes for future agents
- Do NOT remove `auto` from `MODEL_CANDIDATES` — explicit OpenAI/Anthropic models 403 on this key.
- Hub AI returns balance + cost in each response (`balance_inr`, `cost.cost_inr`) — useful for surfacing burn rate later.
