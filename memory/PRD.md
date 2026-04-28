# SMIFS Lead Wealth-Engagement Agent — PRD

## Original problem statement
Premium multi-agent chat for SMIFS Management Services Limited (FARM stack).
LLM provider: Hub AI (OpenAI-compatible) at `https://ai.superclue.io/api/v1`.

## Phase 0 — Delivered (2026-02-28) ✅ — Minimal premium chat with Hub AI.
## Phase 1 — Delivered (2026-02-28) ✅ — RAG over 8 SMIFS docs (53 chunks, local sentence-transformers).
## Phase 2 — Delivered (2026-02-28) ✅ — Multi-agent orchestrator (Router → 6 specialists), block payloads, SSE.
## Phase 3 — Delivered (2026-02-28) ✅ — In-chat client verification + session rehydration.

## Phase 4 — Delivered (2026-02-28) ✅
Admin Console at `/admin` — leads, cost ledger, insights, doc uploads.

### Architecture additions
```
Backend
  ├── cost_ledger.py           — extract metrics from every Hub AI response →
  │                                fire-and-forget insert into llm_calls
  ├── agents/llm.py            — call_with_fallback wraps every chat completion
  │                                with cost_ledger.fire_and_forget_record
  ├── admin.py                 — build_admin_router(db) factory, all endpoints
  │                                gated by X-Admin-Token = ADMIN_TOKEN
  └── rag.py                   — ingest_extra_chunks(...) for uploads;
                                  reload_index_from_db rebuilds in-memory index

Frontend
  /admin
    └── pages/Admin.jsx        — token gate + tabbed shell
        └── components/admin/
            ├── OverviewTab    — KPIs + 7-day spark + intent bars
            ├── LeadsTab       — filterable table + side drawer + transcript
            ├── CostLedgerTab  — wallet, today/week/month, by-model, by-task
            ├── InsightsTab    — sessions/messages/verified, intent dist, asset classes
            └── KnowledgeBaseTab — drag-drop upload, doc list, delete
```

### New endpoints (all under `/api/admin/*`, gated by `X-Admin-Token`)
| Method | Path | Description |
|---|---|---|
| GET   | `/admin/cost`               | wallet, today/week/month INR, by_model, by_task, 7d series |
| GET   | `/admin/insights?range=Nd`  | sessions/messages/verified totals, intent distribution, lead asset classes, escalation_rate |
| GET   | `/admin/leads?status=...`   | filterable lead list (newest first) |
| GET   | `/admin/leads/{id}`         | one lead + last 10-turn transcript snippet |
| PATCH | `/admin/leads/{id}`         | update status + notes |
| GET   | `/admin/docs`               | doc_chunks grouped by doc_id with chunk count + source |
| DELETE| `/admin/docs/{doc_id}`      | remove chunks + uploaded file (refuses seed docs) |
| POST  | `/admin/reingest` (multipart) | accepts files=...&reset_seeds=...; supports .pdf/.docx/.md/.txt up to 10MB |

