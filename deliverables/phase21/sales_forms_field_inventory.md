# Sales-Ops Product Forms — Field Inventory (current ground truth)

Source of truth:
- **Frontend** — `frontend/src/components/blocks/SaleFormBlock.jsx` (`COMMON`, `PRODUCT_FIELDS`, `ARN_FIELDS`, `VEHICLE_AUTOFILL_BY_PRODUCT`)
- **Backend** — `backend/sales_api.py` (`_PRODUCT_SCHEMA`, `_validate_common`, `_validate_mf_arn`)

Read-only inventory. No proposed removals — user marks her own deletions.

---

## 0. COMMON fields (rendered on EVERY product form, top of the form)

| Key | UI Label | Type | Required | Locked-by-vehicle | Notes |
|---|---|---|---|---|---|
| `client_name` | Client name | text | yes | no | 2-120 chars |
| `client_pan` | Client PAN | text | yes | no | `^[A-Z]{5}\d{4}[A-Z]$`, auto-upper-cased |
| `client_phone` | Client phone | tel | yes | no | min 10 digits; trimmed to last 10 |
| `client_email` | Client email | email | yes | no | RFC-style regex |
| `amount_inr` | Amount (₹) | number | yes | no | min 1000 |
| `expected_login_date` | Expected login date | date | yes | no | defaults to today+2 |
| `expected_payment_date` | Expected payment | date | yes | no | defaults to today+5 |
| `remarks` | Remarks (optional) | textarea | no | no | — |

---

## 1. Mutual Fund — standard flow (non-ARN)

(`product = "mutual_fund"`, `arnTransfer = false`)

| Key | UI Label | Type | Required | Locked-by-vehicle | Options / Conditional |
|---|---|---|---|---|---|
| `amc_name` | AMC | text | yes | **YES** (auto-filled from picked vehicle) | — |
| `scheme_name` | Scheme name | text | yes | **YES** (auto-filled from picked vehicle) | — |
| `scheme_type` | Scheme type | radio | yes | no | options: `SIP`, `Lump sum`, `SWP`, `STP` |
| `frequency` | Frequency | select | conditional | no | options: `Monthly`, `Quarterly`, `Annually` · shown when `scheme_type && scheme_type !== "Lump sum"` |
| `folio_number` | Folio number (existing) | text | no (optional) | no | — |
| `arn_distributor_code` | ARN / Distributor code | text | no (optional) | no | — |

Backend mirror (`_PRODUCT_SCHEMA["mutual_fund"]`): required=`[amc_name, scheme_name, scheme_type]`, optional=`[frequency, folio_number, arn_distributor_code]`.

---

## 2. Mutual Fund — ARN Transfer sub-flow

(`product = "mutual_fund"`, `arnTransfer = true` — toggled by the "Repeat" pill in the form header. `PRODUCT_FIELDS.mutual_fund` is swapped out for `ARN_FIELDS`.)

| Key | UI Label | Type | Required | Locked-by-vehicle | Options / Notes |
|---|---|---|---|---|---|
| `existing_arn` | Existing ARN code | text | yes | no | regex `^ARN-[A-Za-z0-9]{4,7}$` or 4-7 alphanumeric; auto-upper-cased |
| `new_arn` | New ARN code | text | yes | no | same regex; must differ from `existing_arn` |
| `folio_numbers` | Folio number(s) | text | yes | no | placeholder: "comma-separated folios" |
| `amc_name` | AMC name (locked) | text | yes | **YES** (auto-filled) | label includes "(locked)" suffix |
| `scheme_name` | Scheme name (locked) | text | yes | **YES** (auto-filled) | label includes "(locked)" suffix |
| `transfer_effective_date` | Transfer effective date | date | yes | no | ISO `YYYY-MM-DD` |
| `aum_inr` | AUM being transferred (₹) | number | yes | no | min 1000 |
| `arn_remarks` | Remarks (optional) | textarea | no | no | max 500 chars (backend) |

Backend writes `subtype="arn_transfer"`, `scheme_type="ARN Transfer"` and surfaces `amc_name` / `scheme_name` at the top level for downstream compat (`_validate_mf_arn`).

---

## 3. AIF

(`product = "aif"`)

| Key | UI Label | Type | Required | Locked-by-vehicle | Options / Notes |
|---|---|---|---|---|---|
| `aif_name` | AIF name | text | yes | **YES** (auto-filled) | — |
| `category` | Category | radio | yes | no | options: `Cat I`, `Cat II`, `Cat III` |
| `commitment_amount_inr` | Commitment amount (₹) | number | yes | no | min 0 |
| `drawdown_schedule` | Drawdown schedule | textarea | yes | no | placeholder: "e.g. 100% upfront, OR phased over 3 years (40 / 30 / 30)" |
| `fund_manager` | Fund manager | text | yes | no | — |

---

