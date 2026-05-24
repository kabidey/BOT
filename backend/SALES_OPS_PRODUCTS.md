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

---

## Phase 17 — Deck-pegged Sales-Ops picker + MF ARN Transfer (May 2026)

### Catalog endpoint
`GET /api/sales/catalog?session_id=<sid>` — verified-employee-only (403 otherwise).
Source: `doc_chunks` rows where `subsource == "vehicle"`. 60-second in-process cache.
Returns:
```jsonc
{
  "generated_at": "2026-05-…",
  "total_vehicles": 168, "unmapped_count": 0,
  "totals": {"mutual_fund": 48, "aif": 31, "pms": 42, "fd": 4, "insurance": 42, "ncd_primary": 1},
  "buckets": {
    "mutual_fund": [{vehicle_id, vehicle_name, vehicle_type, is_focused, is_active, updated_at_iso}, …]
  }
}
```
Within each bucket: `is_focused=true` first, then alphabetical by `vehicle_name`. `is_active=false`
rows are NOT filtered (user decision: "all deck vehicles are sellable").

### `vehicle_type → product_type` mapping table

| API `vehicle_type` | Bot product bucket | Count in current deck |
|---|---|---|
| `MF`        | `mutual_fund`  | 48 |
| `AIF`       | `aif`          | 31 |
| `PMS`       | `pms`          | 42 |
| `FD`        | `fd`          |  4 |
| `Insurance` | `insurance`    | 37 |
| `Mediclaim` | `insurance`    |  5 |
| `NCD`       | `ncd_primary`  |  1 |
| _unmapped_  | dropped + logged to `security_events.kind="unmapped_vehicle_type"` | 0 |

If the SMIFS Knowledge API ever ships a new `vehicle_type`, the catalog **does not crash and does
not silently include it**: each unknown row creates one `security_events` row tagged
`unmapped_vehicle_type` and is excluded from the picker until the mapping table is extended.

### Picker UX
1. Stage 1 (existing): user picks a product tile.
2. Stage 2 (NEW): searchable vehicle dropdown — focused vehicles float to top with a ★ marker.
3. On pick: the product-specific identity field auto-fills and locks read-only:
   - `mutual_fund` → `scheme_name` (and `amc_name`)
   - `aif` → `aif_name`
   - `pms` → `strategy_name`
   - `fd` → `issuer_name`
   - `insurance` → `carrier`
   - `ncd_primary` → `issuer_name`
4. Empty deck for the chosen bucket → form is blocked with a graceful "No <product> in current deck —
   contact RM/Sales Ops" message. **No free-text fallback.**

### Cross-type enforcement (server-side)
`POST /api/sales` rejects a payload whose `vehicle_id` belongs to a different product bucket than
`form_type` with HTTP **400 vehicle_id belongs to product_type='X' but form_type='Y'`. Tampered
requests can't bind off-bucket vehicles.

### MF ARN Transfer (tag, not new product)
- Toggle: a single `data-testid="mf-arn-transfer-toggle"` checkbox at the top of the MF form (after
  a vehicle has been picked).
- When ON, the body swaps to:
  | Field | Validation |
  |---|---|
  | `existing_arn` | regex `^ARN-[A-Za-z0-9]{4,7}$` or `^[A-Za-z0-9]{4,7}$` |
  | `new_arn` | same regex, MUST differ from `existing_arn` |
  | `folio_numbers` | non-empty (comma-separated allowed) |
  | `amc_name`, `scheme_name` | auto-filled from picked vehicle, locked read-only |
  | `transfer_effective_date` | YYYY-MM-DD |
  | `aum_inr` | ≥ ₹1,000 |
  | `arn_remarks` | optional, ≤ 500 chars |
- Persisted as `sales_entries.subtype = "arn_transfer"` with the 7 fields nested under
  `product_details.arn_transfer`.

### Email subject (Phase 17 addition)

| Tag | Subject template |
|---|---|
| `subtype == "arn_transfer"` | `[SMIFS Sales-Ops] MF — ARN Transfer — <client_name> — ₹<aum>` |
| Optional env routing override | `TO_EMAIL_MF_ARN_TRANSFER` — if set, ARN-tagged sales route there instead of `TO_EMAIL_MUTUAL_FUND`; if unset, fall through to the standard MF inbox. |

### Admin Sales Pipeline
- New column **Vehicle** (placed between Product and Client), truncated at 32 chars with full-name tooltip.
- ARN-tagged rows show a small purple `ARN` pill next to the Product label and (when opened) in the drawer header.
- New filter checkbox **ARN Transfer only** visible when the product filter is empty or set to `mutual_fund`.
- Drawer flattens the nested `arn_transfer` sub-object into the specifics grid.

### Backwards compatibility
- Legacy `sales_entries` rows (pre-Phase 17) have no `vehicle_id` / `subtype`. These continue to list
  with an em-dash in the Vehicle column. We do **not** mass-migrate; the constraint is enforced
  on new submissions only.