### Cost ledger (collection: `llm_calls`)
Every Hub AI chat completion is captured with:
- `cost_inr`, `input_inr`, `output_inr` (from `data.cost`)
- `balance_inr_after` (from `data.balance_inr`)
- `input_tokens`, `output_tokens`, `total_tokens`
- `model_resolved` (real provider model id), `model_requested` (chain name)
- `latency_ms` (Hub AI's reported, falls back to local)
- `task` (`router` | `chat`), `session_id`, `intent`
- `created_at` (ISO string), `created_at_dt` (real ISODate for TTL)

### TTL indexes (created on startup)
- `sessions.updated_at_dt` → `expireAfterSeconds=86400` (24h, security: verified chip auto-expires)
- `llm_calls.created_at_dt` → `expireAfterSeconds=7776000` (90d cost retention)
- `llm_calls.created_at` (-1, non-TTL secondary index for fast range queries)
- `leads.created_at` (-1, non-TTL secondary index)

### Admin UI tabs
- **Overview**: 6 KPI cards (Wallet ₹, Today, 7d sessions, Verified clients, Total leads, Escalation rate), 7-day cost-burn sparkline, top-intents bar chart
- **Leads**: filterable table (all / new / contacted / qualified / closed); row click → side drawer with full lead, status pills, advisor notes, save, transcript
- **Cost Ledger**: large wallet balance card + 4 KPIs + daily burn sparkline + by-model + by-task tables
- **Insights**: range chips (1d/7d/30d), 4 KPIs, intent distribution + lead asset classes (twin bar charts)
- **Knowledge Base**: drag-and-drop or click-to-pick uploader (PDF/DOCX/MD/TXT, ≤10MB), doc table with delete (refuses seeds)

### Acceptance criteria (all 9 PASS)
| # | Criterion | Result |
|---|---|---|
| 1 | Token gate works (correct/wrong) | ✅ |
| 2 | Overview KPIs populated with real data | ✅ |
| 3 | Leads tab + status update + transcript drawer | ✅ |
| 4 | Cost Ledger shows real wallet balance + tables | ✅ |
| 5 | Insights intent distribution + lead asset classes | ✅ |
| 6 | KB upload + search + delete + seed-delete refused (400) | ✅ |
| 7 | TTL indexes confirmed | ✅ ttl_updated_at_dt 86400; ttl_created_at_dt 7776000 |
| 8 | Phase 0/1/2/3 unbroken | ✅ |
| 9 | Public chat at / leaks zero admin info | ✅ |

### Testing
- iter 5: 16/16 backend pytest + 100% frontend Playwright. No functional bugs. Two LOW dev-only nits (webpack overlay on 401, toast TTL) fixed inline.

### Hub AI active model: `gpt-4o-mini-2024-07-18` (Hub AI key now has openai+anthropic+groq+gemma4-local providers enabled)

## Phase 5 — Backlog (per user)
- "Remembered device" 30-day path: skip Q2 for verified sessions returning within 30d (parked from Phase 3 enhancement suggestion)
- Replace deprecated `@app.on_event` with FastAPI lifespan handlers
- Multi-worker safety: replace module-level globals (`EMBEDDER_KIND`, `_index_matrix`, `_LAST_OK`, `_query_cache`)
- Streaming token-by-token for KNOWLEDGE branch (currently full-message)
- Rate-limiting per IP on `/api/agent/turn`, `/api/leads`, auth attempts
- Surface router confidence in `low_confidence_intents` insights field (currently empty)
- Replace `window.confirm()` with the existing popover pattern in KB delete flow

## Notes for future agents
- **Hub AI**: 30 text models exposed at `/api/v1/models`. `gpt-4o-mini` (real OpenAI) is primary. Per-task chains in `agents/llm.py::CHAT_CHAIN` and `ROUTER_CHAIN`. Don't remove `auto`.
- **Hub AI `/embeddings` 404** → local sentence-transformers (all-MiniLM-L6-v2, 384-dim). Index in `doc_chunks`.
- **Auth pre-empts router** for any message containing `SMIFS\d+` or 10+-digit phone (intentional Phase 3 contract).
- **Cost data** is captured on EVERY successful chat completion automatically via fire_and_forget. Failed calls (401/402/etc) don't get recorded — they don't have cost data.
- **`balance_inr`** in `/api/admin/cost` is read from the latest `llm_calls.balance_inr_after > 0` row. If you ever see ₹0, no chat has been processed yet in this DB.
- **Mock data**: 5 mock_clients (SMIFS001-005), 10 mock_market quotes, 8 seed docs (53 chunks). Verify-question answers are case-insensitive partial-match.
- **Admin token**: `smifs-admin-2026` (env `ADMIN_TOKEN`). RAG_MIN_SCORE=0.25.
- **Public chat at /** does NOT link to `/admin` — separate route, separate token, separate localStorage key.
- **Stale Phase 2 tests** (`test_client_lookup_with_code` etc.) need updating to match Phase 3 contract — that's a hygiene pass, not a regression.
