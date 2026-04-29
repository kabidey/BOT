# Mackertich ONE Advisor — Production Deployment Notes

This document describes how to take the SMIFS Lead Wealth-Engagement Agent
(Mackertich ONE Advisor) from preview to production traffic. It assumes the
codebase from `/app/backend` and `/app/frontend` and a managed MongoDB instance.

---

## 1. Required environment variables

| Variable | Description | Example |
|---|---|---|
| `MONGO_URL` | MongoDB connection string | `mongodb+srv://…` |
| `DB_NAME` | Mongo database name | `smifs_advisor_prod` |
| `LLMHUB_API_KEY` | Hub AI key (chat + embeddings + tool-calling) | `llmhub_…` |
| `LLMHUB_BASE_URL` | Hub AI base URL | `https://ai.superclue.io/api/v1` |
| `ORGLENS_API_KEY` | OrgLens key with `employees:pii` + `clients:pii` scopes | `XRdz…` |
| `ORGLENS_BASE_URL` | OrgLens base URL | `https://orglens.pesmifs.com/api/v1` |
| `ADMIN_TOKEN` | **MUST be ≥16 chars, never the dev default** | `<rotated 32-char hex>` |

The backend will boot and `WARN` with a prominent line if `ADMIN_TOKEN`
is missing, equal to the dev default `smifs-admin-2026`, or shorter than 16
chars. It does **not** crash, so misconfiguration is visible but not silent.

## 2. Recommended environment variables

| Variable | Description | Example |
|---|---|---|
| `CORS_PROD_ORIGINS` | Comma-separated production allowlist for the global FastAPI CORS middleware. **When set, replaces the permissive `*` default.** | `https://smifs.com,https://www.smifs.com,https://chat.smifs.com` |
| `HUB_EMBED_MODEL` | Hub embedder model | `text-embedding-3-small` (default) |
| `HUB_EMBED_BATCH` | Embedding batch size | `32` |
| `ROUTER_TOOL_CHAIN` | Comma-separated tool-call-capable models | `llama-3.3-70b-versatile,claude-haiku-4-5-20251001` |
| `HUB_HINT_FIELD` / `HUB_HINT_CHAT` / `HUB_HINT_ROUTER` | Hub AI routing-hint field + JSON values (see `HUB_AI_CAPABILITIES.md`) | `routing_hint` / `{"prefer":"fast"}` / `` |

The widget-config dynamic origin allowlist (Admin → Widget → Allowed Origins)
applies **only** to `GET /api/widget/config` — it does not relax the global
CORS middleware. Set `CORS_PROD_ORIGINS` to lock down all other endpoints.

## 3. Pre-deploy checklist

- [ ] **Rotate `ADMIN_TOKEN`** — generate a new 32-char hex (`openssl rand -hex 32`),
      put it in the prod secret store, and confirm `/api/admin/cost` rejects the
      old token.
- [ ] **Set `CORS_PROD_ORIGINS`** to the exact production hostnames. Trailing
      slashes and protocol matter: `https://smifs.com` ≠ `https://www.smifs.com`.
- [ ] **Set OrgLens key with `pii` scope** (probe via `GET /api/health` →
      `orglens_permissions` should include both `employees:pii` and `clients:pii`).
- [ ] **Allowed origins for the embed widget**: open Admin → Widget → Allowed
      Origins and add the production domains where `<script src=".../widget.js">`
      will be embedded (e.g. `https://smifs.com`). Save.
- [ ] **Embed snippet** to give the marketing team:
      ```html
      <script
        src="https://chat.smifs.com/widget.js"
        data-api="https://chat.smifs.com"
        async></script>
      ```
- [ ] **Verify Hub AI balance** in Admin → Cost Ledger. Top up if `<₹500`.
- [ ] **Confirm RAG corpus** in Admin → Knowledge Base shows the 8 seed docs
      plus any uploaded SOPs.

## 4. Post-deploy smoke test

Run these from a workstation that can reach the prod hostname:

```bash
HOST="https://chat.smifs.com"

# 1. health (LLM + OrgLens both reachable, embedder=hub_ai, cors_mode=prod)
curl -fsS "$HOST/api/health" | jq

# 2. widget config (200 from an allowed origin, theme returned)
curl -fsS -H "Origin: https://smifs.com" "$HOST/api/widget/config" | jq

# 3. visitor turn (no auth challenge, KB-grounded answer)
curl -fsS -X POST "$HOST/api/agent/turn" \
  -H "Content-Type: application/json" \
  -d '{"session_id":null,"message":"What is an AIF?"}' | jq '.intent, .blocks[0].text[:120]'

# 4. admin probe with rotated token
curl -fsS -H "X-Admin-Token: $ADMIN_TOKEN" "$HOST/api/admin/cost" | jq '.balance_inr'
```

Expected:
1. `llm_reachable: true`, `orglens_reachable: true`, `cors_mode: "prod"`,
   `admin_token_strength: "ok"`, `rate_limiting: "in_process"`.
2. JSON with `brand_name: "Mackertich ONE"`, `theme_version` present, and
   the response carries `Access-Control-Allow-Origin: https://smifs.com`.
3. `intent: "KNOWLEDGE"` with a coherent answer.
4. A balance number, no 401.

## 5. Rate limiting (in-process, single-worker)

| Endpoint | Limit | Key |
|---|---|---|
| `POST /api/agent/turn` | 30 / min | `session_id` |
| `POST /api/agent/turn` | 60 / min | client IP |
| `POST /api/agent/turn/stream` | 30 / min | `session_id` |
| `POST /api/agent/turn/stream` | 60 / min | client IP |
| `POST /api/leads` | 10 / min | client IP |

On exceed: HTTP `429 Too Many Requests` with `Retry-After: <seconds>`.
For chat endpoints, the body is a chat-block payload so the FE can render a
soft "please pause" card without breaking the streaming UI.

**Multi-worker note.** The limiter is a per-process sliding window. If you
later scale to >1 uvicorn worker, each worker has its own counter and the
effective limit becomes `N × limit`. Acceptable for low scale; swap to Redis
(or `slowapi` with a Redis store) when traffic warrants. Today the recommended
prod setup is **1 worker with autoscaling on the container layer** which
sidesteps this entirely.

## 6. Logging

- All log records pass through two scrub filters before reaching the handler:
  - `PanScrubFilter` — masks any PAN-shaped token to `XXXXX####X` regardless
    of level.
  - `SecretScrubFilter` — masks `Bearer <token>`, `X-API-Key: <value>`, the
    literal Hub AI / OrgLens / Admin token values, and (at INFO/DEBUG only)
    email addresses to `aa***@domain`. WARN/ERROR keep emails intact for
    on-call debugging.
- Filters are attached to root, uvicorn (access + error), httpx, and every
  `agents.*` logger.

## 7. Known limitations

- **Single-process rate limiter** — see §5.
- **Persistent device tokens** for re-auth (skip PAN within 30 days) are not
  yet implemented — every browser session re-verifies. Tracked as a P1 item.
- **Hub AI balance** is monitored manually via Admin → Cost Ledger. Wire an
  alerting rule on `/api/admin/cost` → `balance_inr` if you want auto-paging.

## 8. Rollback

Roll back by switching the container image tag to the previous version. The
DB schema is forward-compatible from Phase 5 → 6: previous-version pods will
ignore the `session_archives` collection and the new `pending_record` /
`expected_pan_hash` fields on `sessions`. No data migration is required.
