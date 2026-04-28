# SMIFS Lead Wealth-Engagement Agent — PRD

## Original problem statement
Premium multi-agent chat for SMIFS Management Services Limited. Stack: FARM (FastAPI + React + MongoDB).
Future phases will add a Router → RAG / API / Form / Auth orchestrator.
LLM provider: Hub AI (OpenAI-compatible) at `https://ai.superclue.io/api/v1`.

## Phase 0 — Delivered (2026-02-28)
Minimal premium chat backed by Hub AI. All 5 acceptance criteria pass.
- `/api/health`, `/api/chat` (with multi-turn memory), `/api/conversations/{sid}`, `/api/docs`
- Hub AI key only allows the `auto` route (resolves to gemma4-local / groq llama models).
  Explicit openai/anthropic models 403 — fallback chain handles it, last-successful is cached.
- React UI on `/`: navy #0B1B2B + gold #C9A86A, Cormorant Garamond serif, localStorage session_id.

## Phase 1 — Delivered (2026-02-28)
RAG pipeline + grounded answers with inline citations. All 6 acceptance criteria pass (19/19 backend + all frontend tests).

### What's been built
- **Seed corpus** (`backend/seed_docs/*.md`): 8 SMIFS-themed markdown docs (NCDs, AIFs, PMS, Mutual Funds, IPOs, KYC/Compliance, About SMIFS, Risk Disclosure)
- **`rag.py`** module:
  - Chunker: split markdown by `##` headings, then ~400-token windows with 50-token overlap → 53 chunks
  - Embedder: probes Hub AI `/embeddings` first; falls back to local `sentence-transformers all-MiniLM-L6-v2` (384-dim). Hub AI returned 404, so **local** is the active path.
  - Vector store: chunks + embeddings persisted in MongoDB collection `doc_chunks`. In-memory L2-normalised numpy matrix for cosine search.
  - LRU query-embedding cache (256 entries) — 140ms for repeat queries vs ~600ms cold.
- **New endpoints**:
  - `POST /api/admin/reingest` — gated by `X-Admin-Token` header (env `ADMIN_TOKEN=smifs-admin-2026`). Returns `{docs, chunks, embedder}`.
  - `POST /api/rag/search` — `{query, top_k}` → top hits with score (debug helper).
  - Auto-ingestion on startup if `doc_chunks` is empty.
- **`/api/chat` updated**:
  - Retrieves top-5 chunks; if any score ≥ `RAG_MIN_SCORE` (0.25) injects them into the system prompt with grounded instructions.
  - If all below threshold → out-of-KB instructions (no fabrication, offer human escalation).
  - Returns `grounded: bool` and `citations: [{doc_id, doc_title, section, score, text}]` (top 3 above threshold).
  - Last 10 turns of history preserved per session.
- **Frontend** (`Chat.jsx` + `App.css`):
  - Citation chips below each grounded reply: `📄 doc_title · §section`
  - "Knowledge grounded" / "Outside knowledge base" indicator pills
  - Right-side popover (with scrim, ESC + click-out close) showing the full passage
  - Updated suggestion chips to RAG-friendly questions (AIF ticket, NCD tax, PMS vs MFs)
  - Header pill now also shows chunk count: `Engine online · gemma-4-E4B · 53 chunks`

### Architecture
```
Client (React)
  ↓ axios POST /api/chat
FastAPI (server.py)
  ↓ rag.search(query, top_k=5)              ← embeds query, in-memory cosine over MongoDB
  ↓ build system prompt with KB block
  ↓ chat_with_fallback(messages)            ← Hub AI with model "auto" (cached)
  ↓ persist user+assistant to MongoDB
  ↓ return {reply, grounded, citations}
```

### Acceptance criteria (all PASS)
| # | Criterion | Result |
|---|---|---|
| 1 | Startup auto-ingests; doc_chunks > 30 | ✅ 53 chunks |
| 2 | `rag/search` "What is an NCD?" → ncds_overview score > 0.5 | ✅ 0.545 |
| 3 | `chat` "AIF minimum ticket" mentions ₹1 crore + cites aif_overview | ✅ |
| 4 | Off-topic "weather Mumbai" → low confidence + escalation | ✅ grounded:false, citations:[] |
| 5 | Citation chips render, hover/click reveals chunk text | ✅ |
| 6 | `/api/docs` updated with new endpoints | ✅ |

## Phase 2+ — Backlog (per user direction)
- **Phase 2**: Multi-agent orchestrator (Router → RAG / API / Form / Auth) per master brief
- **Phase 3**: Conversation rehydration on reload (call `/api/conversations/{sid}` on mount)
- **Phase 4**: Cost-ledger admin view (Hub AI returns `cost.cost_inr` + `balance_inr` per response — surface as ops telemetry)
- Streaming responses (SSE)
- Rate-limit on `/api/chat` and `/api/admin/reingest`
- Lifespan handlers (replace deprecated `@app.on_event`)
- Multi-worker safety: replace module-level globals (`EMBEDDER_KIND`, `_index_matrix`, `_query_cache`) with a process-shared cache or a lazy per-worker init

## Notes for future agents
- Do NOT remove `auto` from `MODEL_CANDIDATES` — explicit OpenAI/Anthropic models 403 on this key.
- Hub AI `/embeddings` returns 404 — the local sentence-transformers fallback is the active path.
  First chat after a cold restart takes ~5-6s for model warm-up; subsequent calls 700ms-1.5s.
- `RAG_MIN_SCORE = 0.25` in `server.py` — anything below means out-of-KB.
- Admin token: `smifs-admin-2026` (in `.env`).
