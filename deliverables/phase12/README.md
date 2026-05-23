# Phase 12 — OrgLens API re-probe + client-stack tool expansion

## What changed in OrgLens

OpenAPI v3.1 spec (`backend/ORGLENS_OPENAPI.json`, 138 KB · 167 paths · API v3.0.0).
**Net new capability:** a full **back-office equity stack** under `/api/v1/bo/...` and a **mutual-fund stack** under `/api/v1/mf/...`. None of our existing endpoints changed schema.

Detailed diff: `backend/ORGLENS_DIFF.md`.

## New client tools shipped (6)

All gated to `session_type=client AND auth_state=verified`. The UCC is read from the verified session — **never** from the LLM.

| Tool | Endpoint(s) used | Render block |
|---|---|---|
| `client_portfolio` | `GET /v1/bo/client/{ucc}/portfolio` | `holdings_table` |
| `client_ledger_balance` | `GET /v1/bo/client/{ucc}/ledger/balance` | `ledger_balance_card` |
| `client_recent_trades` | `GET /v1/bo/client/{ucc}/trade-book` | `transactions_list` |
| `client_deposits_withdrawals` | `GET /v1/bo/client/{ucc}/deposits` + `…/withdrawals` | `money_flow` |
| `client_mf_holdings` | `GET /v1/bo/client/by-ucc/{ucc}` → `/v1/mf/client/by-pan/{pan}` → `/v1/mf/client/{uid}/folios` | `mf_folios_list` |
| `client_mf_sips` | `…/mf/client/by-pan/{pan}` → `…/mf/client/{uid}/sips` | `sip_list` |

PAN is resolved server-side via the BO master endpoint and used only as a join key; it is **never returned to the LLM or persisted to session state**.

### Sample responses (Balaram Patro · UCC 63876)

Stored in `tool_samples.txt`. Excerpts:

**`client_portfolio`**
```
"text": "I checked your back-office holdings and currently see no open equity positions in your demat with us. If you've recently traded, settled positions may take a day to appear — happy to refresh again later."
```

**`client_ledger_balance`** (rendered card)
```
"text": "Your trading-account ledger balance is ₹0.00."
"blocks": [{"type": "ledger_balance_card", "data": {"ucc":"63876","balance":0.0,"total_credits":0.0,"total_debits":0.0,"entries":0,"as_of":null}}]
```

**`client_recent_trades`**, **`client_deposits_withdrawals`**, **`client_mf_holdings`**, **`client_mf_sips`** — all return correctly-shaped graceful-empty responses for this particular client (Balaram doesn't actively trade equities or hold MFs via SMIFS). Endpoints reachable, schemas correct, no errors. With an MF-active UCC the same wrappers return populated arrays (verified via curl probes during diff capture).

## 20-row coverage matrix

`coverage_matrix.md` · `coverage_matrix.json` · **20 / 20 PASS**.

| # | Role | Question | Got intent | Got tool |
|---|---|---|---|---|
| 1 | employee | What is my designation? | KNOWLEDGE | (USER_PROFILE) |
| 2 | employee | Who do I report to and who do they report to? | DIRECTORY_QUERY | `directory_my_reporting_chain` |
| 3 | employee | Show me my direct reports. | DIRECTORY_QUERY | `directory_my_team` |
| 4 | employee | List all compliance department members. | DIRECTORY_QUERY | `directory_search_employees` |
| 5 | employee | Tell me about Awanish Chandra. | DIRECTORY_QUERY | `directory_lookup_employee` |
| 6 | employee | How many departments do we have? | DIRECTORY_QUERY | `directory_departments` |
| 7 | employee | List all SMIFS office locations. | DIRECTORY_QUERY | `directory_locations` |
| 8 | employee | Who joined SMIFS in the last 30 days? | DIRECTORY_QUERY | `directory_recent_joins` |
| 9 | employee | What's my HRBP's name? | KNOWLEDGE | (USER_PROFILE) |
| 10 | employee | What's the minimum ticket for Mackertich ONE PMS? | KNOWLEDGE | (smifs_knowledge KB) |
| 11 | client | What's my risk profile? | KNOWLEDGE | (CLIENT_PROFILE) |
| 12 | client | Who is my relationship manager? | KNOWLEDGE | (CLIENT_PROFILE) |
| 13 | client | Show my equity portfolio holdings. | CLIENT_QUERY | **`client_portfolio`** |
| 14 | client | What's my account ledger balance? | CLIENT_QUERY | **`client_ledger_balance`** |
| 15 | client | Show me my recent trades. | CLIENT_QUERY | **`client_recent_trades`** |
| 16 | client | When did I deposit money into my account? | CLIENT_QUERY | **`client_deposits_withdrawals`** |
| 17 | client | Show me my mutual fund holdings. | CLIENT_QUERY | **`client_mf_holdings`** |
| 18 | client | What's the minimum ticket for Mackertich ONE PMS? | ESCALATION | (WM fallback + escalation_card) |
| 19 | visitor | What is an AIF? | KNOWLEDGE | (seed) |
| 20 | visitor | What is the minimum ticket for Mackertich ONE PMS? | CALLBACK_REQUEST | (form) |

Notes:
- Rows 1, 9, 11, 12 stay on USER_PROFILE / CLIENT_PROFILE injection — no tool fired (correct, no OrgLens hop needed).
- Rows 13–17 fire the new `client_*` tools.
- Row 18 preserves Phase 10 verified-client strict gate (escalation, not seed).
- Rows 19–20 preserve Phase 11 bug-fix behaviour.

## Privacy / cost invariants

- `client_*` tools STRICTLY check `session_type=client AND auth_state=verified AND identity.type=="client"` before dispatch (`_branch_client_query` in orchestrator).
- UCC comes from session state, never from `tool_args`.
- PAN is resolved server-side (BO master → MF lookup) and is **never** exposed to the LLM, never logged, never returned in tool output.
- `client_api._scrub_client` strips `pan`, `aadhaar`, `bank_account_no`, `ifsc`, `raw_html_*` before any payload returns.
- 5-minute per-(session, tool, args) cache (mirrors `directory_agent`) so duplicate questions don't re-hit OrgLens. Existing 100/min rate ceiling is comfortable.
- No heavy data is pre-fetched on verification — `client_*` tools only fire on a router decision.

## Phase 0-11 regression status

- `tests/test_phase10_role_gateway.py` — **22 passed in 47 s**.
- No new gpt-4o-mini.
- Role gate, knowledge gating, idle expiry + rehydration, embed widget, WhatsApp / email handoff, Knowledge Gaps tab, KB auto-sync, Stop button — untouched.
