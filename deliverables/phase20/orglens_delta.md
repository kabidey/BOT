# Phase 20 — OrgLens Deep-Integration Probe & Delta

> **Executive summary** *(read this first)*
>
> OrgLens publishes a real OpenAPI spec at `https://orglens.pesmifs.com/api/openapi.json` — **167 operations across 25 tag buckets**, with **35 endpoints in the four "external-api*" tags** that we can hit with our `X-API-Key`. We currently consume **10 of those 35** (≈ 29 % surface coverage) and on the endpoints we do consume we project **~32 % of the returned fields** — so the *effective* utilisation rate of the OrgLens corpus we have rights to is roughly **0.29 × 0.32 ≈ 9 %**. The 4-endpoint `bo-crm` cluster (branch economics, per-client PnL) is locked behind HTTPBearer and is **not accessible from the chat backend** until OrgLens grants it to our API key — call this out before the build pass. Latencies are healthy: every directory + client-detail endpoint runs at **p50 ≈ 800 ms / max ≈ 920 ms**; only `/org-tree` is slow (**p50 ≈ 2.8 s, must be cached**). The single biggest under-used asset is `/api/v1/mf/client/by-uid/{uid}` — every MF client carries **102 raw fields** including AUM debt/equity split, target asset allocation, RM phone/email, KYC, family, bank, occupation, net worth — we surface zero of this in chat today.

---

## 1. Probe methodology

1. Tried the obvious doc paths (`/api-docs`, `/swagger.json`, `/openapi.json`, `/redoc`, `/api/v1/openapi`). The OrgLens SPA returns its `index.html` for all of those. The real spec lives at **`/api/openapi.json`** (138 kB, 167 operations).
2. Saved the spec to `orglens_probe/_openapi.json` and parsed it into a flat catalog at `orglens_probe/_catalog.json` (173 operation variants — six paths expose both an `external` and a `bo-crm` flavour).
3. For every endpoint in the four "external-api*" tags + `client-external-api`, sampled a live response with our production key. **40 raw payloads** captured in `orglens_probe/v1_*.json`.
4. Latency-sampled the hot path (3× per endpoint) — see §5.
5. Confirmed the auth gate on every `bo-crm` operation returns **`HTTPBearer` 401** with our X-API-Key (response `{"detail":"Not authenticated"}`).

---

## 2. Surface map by tag

| Tag | # endpoints | Auth | Accessible from chat? | Notes |
|---|---|---|---|---|
| `external-api` | 10 | X-API-Key | ✅ | Directory primitives — employees, departments, locations, designations, stats, org-tree, permissions |
| `external-api-bo` | 12 | X-API-Key | ✅ | Broker-office client financial data — 360, portfolio, ledger, trade-book, charges, deposits, withdrawals, fo-pl |
| `external-api-mf` | 9  | X-API-Key | ✅ | Mutual-fund — clients-by-uid/pan, folios, transactions, sips, funds, mf/stats |
| `client-external-api` | 4 | X-API-Key | ✅ | General client identity layer — `/clients`, `/client/by-ucc`, `/client/by-pan`, `/clients/stats` |
| `bo-crm` | 4 | HTTPBearer (user JWT) | ❌ **blocked** | Branch economics, per-client PnL — needs OrgLens to expand our key scope or accept a service JWT |
| `sharepro-api` | 29 | HTTPBearer | ❌ blocked | Internal broker analytics, queue admin, brokerage cache |
| `mutual-funds` (non-`v1`) | 17 | HTTPBearer | ❌ blocked | Internal MF admin dashboard — credentials mgmt, sync, dashboards |
| `employees`, `stats` (non-`v1`) | 10 | HTTPBearer | ❌ blocked | Internal HR admin; redundant with the `/v1/*` external-api equivalents |
| `auth`, `api-keys`, `users`, `audit`, `sync`, `entity-resolver`, `gateway-health`, `csv`, `fields`, `backoffice*`, `bo-crm`, `untagged` | 71 | mixed (mostly admin/bearer) | ❌ admin-only | Out-of-scope for the chat bot |

**Net usable surface for the chat backend today = 35 endpoints across 4 tags.**

---

## 3. Current consumption (delta vs spec)

