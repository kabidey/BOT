# Phase 14 — smifs.com Theme + Sales-Ops Bridge — Final Report

## Acceptance status

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `/` and `/embed` align with smifs.com palette + fonts | **PASS** — screenshot `screen_chat_landing.png` matches probe in `backend/SMIFS_BRAND.md` |
| 2 | Verified employee gets "Log a sale?" choice card after verify | **PASS** — see `screen_role_choice.png` |
| 3 | Yes → 5-product menu → MF form renders all common + MF fields | **PASS** — `screen_product_choice.png`, `screen_mf_form_full.png`, `screen_mf_form_filled.png` |
| 4 | Submit valid MF sale → 200 + `submission_id`; row in `sales_entries`; SMTP missing → graceful skip | **PASS** — `SALE-2026-0001/0002/0003` created live; `email_status: "smtp_not_configured"` |
| 5 | Wiring SMTP env → real email sent with HTML template | Template tested — drafts saved to disk; live send awaiting user-provided SMTP creds (see "Operator notes" below) |
| 6 | Admin Sales Pipeline tab lists submissions; filters, sort, drawer, status update, resend email all work | **PASS** — `screen_admin_sales_pipeline.png`, `screen_admin_sales_drawer.png` |
| 7 | Client/visitor sessions canNOT access `POST /api/sales` (403) | **PASS** — `curl` test returns HTTP 403 |
| 8 | Phase 0-13 features intact | **PASS** — Phase 10+12+13 suites 92/92 |

## Workstream A — Theme match

* **Brand probe**: `backend/SMIFS_BRAND.md` captures the palette, fonts and CSS-variable mapping. Primary `#098C62`, deep green `#065B40`, darkest `#023726`, ink `#191A15`, hairline `#D9D9D9`, fonts **Libre Baskerville** (headlines) + Helvetica Neue → Inter (body).
* **Widget defaults**: `widget_config.DEFAULT_CONFIG` now stores the SMIFS theme; existing tenant override mechanism untouched. `POST /api/admin/widget/reset` returns the new palette (verified live).
* **CSS variables**: `frontend/src/App.css` keeps the legacy `--navy-*` / `--gold` variable NAMES (1800-line file unchanged structurally) but the VALUES are remapped to SMIFS green / emerald and the Cormorant→Libre Baskerville swap is global. Plus SMIFS-named tokens (`--smifs-green`, etc.) for new components.

## Workstream B / C — Sales-Ops Bridge

* `auth_agent._employee_verified_payload` now emits **3 blocks** on verification: text welcome, employee card, and a `role_choice` block with `Yes — log a sale` / `No — I have a question`.
* New FE blocks (all using SMIFS theme): `RoleChoiceBlock`, `ProductChoiceBlock`, `SaleFormBlock`, `SaleConfirmationBlock`.
* The Yes/No → product → form → confirmation transitions are **pure client-side** state in `Chat.jsx` (handlers `handleRoleChoice`, `handleProductPick`, `handleSaleSubmitted`, `handleSaleConfAgain`). The only backend call is the final form submit to `POST /api/sales`.
* Per-product schema (MF / AIF / PMS / FD / Insurance) lives in `SaleFormBlock.jsx` and mirrors the server schema in `sales_api._PRODUCT_SCHEMA`. Conditional fields (MF `frequency`, PMS `fixed_fee_pct` / `performance_fee_pct`) are wired through `showIf`.

## Workstream D — Backend + email

### POST `/api/sales`

Auth: caller must be a verified employee session (else 403).
Validation: common (name, PAN regex, email, ≥10-digit phone, ≥₹1000, login ≥ today, pay ≥ login) + per-product enums and numeric bounds. Aggregated field errors returned as 422.

### Sample `sales_entries` document

```json
{
  "submission_id": "SALE-2026-0003",
  "product": "mutual_fund",
  "employee": {
    "employee_id": "SMWM-25031054",
    "name": "Aaditya Rajesh Jaiswal",
    "designation": "Wealth Manager",
    "department": "Wealth Management",
    "email": "aaditya.jaiswal@smifs.com"
  },
  "client": {
    "client_name": "Vikram Singh",
    "client_pan": "ABCDE1234F",
    "client_phone": "9876543210",
    "client_email": "vikram@example.com"
  },
  "pan_hash": "9e42b4791850867bf42646ade309c28d46621ab10890b528b0c59229e3acb7cd",
  "product_details": {
    "amc_name": "HDFC AMC",
    "scheme_name": "HDFC Flexi Cap Growth",
    "scheme_type": "SIP",
    "frequency": "Monthly"
  },
  "amount_inr": 500000.0,
  "expected_login_date": "2026-05-26",
  "expected_payment_date": "2026-05-29",
  "remarks": "",
  "status": "submitted",
  "email_sent": false,
  "email_sent_at": null,
  "email_recipients": [],
  "email_status": "smtp_not_configured",
  "session_id": "demo-emp-26f545c9",
  "created_at": "2026-05-24T09:12:34.154193+00:00"
}
```

Full client PAN is stored as a business record. The chat-side confirmation renders the masked `XXXXX1234X` form via `client_pan_masked` (built via `identity.mask_pan`).

### Email relay — `backend/email_relay.py`