## 4. PMS

(`product = "pms"`)

| Key | UI Label | Type | Required | Locked-by-vehicle | Options / Conditional |
|---|---|---|---|---|---|
| `pms_provider` | PMS provider | text | yes | **YES** (auto-filled) | — |
| `strategy_name` | Strategy name | text | yes | **YES** (auto-filled) | — |
| `corpus_inr` | Corpus (₹) | number | yes | no | min 5,000,000 (₹50L SEBI floor) |
| `fee_structure` | Fee structure | radio | yes | no | options: `Fixed only`, `Variable only`, `Hybrid` |
| `fixed_fee_pct` | Fixed fee % | number | conditional | no | step 0.01, min 0, max 10 · shown when `fee_structure === "Fixed only"` OR `"Hybrid"` |
| `performance_fee_pct` | Performance fee % | number | conditional | no | step 0.01, min 0, max 50 · shown when `fee_structure === "Variable only"` OR `"Hybrid"` |

---

## 5. Fixed Deposit (`fd`)

(`product = "fd"`)

| Key | UI Label | Type | Required | Locked-by-vehicle | Options / Notes |
|---|---|---|---|---|---|
| `issuer_name` | Issuer (bank / NBFC) | text | yes | **YES** (auto-filled) | — |
| `issuer_type` | Issuer type | radio | yes | no | options: `Bank`, `NBFC`, `Corporate FD` |
| `tenure_months` | Tenure (months) | number | yes | no | min 1, max 120 |
| `interest_rate_pct` | Interest rate (%) | number | yes | no | step 0.01, min 0, max 15 |
| `payout_frequency` | Payout frequency | select | yes | no | options: `Monthly`, `Quarterly`, `Half-yearly`, `Annual`, `On maturity` |
| `fd_type` | FD type | radio | yes | no | options: `Cumulative`, `Non-cumulative` |

---

## 6. Insurance

(`product = "insurance"`)

| Key | UI Label | Type | Required | Locked-by-vehicle | Options / Notes |
|---|---|---|---|---|---|
| `carrier` | Carrier | text | yes | **YES** (auto-filled) | placeholder: "LIC, HDFC Life, …" |
| `product_type` | Product type | radio | yes | no | options: `Term`, `ULIP`, `Endowment`, `Money-back`, `Health`, `Annuity` |
| `policy_term_years` | Policy term (years) | number | yes | no | min 1, max 50 |
| `premium_frequency` | Premium frequency | select | yes | no | options: `Single`, `Annual`, `Half-yearly`, `Quarterly`, `Monthly` |
| `sum_assured_inr` | Sum assured (₹) | number | yes | no | min 0 |

---

## 7. NCD Primary Issue (`ncd_primary`)

(`product = "ncd_primary"`)

| Key | UI Label | Type | Required | Locked-by-vehicle | Options / Notes |
|---|---|---|---|---|---|
| `issuer_name` | Issuer / Issue name | text | yes | **YES** (auto-filled) | placeholder: "auto-filled from picked vehicle" |
| `series_option` | Series / Option | text | yes | no | placeholder: "e.g. Series III — 5Y Quarterly" |
| `application_amount_inr` | Application amount (₹) | number | yes | no | min 10,000 · step 1,000 · helper: "Multiple of ₹1,000 — NCDs are issued in ₹1,000 face-value lots." · backend custom rule: `value % 1000 == 0` |
| `number_of_ncds` | Number of NCDs | **computed** (read-only) | — | no | derived from `application_amount_inr`: `Math.floor(amount / 1000)` when amount > 0 and divisible by 1000, else "—". Helper: "Auto-computed (application amount ÷ ₹1,000)." Not submitted as a separate field. |
| `coupon_rate_pct` | Coupon rate (% p.a.) | number | yes | no | step 0.01, min 1, max 20 |
| `tenure_years` | Tenure (years) | number | yes | no | min 1, max 15 |
| `interest_frequency` | Interest payment frequency | select | yes | no | options: `Monthly`, `Quarterly`, `Annual`, `Cumulative` |
| `asba_upi_reference` | ASBA / UPI reference | text | no (optional) | no | placeholder: "(optional)" |

---

## Vehicle autofill map (which keys get pre-populated + locked when a deck vehicle is picked)

From `VEHICLE_AUTOFILL_BY_PRODUCT` in `SaleFormBlock.jsx`:

| Product | Keys auto-filled from `vehicle_name` |
|---|---|
| `mutual_fund` | `amc_name`, `scheme_name` |
| `aif` | `aif_name` |
| `pms` | `pms_provider`, `strategy_name` |
| `fd` | `issuer_name` |
| `insurance` | `carrier` |
| `ncd_primary` | `issuer_name` |

The same `vehicle_name` string is mirrored into each listed key; the deck does NOT expose a separate provider/scheme split (vehicle_name IS the identity).