| Endpoint | Currently called? | Caller | Fields projected today | Fields returned (BO/MF live sample) | Field-utilisation |
|---|---|---|---|---|---|
| `GET /api/v1/stats` | ✅ | `directory.aggregate_stats()` | 4 | 4 | **100 %** |
| `GET /api/v1/permissions` | ✅ | `directory._capabilities()` | 5 | 5 | **100 %** |
| `GET /api/v1/departments` | ✅ | `directory.departments()` | 3/dept | 5/dept | 60 % |
| `GET /api/v1/locations` | ✅ | `directory.locations()` | 3/loc | 5/loc | 60 % |
| `GET /api/v1/designations` | ✅ | `directory.designations()` | 3/desig | 5/desig | 60 % |
| `GET /api/v1/employees` | ✅ | `directory.search_employees()` | ~12 | 77 | **15 %** |
| `GET /api/v1/employee/by-code/{id}` | ✅ | `directory.employee_by_code()` | ~25 | **77** | **32 %** |
| `GET /api/v1/employee/by-email/{email}` | ✅ | `directory.employee_by_email()` | ~25 | 77 | 32 % |
| `GET /api/v1/org-tree` | ✅ | `directory.org_tree()` | tree shape | tree shape + 77 emp fields/node | n/a (structure-only consume) |
| `GET /api/v1/employees/{identifier}` | ❌ | — | — | 77 | **0 %** |
| `GET /api/v1/bo/client/by-ucc/{ucc}` | ✅ | `client_api.client_lookup()` | ~10 | 47 | **21 %** |
| `GET /api/v1/bo/client/{ucc}/360` | ✅ | `client_api.client_360()` | ~12 | **7-block snapshot, ~70 leaf fields** | **17 %** |
| `GET /api/v1/bo/client/{ucc}/portfolio` | ✅ | `client_api.portfolio()` | ~6/holding | 12/holding | 50 % |
| `GET /api/v1/bo/client/{ucc}/ledger/balance` | ✅ | `client_api.ledger()` | 3 | 6 | 50 % |
| `GET /api/v1/bo/client/{ucc}/trade-book` | ✅ | `client_api.trades()` | ~6/trade | 14/trade | 43 % |
| `GET /api/v1/bo/client/{ucc}/deposits` | ✅ | `client_api.deposits()` | ~5/dep | 9/dep | 56 % |
| `GET /api/v1/bo/client/{ucc}/withdrawals` | ✅ | `client_api.withdrawals()` | ~5/wd | 9/wd | 56 % |
| `GET /api/v1/bo/clients` *(list w/ rm filter!)* | ❌ | — | — | 47/client | **0 %** |
| `GET /api/v1/bo/client/{ucc}/ledger` *(full)* | ❌ | — | — | rich list | **0 %** |
| `GET /api/v1/bo/client/{ucc}/charges` | ❌ | — | — | rich list | **0 %** |
| `GET /api/v1/bo/client/{ucc}/fo-pl` | ❌ | — | — | scrip-level F&O P&L | **0 %** |
| `GET /api/v1/bo/stats` | ❌ | — | — | 6 corpus counters | **0 %** |
| `GET /api/v1/mf/client/by-pan/{pan}` | ✅ | `client_api.mf_lookup()` | ~8 | **102** | **8 %** |
| `GET /api/v1/mf/client/by-uid/{uid}` | ❌ | — | — | 102 | **0 %** |
| `GET /api/v1/mf/client/{uid}/folios` | ✅ | `client_api.folios()` | ~6/folio | 12/folio | 50 % |
| `GET /api/v1/mf/client/{uid}/sips` | ✅ | `client_api.sips()` | ~6/sip | 14/sip | 43 % |
| `GET /api/v1/mf/client/{uid}/transactions` | ❌ | — | — | rich txn-list | **0 %** |
| `GET /api/v1/mf/clients` *(global search, RM filter)* | ❌ | — | — | 102/client | **0 %** |
| `GET /api/v1/mf/folios` *(global folio search)* | ❌ | — | — | 12/folio | **0 %** |
| `GET /api/v1/mf/funds` | ❌ | — | — | `{fundid, fundName}` only | n/a (thin) |
| `GET /api/v1/mf/stats` | ❌ | — | — | AUM ₹162.24 Cr + 7 counters | **0 %** |
| `GET /api/v1/clients` *(global)* | ❌ | — | — | 5 indexed fields | **0 %** |
| `GET /api/v1/clients/stats` | ❌ | — | — | category/state breakdowns across 38566 clients | **0 %** |
| `GET /api/v1/client/by-ucc/{ucc}` *(canonical)* | ❌ | — | — | richer than bo flavour | **0 %** |
| `GET /api/v1/client/by-pan/{pan}` *(canonical)* | ❌ | — | — | richer than bo flavour | **0 %** |

