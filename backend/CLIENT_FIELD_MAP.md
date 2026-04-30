# Client Field Map — SMIFS OrgLens (Phase 10)

Full inventory of fields returned by `GET /client/by-ucc/{ucc}` and
`GET /clients?...` using our `clients:read` + `clients:pii` scope.

Captured live 2026-04-30 · sample subject: UCC `63876`.
Total distinct keys returned: **60**.

## Endpoint inventory

| Path | Status | Notes |
|---|---|---|
| `GET /clients` | ✅ 200 | Accepts `limit`, `skip`. Total = **30928**. |
| `GET /clients/stats` | ✅ 200 | Total / active / closed / suspended + breakdown by state. |
| `GET /client/by-ucc/{ucc}` | ✅ 200 | Primary lookup. Full 60-field record. |
| `GET /client/by-pan/{pan}` | ❌ 404 | Not implemented. PAN search must use our internal `pan_hash` index. |
| `GET /client/by-ucc/{ucc}/holdings` | ❌ 404 | No holdings endpoint. |
| `GET /client/by-ucc/{ucc}/transactions` | ❌ 404 | No transactions endpoint. |
| `GET /client/by-ucc/{ucc}/portfolio` | ❌ 404 | |
| `GET /client/by-ucc/{ucc}/kyc` | ❌ 404 | KYC fields live inside the main record. |
| `GET /client/by-ucc/{ucc}/nominee` | ❌ 404 | Nominee not exposed; use `father_name` at most. |

**Implication for tools**: there is no separate holdings / transactions surface to
query. `CLIENT_PROFILE` system-prompt injection is the complete answer surface
for client Q&A. No per-tool Router wiring for clients beyond the existing
`lookup_client` intent.

## 1. Identity / personal
| Field (API key) | Normalised | Notes |
|---|---|---|
| `clientid` | `clientid` | internal id |
| `ucc` | `ucc` | Unique Client Code |
| `client_x000d__name` | `client_name` | full name; `_x000d__` is a Windows CRLF artefact |
| `gender` | `gender` | |
| `birth_x000d__date` | `birth_date` | DOB |
| `father_x000d__name` | `father_name` | |
| `pan` | **STRIPPED** | HMAC `pan_hash` only |
| `aadhar_no` | **STRIPPED** | government ID |

## 2. Contact
| Field | Normalised | Notes |
|---|---|---|
| `email` | `email` | plaintext in `raw`; masked in `email_display` |
| `mobile` | `mobile` | |
| `telephone` | `telephone` | |
| `address1` / `address2` / `address3` | same | |
| `city`, `state` | same | |

## 3. Bank / demat — STRIPPED
| Field | Strip? |
|---|---|
| `bank` | ✅ stripped |
| `bank_x000d__actype` | ✅ |
| `bank_x000d__city` | ✅ |
| `bank_x000d__micr` | ✅ |
| `bank_x000d__rtgs` | ✅ |
| `account` | ✅ |
| `dp_id` | kept — informational |
| `dp_name` | kept — informational |

## 4. Status / timeline
| Field | Normalised | Notes |
|---|---|---|
| `status` | `status` | e.g. "Active" |
| `active_x000d__date` | `active_date` | |
| `suspended_x000d__date` | `suspended_date` | usually empty |
| `orginal_x000d__active_x000d__date` | `original_active_date` | |
| `client_x000d__category` | `client_category` | |

## 5. Relationship Manager
| Field | Notes |
|---|---|
| `rm_name` | ✅ always present |
| `rm_code` | ✅ always present |
| `crm_name` | CRM (customer relationship manager) |
| `sub_broker_name` / `sub_broker_code` | if sub-brokered |

**IMPORTANT**: The OrgLens client API does **NOT** return `rm_email` or
`rm_mobile`. We enrich these at verification time by searching the *employee*
directory for `rm_name` and copying over the masked email/mobile. If the RM
cannot be matched in the employee directory (external dealer, sub-broker RM
etc.), we surface `rm_name` + `rm_code` alone in the fallback message.

## 6. Trading segments (boolean "Yes"/"No" flags)
`nse`, `bse`, `pms`, `nfo`, `bfo`, `mcfo`, `ncfo`, `cnfo`, `icfo`, `cbfo`,
`nbfc`, `bmfs`, `nmfs`, `nsel`, `cse`, `mxeq`, `mxfo`.

Renders as chip set in `client_card`.

## 7. POA
| Field | Notes |
|---|---|
| `poa` | Yes/No |
| `poa_x000d__holder_x000d__name` | if POA, the holder |
| `poa_x000d__execution_x000d__date` | |

## 8. Profile
| Field | Notes |
|---|---|
| `risk_profile` | e.g. "Moderate" |
| `income_range` | |
| `occupation` | |
| `notes_remarks` | free text |

## 9. Normalised keys

At verification time we run a normaliser that:
1. Replaces `_x000d__` → `_` on every key name (Excel CRLF artefact).
2. Strips the sensitive 4 key groups above.
3. Copies the normalised record onto `identity.raw`.

## 10. CLIENT_PROFILE injection (ephemeral, per-turn)

The chat LLM receives `CLIENT_PROFILE = {…}` in its system prompt (mirroring
USER_PROFILE for employees). It contains all fields from #1 / #2 / #4 / #5
/ #6 / #7 / #8 — i.e. everything except the STRIPPED group. Persist-time
PII scrub still masks email/phone/PAN in `conversations.messages[].content`.

## 11. Fallback when CLIENT_PROFILE can't answer
```text
I don't have that information in your record. Please connect with your
Wealth Manager — <rm_name> (<rm_email>, <rm_mobile>).
```
If the RM email/mobile aren't available (external RM), fallback collapses to:
```text
Please connect with your Wealth Manager — <rm_name> (RM code <rm_code>).
```
