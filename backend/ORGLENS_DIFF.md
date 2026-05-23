# OrgLens API — Phase 12 Diff Report

OpenAPI spec: `backend/ORGLENS_OPENAPI.json` (138 KB · 167 paths · v3.0.0).
Probed: 2026-05-23 against `https://orglens.pesmifs.com/api/v1`.

## Currently used (Phases 0–11)

| Endpoint | Caller | Purpose |
|---|---|---|
| `GET /v1/employee/by-email/{email}` | `identity.fetch_employee_by_email` | Employee verification step 1 |
| `GET /v1/employee/by-code/{code}` | `directory.lookup_employee`, `field_value`, `reporting_chain` | Direct employee lookup |
| `GET /v1/employees?search=&limit=&skip=` | `directory.search_employees`, `_fetch_all_employees`, identity RM enrichment | Search + paginate |
| `GET /v1/client/by-ucc/{ucc}` | `identity.fetch_client_by_ucc` | Client verification + CLIENT_PROFILE |
| `GET /v1/permissions` | `identity._probe_permissions` | API-key health probe |
| `GET /v1/departments` | `directory.list_departments` | Org chart |
| `GET /v1/locations` | `directory.list_locations` | Branches |
| `GET /v1/designations` | `directory.list_designations` | Role list |
| `GET /v1/org-tree` | `directory.org_tree`, `my_team` | Hierarchy |
| `GET /v1/stats` | `directory.get_stats` | Org KPIs |

## New endpoints worth wiring (Phase 12 candidates)

### Client back-office stack (NEW · `/api/v1/bo/...`)
All gated to verified-client sessions; the bot's verified UCC is the implicit caller — never accept a UCC from the LLM.

| Endpoint | Returns | New tool |
|---|---|---|
| `GET /v1/bo/client/{ucc}/360` | Unified snapshot: master + portfolio + ledger summary | **`client_360`** |
| `GET /v1/bo/client/{ucc}/portfolio` | Cash-market holdings array | **`client_portfolio`** |
| `GET /v1/bo/client/{ucc}/ledger/balance` | Running balance · total credits / debits | **`client_ledger_balance`** |
| `GET /v1/bo/client/{ucc}/ledger?limit=` | Recent ledger entries | (folded into `client_ledger_balance`) |
| `GET /v1/bo/client/{ucc}/trade-book?limit=` | Recent trades from contract notes | **`client_recent_trades`** |
| `GET /v1/bo/client/{ucc}/deposits?limit=` | Money-in entries | **`client_deposits_withdrawals`** (combined) |
| `GET /v1/bo/client/{ucc}/withdrawals?limit=` | Money-out entries | (folded above) |
| `GET /v1/bo/client/{ucc}/charges?limit=` | Brokerage / STT / SEBI / GST breakdown per contract note | future |
| `GET /v1/bo/client/{ucc}/fo-pl` | F&O P&L per scrip | future |

### Client MF stack (NEW · `/api/v1/mf/...`)
| Endpoint | Returns | New tool |
|---|---|---|
| `GET /v1/mf/client/by-pan/{pan}` | MF client lookup (returns UID) | helper used by `client_mf_holdings` |
| `GET /v1/mf/client/{uid}/folios` | Folios (one row per scheme) | **`client_mf_holdings`** |
| `GET /v1/mf/client/{uid}/sips` | Active SIPs with amount + frequency | **`client_mf_sips`** |
| `GET /v1/mf/client/{uid}/transactions?limit=` | MF subscribe / redeem trail | (read via 360) |

### Other (unwired, not yet Phase 12 scope)
- `/v1/bo/clients`, `/v1/bo/stats`, `/v1/mf/clients`, `/v1/mf/folios`, `/v1/mf/funds`, `/v1/mf/stats` — admin / ops surface, not chat-shaped.
- `/v1/graphql` — would be useful for advanced filtering; deferred until we have a structured need.
- `/v1/clients` (search), `/v1/clients/stats` — admin-only.

## Removed / deprecated
**None.** All endpoints we currently use are still present and stable.

## Schema diffs on endpoints we already use
**None.** `/v1/client/by-ucc/{ucc}` still returns `{"client": {...}}`, employees still page on `/v1/employees`. The `_x000d__` artefacts in client records are still there (still handled by `_normalize_client_keys`).

## Phase 12 plan

1. **Ship 5 new client tools** (no employee tools added in this phase — directory_* already comprehensive):
   - `client_portfolio` · `client_ledger_balance` · `client_recent_trades` · `client_deposits_withdrawals` · `client_mf_holdings` · `client_mf_sips` (6 total — `client_360` is implemented but kept internal until Phase 13 chooses where to surface it)
2. All client tools STRICTLY gate on `session_type=="client" AND auth_state=="verified"` and call OrgLens with the session's verified UCC — never an LLM-supplied UCC.
3. New result-block renderers:
   - `holdings_table`, `transactions_list`, `ledger_balance_card`, `mf_folios_list`, `sip_list`
4. Router prompt for clients now exposes the tool palette (with WHEN-TO-USE / WHEN-NOT-TO-USE hints).
5. 5-minute per-(tool,args,uuc) cache + 100/min global token bucket. Existing OrgLens cache layer (`directory._get` already caches) reused.

## Privacy / cost

- No PAN, no Aadhaar, no plaintext bank account numbers in the bot's tool outputs — these are scrubbed before being returned to the chat agent and never sent into the LLM.
- Heavy data (portfolio / ledger) is NOT pre-fetched on verification — tool calls only fire when the user asks a question whose router-classification triggers them.
- Tool responses cached 5 min — repeated questions don't re-hit OrgLens.
