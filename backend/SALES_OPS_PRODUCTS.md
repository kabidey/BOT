# SMIFS Sales-Ops — Supported Products Matrix

Single-source-of-truth feature matrix for the Sales-Ops bridge (the post-
verification "Log a sale?" flow inside the Mackertich ONE Advisor bot).
Each row maps to:

* a tile in `frontend/src/components/blocks/ProductChoiceBlock.jsx`
* a schema entry in `backend/sales_api._PRODUCT_SCHEMA`
* a form definition in `frontend/src/components/blocks/SaleFormBlock.jsx`
* an email subject + label in `backend/email_relay._PRODUCT_LABEL`
* a row in the admin Sales Pipeline tab

| Product id     | UI label             | Status   | Phase | Notes |
|----------------|----------------------|----------|-------|-------|
| `mutual_fund`  | Mutual Fund          | LIVE     | 14    | SIP / Lump sum / SWP / STP. Frequency field is conditional on scheme_type. |
| `aif`          | AIF                  | LIVE     | 14    | Cat I / II / III; commitment + drawdown schedule + fund manager. |
| `pms`          | PMS                  | LIVE     | 14    | Strategy, corpus (≥ ₹50 L), Fixed / Variable / Hybrid fee structure. |
| `fd`           | Fixed Deposit        | LIVE     | 14    | Bank / NBFC / Corporate FD; tenure 1–120 months, rate 0–15 %. |
| `insurance`    | Insurance            | LIVE     | 14    | Term / ULIP / Endowment / Money-back / Health / Annuity. |
| **`ncd_primary`** | **NCD Primary Issue** | **LIVE** | **15** | **Public-issue NCD application. Amount multiple of ₹1,000. Auto-computes number_of_ncds. Coupon 1–20 % p.a., tenure 1–15 y, frequency Monthly / Quarterly / Annual / Cumulative. Optional ASBA / UPI reference.** |

## NCD Primary Issue — fields summary

### Common (shared block)
* `client_name` (text, 2–120 chars, required)
* `client_pan` (regex `^[A-Z]{5}\d{4}[A-Z]$`, required, normalised on submit)
* `client_phone` (≥ 10 digits, required, normalised to last-10)
* `client_email` (RFC-style email, required)
* `amount_inr` (auto-mirrored from `application_amount_inr` on the FE, ≥ ₹1,000)
* `expected_login_date` / `expected_payment_date` (required, today-or-later, payment ≥ login)
* `remarks` (optional, ≤ 500 chars)

### NCD-specific (`product_details`)
| Field | Type / range | Required | Notes |
|---|---|---|---|
| `issuer_name`            | text                          | ✓ | e.g. "Muthoot Finance NCD Tranche IV" |
| `series_option`          | text                          | ✓ | e.g. "Series III — 5Y Quarterly" |
| `application_amount_inr` | number ≥ 10,000, % 1000 == 0  | ✓ | "Multiple of ₹1,000" rule enforced server + client |
| `number_of_ncds`         | int (read-only, computed)     | — | `floor(application_amount_inr / 1000)` — populated server-side at persist time |
| `coupon_rate_pct`        | number 1–20, step 0.01        | ✓ | |
| `tenure_years`           | int 1–15                      | ✓ | |
| `interest_frequency`     | enum                          | ✓ | `Monthly` / `Quarterly` / `Annual` / `Cumulative` |
| `asba_upi_reference`     | text                          | ✗ | Optional payment-method ref. |

## Validation guarantees

* `POST /api/sales` is **verified-employee-only** — visitor / client / unauthenticated sessions get HTTP 403.
* All validation runs server-side; FE pre-flight only short-circuits user friction.
* Negative cases verified by curl in Phase 15:
  * `application_amount_inr = 7500` → 422 `"Application amount must be a multiple of ₹1,000 (NCD face value)."`
  * `coupon_rate_pct = 25`         → 422 `"Must be ≤ 20."`
  * Missing required fields        → 422 `"Required."` per field

## Email subject convention

| Product | Subject template |
|---|---|
| MF / AIF / PMS / FD / Insurance | `[Mackertich ONE] New <Label> sale logged · ₹<amount> · by <employee>` |
| **NCD Primary Issue**           | **`[SMIFS Sales-Ops] NCD Primary Issue — <client_name> — ₹<amount>`** |

## Persistence (single shared collection — `sales_entries`)

The NCD row uses the same envelope as every other product — only
`product = "ncd_primary"` and the contents of `product_details` differ.
The admin tab, KPI roll-ups, status workflow, drawer, and "Resend email"
button work unchanged because they're product-agnostic.

## Regression checkpoints — when to update this doc

* When a new product tile is added: append a row above, bump phase number.
* When a per-product email subject deviates from the default: extend the
  "Email subject convention" table.
* When a field changes type / range: keep the "Validation guarantees" list
  in sync — it's the contract the testing agent reads.
