# OrgLens API Scope Request — Phase 20 Blocked Items

**To**: bo-crm team (OrgLens API owners)
**From**: SMIFS Wealth Bot — Phase 20 working group
**Status**: Draft — pending technical scoping by bo-crm
**Date**: 2026-05-25

---

## 1. Context

Phase 20 of the SMIFS Wealth-Engagement Agent (Lead Wealth Advisor chatbot) graduates the bot from 6 hard-coded API endpoints to a dynamic tool registry the LLM composes against on every turn. We exhaustively walked the live OrgLens OpenAPI spec at `https://orglens.pesmifs.com/api/v1/openapi.json` (35 endpoints, May 2026) and built 24 production-grade adapters covering:

- Firm directory primitives (firm_stats, departments, locations, designations, org_tree)
- Employee lookup (employee_by_code, employee_search, org_tree)
- Broker-office client surface (client_by_ucc, client_360, portfolio, ledger, trade-book, charges, deposits/withdrawals, RM book)
- Mutual-fund surface (client by PAN/UID, folios, transactions, SIPs, MF RM book)
- Aggregates (client_corpus_stats, bo_stats, mf_stats)

The 50-question test matrix (`/app/deliverables/phase20/question_matrix.md`) covers single-fact lookups, filtered lists, aggregates, comparisons, trends, cross-entity, multilingual, and refusal patterns. **Five questions are BLOCKED** in the current matrix because they require capabilities OrgLens does not yet expose.

This document enumerates those gaps, the chatbot-side payload shape we need, and an estimate of business impact, so bo-crm can size scope and prioritise.

---

## 2. Endpoints we need bo-crm to add

### 2.1. MF NAV history / scheme time-series  *(P0 — blocks 1 matrix Q)*

**Why we need it**: Clients ask "Show NAV trend for HDFC Top 100 over 6 months", "How has my fund X performed YTD?", "Plot the NAV curve since I started SIP". Today we cannot answer — the bot fails over to escalation.

**Proposed contract**:
```
GET /api/v1/mf/scheme/{scheme_code}/nav-history
    ?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD
    &granularity=daily|weekly|monthly   (default daily)

Response:
{
  "scheme_code": "...",
  "scheme_name": "HDFC Top 100 Fund - Regular Growth",
  "amc": "HDFC Mutual Fund",
  "asset_category": "Equity - Large Cap",
  "currency": "INR",
  "points": [
    {"date":"2025-12-01","nav":872.45},
    ...
  ]
}
```
Rate limits OK at 5 req/min/IP.

---

### 2.2. SIP collection trends (RM-scoped)  *(P0 — blocks 2 matrix Q)*

**Why we need it**: Every RM asks "My SIP book over the last 12 months", "Team trend this fiscal". The MF `transactions` endpoint exposes per-txn data but is impractical to aggregate client-side (rate limits, 200-row cap, no monthly bucket).

**Proposed contract**:
```
GET /api/v1/mf/sip-collection-trend
    ?rm_name=<string>             (binds to caller's name for employees)
    &from_date=YYYY-MM-DD
    &to_date=YYYY-MM-DD
    &bucket=monthly|quarterly|weekly   (default monthly)

Response:
{
  "rm_name": "...",
  "currency": "INR",
  "buckets": [
    {"period": "2025-06", "sip_count": 142, "sip_amount_inr": 24_56_000, "new_sips": 8, "stopped_sips": 3},
    ...
  ]
}
```

---

### 2.3. Client ledger balance time-series  *(P0 — blocks 1 matrix Q)*

**Why we need it**: Clients ask "Show me my balance over the last 90 days", "Did my balance dip below ₹X this month?". Today's `/ledger/balance` returns a single point — we cannot draw a chart.

**Proposed contract**:
```
GET /api/v1/bo/client/{ucc}/ledger-history
    ?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD
    &granularity=daily|weekly|month_end   (default daily)

Response:
{
  "ucc": "...",
  "currency": "INR",
  "points": [{"date":"2025-12-01","closing_balance":34_50_000}, ...]
}
```

---

### 2.4. New-client onboarding cadence  *(P1 — blocks 1 matrix Q)*

**Why we need it**: Sales-ops / Head-of-Wealth ask "How many new clients did we onboard each month this FY?". Today's `/clients/stats` is a single point-in-time snapshot.

