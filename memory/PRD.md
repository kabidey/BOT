# SMIFS Lead Wealth-Engagement Agent — PRD

## Original problem statement
Premium multi-agent chat for SMIFS Management Services Limited (FARM stack).
LLM: Hub AI (OpenAI-compatible) at `https://ai.superclue.io/api/v1`.

## Phase 0 — Delivered (2026-02-28) ✅
Minimal premium chat with Hub AI. `/api/health`, `/api/chat`, `/api/conversations/{sid}`, `/api/docs`. Multi-turn memory, localStorage session_id, navy+gold premium UI.

## Phase 1 — Delivered (2026-02-28) ✅
RAG over 8 SMIFS markdown docs (53 chunks, sentence-transformers all-MiniLM-L6-v2 local fallback since Hub AI `/embeddings` 404). `/api/admin/reingest`, `/api/rag/search`. Citation chips + grounded indicator + side popover. LRU query-embedding cache.

## Phase 2 — Delivered (2026-02-28) ✅
Multi-agent orchestrator with rich block payloads.

### Hub AI model probe (Feb 2026)
30 text models exposed via `/api/v1/models`. Working chain (all 200 OK):
- `gpt-4o-mini` (real OpenAI — primary)
- `claude-haiku-4-5-20251001` (real Anthropic Haiku 4.5)
- `claude-3-5-sonnet-20241022` (Hub silently re-routes to llama)
- `llama-3.3-70b-versatile` (real groq)
- `gemma-4-E4B` (local fallback)
- `auto`

Per-task caching: `ROUTER_CHAIN` (gpt-4o-mini + Haiku first, prefer structured-JSON capable models) and `CHAT_CHAIN` (broader chain for prose). Caches separately so router's preferred model doesn't invalidate chat's, and vice versa.

### Architecture
```
Client (React, manual SSE parser)
  ↓ POST /api/agent/turn or /api/agent/turn/stream
FastAPI orchestrator.run_turn(db, sid, msg, emit_status?)
  ├── Router (LLM, response_format=json_object)
  │     └── intent ∈ {KNOWLEDGE, MARKET_DATA, CLIENT_LOOKUP,
  │                    LEAD_CAPTURE, CALLBACK_REQUEST, ESCALATION, SMALL_TALK}
  ├── Specialist branch:
  │     KNOWLEDGE       → RAG agent → text block + citations
  │     LEAD_CAPTURE    → RAG intro + form_agent.lead_capture_form(asset_class)
  │     CALLBACK_REQUEST→ form_agent.callback_form()
  │     MARKET_DATA     → api_agent.fetch_market_data → market_card
  │     CLIENT_LOOKUP   → api_agent.lookup_client → client_card or escalation
  │     ESCALATION      → escalation_card
  │     SMALL_TALK      → light LLM reply (small-talk system prompt)
  ├── Persist user + assistant turn (with intent/blocks/citations)
  └── Return {session_id, trace, blocks, citations, model, intent}
```

### Endpoints (Phase 2 additions)
| Method | Path | Description |
|---|---|---|
| POST | `/api/agent/turn`         | New primary chat: returns block payload |
| POST | `/api/agent/turn/stream`  | SSE: status events + final result |
| POST | `/api/leads`              | Persist lead_capture / callback submissions |
| GET  | `/api/health`             | Now also returns `last_chat_model`, `last_router_model` |
| POST | `/api/chat`               | Backward-compat (legacy {reply, grounded, citations}) |

### Block types (frontend renderers)
- `text` — markdown-ish (bold + bullets), with citations chips + grounded indicator
- `form` — inline private-bank styled form, validation, submits to `/api/leads`, success state with reference id
- `market_card` — symbol + name + large serif price + colored ±change pill + "as of" timestamp
- `client_card` — verified-or-locked badge, name, code, holdings summary masked when not verified
- `escalation_card` — gold-bordered "Connect to Human Advisor" CTA that triggers a CALLBACK_REQUEST follow-up

### Mock seed (auto on startup)
- `mock_clients` × 5: SMIFS001 Aarav Mehta … SMIFS005 Vikram Joshi (with verify_questions for Phase 3)
- `mock_market` × 10: RELIANCE, HDFCBANK, TCS, INFY, ITC + 5 mutual funds (SBI/ICICI/Axis/HDFC/Mirae)

### Acceptance criteria (all 10 PASS)
| # | Criterion | Result |
|---|---|---|
| 1 | KNOWLEDGE turn — text + citations | ✅ aif_overview cited |
| 2 | LEAD_CAPTURE — text + form, asset_class=NCD | ✅ NCDs→NCD normalized |
| 3 | MARKET_DATA RELIANCE — market_card | ✅ ₹2,842.55 +1.24% |
| 4 | CALLBACK_REQUEST — text + callback form | ✅ |
| 5 | CLIENT_LOOKUP no code — asks for code | ✅ |
| 6 | `/api/leads` persists, returns lead_id | ✅ |
| 7 | SSE streams status events + result | ✅ |
| 8 | Inline form submission → success state | ✅ |
| 9 | Market card with price + change | ✅ |
| 10 | Phase 0+1 unchanged (citations, off-topic, session) | ✅ |

### Testing
- iter 1 (Phase 0): 8/8 backend + 12/12 Playwright = 100%
- iter 2 (Phase 1): 19/19 backend + all Playwright = 100%
- iter 3 (Phase 2): 21/21 backend + 100% frontend (after router-prompt tightening)

## Phase 3 — Backlog (P0)
- Real client identification flow with verify_questions (gate `client_card.holdings_summary` behind correct answers)
- Conversation rehydration on reload (call `/api/conversations/{sid}` on mount)
- Multi-step Q&A within a session (carry verification state)

## Phase 4 — Backlog
- Cost-ledger admin view (Hub AI returns cost.cost_inr + balance_inr per call)
- PDF/DOCX upload for `/api/admin/reingest` (let SMIFS compliance team feed in real product memorandums)
- Streaming token-by-token responses for KNOWLEDGE branch
- Rate-limiting on `/api/chat`, `/api/agent/turn`, `/api/leads`

## Notes for future agents
- Hub AI `/api/v1/models` lists 30 text models. Per-task chains in `agents/llm.py`. Don't remove `auto` from the chain.
- Hub AI `/embeddings` is 404 — local sentence-transformers is active; embeddings cached in MongoDB.
- The lead-capture branch makes an LLM call (RAG intro) — adds ~2s; consider caching by intent+asset_class for landing-page conversion.
- SSE: comments starting with `:` are heartbeats; strip them in any client-side parser.
- `msg-assistant-N` testid uses the full messages-array index (1, 3, 5…); pick the largest numeric suffix when targeting the latest message.
- Suggestion chips disappear after the first message (welcome card is replaced); subsequent flows must type into chat-input.
- Admin token: `smifs-admin-2026` (env `ADMIN_TOKEN`). RAG_MIN_SCORE=0.25.