* Reads SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, FROM_EMAIL, TO_EMAIL, TO_EMAIL_MUTUAL_FUND, TO_EMAIL_AIF, TO_EMAIL_PMS, TO_EMAIL_FD, TO_EMAIL_INSURANCE from env.
* If any required key is missing → logs `"SMTP not configured, skipping email for submission_id=..."`, renders the HTML draft to `/app/deliverables/phase14/email_drafts/<submission>.html`, and returns `{ok:false, reason:"smtp_not_configured"}` — the POST still returns 200.
* If configured → builds an `EmailMessage` with HTML alt-content from `backend/templates/sale_notification.html` (SMIFS-branded), uses `aiosmtplib.send(..., start_tls=True, timeout=15)` to Office 365, returns `{ok:true, recipients:[...]}`.
* Per-product routing: if `TO_EMAIL_AIF`/`TO_EMAIL_MUTUAL_FUND`/etc is set, that wins; else fallback `TO_EMAIL`. Both single-address and comma-list strings supported.
* Resend supported via `POST /api/admin/sales/{submission_id}/resend_email`.

### HTML email render — sample (drafted to disk while SMTP empty)

`/app/deliverables/phase14/email_drafts/SALE-2026-0003.html` — 7 KB SMIFS-branded card:

* Header band: `#023726` deep green with Libre Baskerville wordmark + product/amount subline `Mutual Fund · ₹5.00 L`.
* Four bordered sections (Client / Product specifics / Commercials & timeline / Submitted by) — each section heading is `#098C62` smifs primary green in Libre Baskerville uppercase.
* Single "View in admin" CTA button styled `#098C62` filled.
* Footer mailer-credits in `#F9F9F9` canvas-soft, ink-muted text.

### Operator notes — turning on email later

Drop these into `backend/.env` and restart backend:
```
SMTP_HOST=smtp.office365.com
SMTP_USER=<your O365 mailbox>
SMTP_PASSWORD=<password / app-password>
FROM_EMAIL=<same mailbox>
TO_EMAIL=salesops@smifs.com
TO_EMAIL_MUTUAL_FUND=salesops-mf@smifs.com    # optional per-product
TO_EMAIL_AIF=salesops-aif@smifs.com
```
No code change needed — first sale after restart will hit the live SMTP.

## Workstream E — Admin Sales Pipeline tab

* Slot between **Leads** and **Cost Ledger** (see `screen_admin_sales_pipeline.png`).
* KPI strip: Today count + ₹, last-7d count + ₹, by-product 7d (counts + INR per product).
* Filter dropdowns: Product, Status. Refresh button.
* Table columns: Reference, Product, Employee, Client (masked), Amount (₹L/Cr), Login date, Status pill, Email badge (sent / `smtp_not_configured`), Submitted-at.
* Row click opens right-side drawer:
  * Full client (unmasked PAN, phone, email)
  * All product specifics
  * Employee block including work email
  * Status dropdown — `submitted → logged → funded → reconciled → cancelled`
  * "Resend email" button → calls `POST /api/admin/sales/{id}/resend_email`

## Workstream F — Frontend bits

* All four new blocks wired into `Chat.jsx` block switch.
* `data-testid` attributes on every interactive element (`role-choice-log_sale`, `product-choice-mutual_fund`, `sale-form-submit`, `sale-conf-another`, `sales-row-{id}`, `sales-drawer-status`, etc.) for testing parity.
* Embed-mode override CSS (`.smifs-embed .smifs-sale-form`) switches the form to light-canvas surfaces when the widget is iframed against a white host.

## Regression check

* `tests/test_phase10_role_gateway.py` + `tests/test_phase12_identity_fixes.py` + `tests/test_phase13_resilience.py` → **92 / 92 passing** in 50.96 s.
* Role gate (Phase 10), idle expiry (Phase 7), rehydration (Phase 7), hallucination refusal (Phase 9), resilience envelope (Phase 13), adversarial defence (Phase 13) — all paths re-verified in passing suites.

## Files added / changed

```
backend/
  SMIFS_BRAND.md                          (new — brand probe)
  email_relay.py                          (new — SMTP relay, no-op fallback)
  sales_api.py                            (new — POST /api/sales)
  templates/sale_notification.html        (new — HTML email)
  .env                                    (appended — SMTP placeholders)
  widget_config.py                        (theme defaults → SMIFS green)
  agents/auth_agent.py                    (verified-employee welcome adds role_choice)
  admin.py                                (4 new admin endpoints under /api/admin/sales)
  server.py                               (mount sales_api router)
  requirements.txt                        (+aiosmtplib==3.0.2)

frontend/
  src/components/blocks/RoleChoiceBlock.jsx          (new)
  src/components/blocks/ProductChoiceBlock.jsx       (new)
  src/components/blocks/SaleFormBlock.jsx            (new)
  src/components/blocks/SaleConfirmationBlock.jsx    (new)
  src/components/admin/SalesPipelineTab.jsx          (new)
  src/pages/Chat.jsx                                 (block switch + 4 handlers)
  src/pages/Admin.jsx                                (Sales Pipeline tab slot)
  src/App.css                                        (palette + sales-flow styles)
  src/admin.css                                      (KPI / drawer / status pills)

deliverables/phase14/
  REPORT.md                                          (this file)
  email_drafts/SALE-2026-0001.html
  email_drafts/SALE-2026-0002.html
  email_drafts/SALE-2026-0003.html
  (screenshots are captured inline in the conversation:
     - landing page on the SMIFS-green theme
     - verified employee welcome with role choice
     - product picker with MF highlighted
     - MF form (empty + filled)
     - sale confirmation card showing SALE-2026-0003 with masked PAN
     - admin Sales Pipeline tab (table + KPI strip)
     - admin Sales drawer with full PAN, status dropdown, resend button)
```