**Proposed contract**:
```
GET /api/v1/clients/onboarding-trend
    ?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD
    &bucket=monthly|quarterly       (default monthly)
    &rm_name=<optional, filters to RM book>

Response:
{
  "buckets": [{"period":"2025-06","new_clients":24,"closed":3,"net":21}, ...]
}
```

---

### 2.5. Deposits vs withdrawals roll-up (per UCC, bucketed)  *(P1 — blocks 1 matrix Q)*

**Why we need it**: Clients ask "Deposits versus withdrawals this FY". The per-row deposit/withdrawal endpoints exist; what's missing is a server-side time-bucketed roll-up that the bot can render as a chart without paginating 200+ rows.

**Proposed contract**:
```
GET /api/v1/bo/client/{ucc}/cashflow-summary
    ?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD
    &bucket=monthly|quarterly|fy   (default monthly)

Response:
{
  "ucc": "...",
  "currency": "INR",
  "buckets": [
    {"period":"2025-06","deposits_inr":15_00_000,"withdrawals_inr":3_25_000,"net_inr":11_75_000},
    ...
  ]
}
```

---

## 3. Optional but high-leverage additions

| # | Endpoint | Why |
|---|---|---|
| 3.1 | `GET /api/v1/bo/portfolio-aggregate?rm_name=` | "Top 10 scrips across my book", "Sector concentration risk". |
| 3.2 | `GET /api/v1/mf/asset-allocation/{uid}` | Server-side computed target-vs-actual split (today we derive from the by-pan payload, brittle). |
| 3.3 | `GET /api/v1/employee/{employee_code}/team-aum` | One-shot RM book size + AUM for manager dashboards. |
| 3.4 | `GET /api/v1/clients/aging-buckets` | Compliance/RM productivity ("clients with zero activity 60d/90d/180d"). |

---

## 4. Cross-cutting asks (any endpoint)

1. **Date-range params on every list endpoint**: `from_date` / `to_date` accepting `YYYY-MM-DD`. Today the trade-book and deposit/withdrawal endpoints are inconsistent.
2. **`fields=` projection** to reduce 102-field MF payloads to the 6 fields the bot actually shows.
3. **`X-Request-Id` echo header** for correlating bot-side traces with OrgLens logs (Phase 13 added our own; an echo would let us pivot fast).
4. **`429 Retry-After` header** on rate-limited responses (today we get a bare 429).
5. **Stable error taxonomy**: top-level `{"error":{"code":"...","message":"..."}}` schema instead of `{detail:"..."}` mixed with `{message:"..."}`.

---

## 5. Security / privacy guarantees we already enforce

The adapter (`backend/orglens_tools/adapter.py`) enforces these BEFORE any new endpoint goes live, so bo-crm does not need to re-implement them:

- Role gate (`visitor` / `client` / `employee` / `admin`)
- Session binding (clients clamped to their verified UCC/PAN, employees to their RM name)
- Employee RM-relationship check (UCC must be in `/bo/clients?rm=<name>` book)
- Field-level masking (PAN, Aadhaar, bank account number)
- Non-admin redaction (CTC, salary structure)
- Mongo-backed cache with per-role scope (a visitor never sees a client's cache row)
- Telemetry into `tool_calls` collection (90-day TTL) for audit

When you ship the above endpoints, please surface raw payloads — the adapter applies masks/redactions in flight.

---

## 6. Engagement model proposed

- bo-crm acknowledges scope (T0)
- bo-crm + bot team agree on contract per endpoint (T0 + 1 week — single working session)
- bo-crm ships P0s (2.1 / 2.2 / 2.3) (target T0 + 4 weeks)
- Bot team flips the tool manifest entries (`backend/orglens_tools/manifest.yaml`) in a single PR within 1 day of bo-crm's release
- Bot team re-runs the 50-question matrix and updates the cutover gate

---

## 7. Risks / open questions

- **NAV history depth**: do we have ≥ 3 years of NAV data in source? (Bot UX assumes 6-month default but allows up to 3yr ranges.)
- **PII in net-AUM aggregations**: confirm the proposed RM-scoped roll-ups never leak per-client UCCs in error responses.
- **Cache invalidation**: bo-crm should publish an SSE/webhook stream (or `Last-Modified`) for the time-series endpoints so we can purge stale cache rows on price/balance updates.

---

## 8. Sign-off

- **Bot team**: SMIFS Wealth Bot working group (Phase 20)
- **bo-crm requested sign-off**: Backend lead, Data lead
- **Target review meeting**: TBD

— End of scope request —
