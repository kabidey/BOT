# Phase 12 Alphanumeric-UCC Bug Fix — Verification Report

## Acceptance Criteria

| # | Criterion | Status |
|---|-----------|--------|
| 1 | UCC `D900300` (alphanumeric) resolves to the correct client | PASS |
| 2 | First name derived correctly for both `D900300` and regression target `63876` | PASS |
| 3 | `identity.raw` strips all direct PII (email, mobile, telephone, father_name, bank_*, address*, dob) | PASS |
| 4 | `_x000D_` Excel CRLF artefacts scrubbed from raw values | PASS |
| 5 | No regression on numeric UCC `63876` | PASS |
| 6 | Production `bot.pesmifs.com/api/health` reachable | PASS |
| 7 | Pytest suite green (Phase 10 + Phase 12) | PASS — 34/34 |

## Live OrgLens probe (UCC → curated client)

### `D900300` (PAN `AIJPD2750P`)
```
OrgLens.client_name     = 'SOMNATH DEY'
OrgLens.pan             = 'AIJPD2750P'
curated.name            = 'SOMNATH DEY'
curated.first_name      = 'Somnath'
curated.email_display   = 'de***@gmail.com'
curated.telephone_display = '***3000'
identity.raw key count  = 38
identity.raw clean of forbidden fields: YES
_x000D_ artefacts in raw: NONE
fields stripped: ['account','address1','address2','address3','bank','bank_actype',
                  'bank_branch','bank_city','bank_micr','bank_rtgs','birth_date',
                  'email','father_name','mobile','mobile1','pan','telephone']
```

### `63876` (PAN `ARIPP3602Q`) — regression target
```
OrgLens.client_name     = 'A BALARAM PATRO'
curated.first_name      = 'Balaram'   (single-letter 'A' initial correctly skipped)
curated.rm_name         = 'JITEN SAHOO'
curated.branch_name     = 'BHUBANESWAR'
identity.raw clean of forbidden fields: YES
```

## End-to-end chat verification (POST /api/chat)

### Flow A — D900300
```
> "I am a SMIFS client, please verify me. My UCC is D900300"
< "Thanks, Somnath. For security, please share your PAN…"

> "my PAN is AIJPD2750P"
< "Welcome back, Somnath. Your Mackertich ONE relationship is verified —
   medium risk profile, with your relationship manager at FUTURE,
   NSE + BSE + NFO active. How can I help today?"
```

### Flow B — 63876 (regression)
```
> "I am a client, my UCC is 63876"
< "Thanks, Balaram. …"

> "PAN is ARIPP3602Q"
< "Welcome back, Balaram. Your Mackertich ONE relationship is verified
   — medium risk profile, with JITEN SAHOO at BHUBANESWAR,
   NSE + BSE + NFO active. How can I help today?"
```

## Production reachability — `bot.pesmifs.com`

```
HTTP 200  (2.39 s)
{
  "status": "ok",
  "llm_reachable": true,
  "model": "llama-3.3-70b-versatile",
  "orglens_reachable": true,
  "orglens_permissions": [
    "bo:financial","bo:read","clients:financial","clients:pii","clients:read",
    "departments:read","designations:read","employees:compensation","employees:pii",
    "employees:read","locations:read","mf:financial","mf:pii","mf:read",
    "org_tree:read","stats:read"
  ],
  "rag_chunks": 1946,
  "embedder": "hub_ai"
}
```

## Code-level summary of fixes

1. **`identity._UCC_TOKEN_RE`** widened to `\b([A-Za-z]{0,2}\d{4,8})\b` so 1–2 letter
   prefixes like `D900300` / `DM12345` survive extraction; canonicalised to uppercase.
2. **`identity._derive_first_name`** now prefers the first MULTI-character token of the
   official OrgLens `client_name`, skips honorifics (`Dr`, `Mr`, `Shri`, `Smt`, …) and
   single-letter initials. Falls back to the email-handle only when no usable name
   token exists.
3. **`identity._RAW_STRIP_FIELDS`** expanded to scrub direct PII from `identity.raw`
   before it ever reaches the LLM prompt: `email`, `mobile`, `mobile1/2`, `telephone`,
   `father_name`, `mother_name`, `spouse_name`, `bank_*` (incl. IFSC, MICR, RTGS,
   branch, city), full `address1..4`, `dob`/`birth_date`. Display-masked variants
   (`email_display`, `telephone_display`) remain available on the curated object.
4. **`auth_agent`** simplified — no longer applies a "fuzzy digit" UCC fallback that
   stripped the alpha prefix; it trusts the regex output verbatim.

## Tests

* `backend/tests/test_phase12_identity_fixes.py` — 12 new cases covering the
  `_derive_first_name` and `extract_ucc` matrix above.
* `backend/tests/test_phase10_role_gateway.py` — updated for new strip rules.
* Combined: **34 / 34 passed** (47.78 s).