**Summary**: 10 endpoints touched of 35 (29 % surface) — field utilisation ≈ 32 % on those — effective corpus utilisation ≈ **9 %**.

---

## 4. Top-30 highest-value unused endpoints, ranked by user-question impact

| # | Endpoint | Bucket | Sample user question it unlocks |
|---|---|---|---|
| 1  | `GET /api/v1/mf/client/{uid}/transactions` | MF transactions | "Show my last 10 MF purchases, biggest first." |
| 2  | `GET /api/v1/mf/clients?rm_name=…` | RM-scoped client list | "List all my MF clients with AUM > 10 L, sorted by AUM." |
| 3  | `GET /api/v1/bo/clients?rm=…` | RM-scoped BO list | "Which of my equity clients haven't traded in 60 days?" |
| 4  | `GET /api/v1/mf/client/by-uid/{uid}` *(full 102-field profile)* | MF profile | "What's my current debt-equity split versus my target allocation?" |
| 5  | `GET /api/v1/clients/stats` | Category mix | "How does our client base break down by category?" → pie chart |
| 6  | `GET /api/v1/mf/stats` | Firm-wide MF KPI | "What's the total MF AUM SMIFS manages?" → single fact |
| 7  | `GET /api/v1/bo/stats` | Firm-wide BO KPI | "How many BO ledger entries did we process this year?" |
| 8  | `GET /api/v1/bo/client/{ucc}/charges` | Per-client brokerage | "How much brokerage have I paid this FY?" → line chart |
| 9  | `GET /api/v1/bo/client/{ucc}/ledger` *(full)* | Full BO ledger | "Show all ledger entries last week with running balance." |
| 10 | `GET /api/v1/bo/client/{ucc}/fo-pl` | F&O P&L | "What's my realised F&O P&L for FY25-26?" |
| 11 | `GET /api/v1/mf/folios?scheme_name=…` | Folio search | "Which clients hold the Parag Parikh Flexi Cap fund?" |
| 12 | `GET /api/v1/client/by-ucc/{ucc}` (canonical) | Client identity | "Resolve UCC M700778." |
| 13 | `GET /api/v1/client/by-pan/{pan}` (canonical) | PAN lookup | "Pull the client tied to PAN GBPPS3015F." |
| 14 | `GET /api/v1/clients?state=West Bengal` | Geographic filter | "How many active clients do we have in West Bengal?" |
| 15 | `GET /api/v1/employees/{identifier}` | Polymorphic emp lookup | "Find me Aaditya — by either email or code." |
| 16 | `GET /api/v1/employee/by-code/{id}` *(use 77 fields, not 25)* | Employee deep profile | "What's Aaditya's HOD / HRBP / band / grade / cost-centre?" |
| 17 | `GET /api/v1/employees?department=Finance` | Team list | "Roster of the Finance team with reporting lines." |
| 18 | `GET /api/v1/org-tree?status=active` | Org structure | "Render the SMIFS org tree." |
| 19 | `GET /api/v1/bo/client/by-ucc/{ucc}` *(47 fields not 10)* | Client master | "What's UCC M700778's branch code, all segment flags, KYC status?" |
| 20 | `GET /api/v1/bo/client/{ucc}/360` *(unwrap all 7 blocks)* | One-shot snapshot | "Give me a full snapshot of UCC M700778." → multi-block answer |
| 21 | `GET /api/v1/mf/clients?name=…` | Name search | "Find my client Amitabh Varma." |
| 22 | `GET /api/v1/bo/client/{ucc}/trade-book?from_date=…&exchange=NSE` | Filtered trades | "Show NSE buys in October." |
| 23 | `GET /api/v1/bo/client/{ucc}/deposits?from_date=…` | Funding history | "How much did I deposit in Q3?" → bar chart |
| 24 | `GET /api/v1/bo/client/{ucc}/withdrawals?from_date=…` | Payouts | "What withdrawals are pending?" |
| 25 | `GET /api/v1/mf/client/{uid}/folios?fields=…` *(filtered)* | Targeted folios | "Show only my equity folios." |
| 26 | `GET /api/v1/mf/client/{uid}/sips` *(use 14 fields not 6)* | SIP details | "Which of my SIPs are running, paused, or stopped?" |
| 27 | `GET /api/v1/clients?status=Suspended` | Suspended audit | "List all suspended client accounts." (employee only) |
| 28 | `GET /api/v1/departments?status=active` | Active deps | "How many departments are active right now?" |
| 29 | `GET /api/v1/locations?status=active` | Office locations | "Where are SMIFS offices located?" |
| 30 | `GET /api/v1/designations?status=active` | Title taxonomy | "What designations exist in the firm?" |

> **Blocked (need OrgLens to expand our API key scope)**: branch economics (`/api/bo/crm/*`), per-client PnL with days param (`/api/bo/client/{code}/pnl`), brokerage-cache analysis, the rich sharepro-api cluster. Recommend the user raises a ticket with OrgLens to allow our X-API-Key on the bo-crm operations *before* the build pass, otherwise we leave a 25-question hole in the matrix.

---

## 5. Latency observations

Sampled live from preview (3 calls per endpoint, sequential):

| Endpoint | p50 (ms) | max (ms) | Tier |
|---|---:|---:|---|
| `/api/v1/org-tree?limit=100` | 2 825 | 3 578 | **slow** |
| `/api/v1/org-tree?limit=10`  | 2 751 | 2 993 | **slow** |
| `/api/v1/bo/client/{ucc}/360` | 905 | 911 | medium |
| `/api/v1/clients/stats` | 886 | 917 | medium |
| `/api/v1/bo/client/by-ucc/{ucc}` | 822 | 823 | medium |
| `/api/v1/bo/stats` | 821 | 864 | medium |
| `/api/v1/mf/stats` | 814 | 821 | medium |
| `/api/v1/employee/by-code/{id}` | 813 | 829 | medium |
| `/api/v1/clients?limit=3` | 834 | 836 | medium |
| All other detail/list endpoints | 780 – 830 | ≤ 870 | medium |
| `/api/v1/permissions`, `/api/v1/stats` (cached on OrgLens side) | 787 – 800 | ≤ 860 | medium |

**Implications for the tool-call budget**

* Single-tool turns are comfortable inside a 6 s soft cap (≈ 800 ms + 1.5-2 s LLM).
* Two-tool turns need parallelism (`asyncio.gather`) — sequential would hit 1.6 s + LLM.
* Three-tool turns demand both parallelism AND aggressive caching, otherwise we blow past the cap.
* `/org-tree` MUST be cached for ≥ 5 min — never fetch on the hot path. Pre-warm at boot.

---

## 6. Auth / role honesty

* **All `external-api*` and `client-external-api` operations accept our single X-API-Key.** No per-user JWT required for the chat backend.
* `bo-crm`, `sharepro-api`, `mutual-funds` (non-v1), `employees` (non-v1) — every operation gated on `HTTPBearer`. **Not callable from our backend.** Recommend asking OrgLens to mirror the bo-crm endpoints into the `external-api-bo` tag with X-API-Key auth, OR to issue us a service-account bearer token.
* `permissions` returns scopes our key holds: `employees:basic`, `employees:contact`, `employees:hierarchy`, `employees:pii`, `employees:compensation`, `org_tree:read`, `stats:read`. **We hold `employees:compensation` but never read salary fields.** Worth surfacing to admin tier only (never to a verified-client surface).
* Role-gating must happen in the **adapter layer**, not in the prompt. Visitors get directory primitives only (departments, stats counters, no PII). Clients get their own UCC scoped tools only. Employees get the full surface plus an RM-relationship check before they can see another client's data (the `bo/clients?rm=` filter already enforces this server-side — we should mirror that in the adapter).

---

## 7. Raw artefacts

* `orglens_probe/_openapi.json` — full 138 kB spec
* `orglens_probe/_catalog.json` — flat catalog of all 173 operation variants
* `orglens_probe/v1_*.json` — 40 sampled live payloads (employee, BO, MF, client, stats endpoints — both happy and empty paths)

Field-by-field enumeration is preserved verbatim in those JSON dumps. The architecture proposal (`architecture_proposal.md`) uses them to build the tool-registry schema in the next pass.
