# Employee Field Map — SMIFS OrgLens (Phase 8.1)

Full inventory of fields returned by `GET /employee/by-email/{email}` and
`GET /employees?...` with our current scope
(`employees:pii`, `employees:compensation`).

Captured live 2026-04-29 · sample subject: `aaditya.jaiswal@smifs.com`.
Total distinct keys returned: **72**.

The column **In `identity.raw`** indicates whether the field is persisted on
`sessions.identity.raw` (server-side only, 30-day TTL). Fields marked
**Stripped** never touch Mongo.

The column **In USER_PROFILE** indicates whether the field is injected into
the chat LLM's system prompt so the bot can answer self-queries directly
without a tool call.

---

## 1. Identity / personal (curated + raw)
| Field | In `identity.raw` | In USER_PROFILE | Notes |
|---|---|---|---|
| `user_id` | ✅ | ✅ | Internal OrgLens id, used for my_team walker |
| `employee_id` | ✅ | ✅ | e.g. `SMWM-25031054` |
| `mongo_id` | ✅ | ❌ | Irrelevant to the user |
| `manager_mongo_id` | ✅ | ❌ | Same |
| `name` / `first_name` / `last_name` | ✅ | ✅ | — |
| `gender` | ✅ | ✅ | — |
| `date_of_birth` | ✅ | ✅ | Useful for "when is my birthday" |
| `has_children` | ✅ | ✅ | — |
| `physically_handicapped` | ✅ | ✅ | — |
| `profile_pic` | ✅ | ❌ | Just a boolean |
| `professional_tax_state` | ✅ | ✅ | — |

## 2. Contact
| Field | In `identity.raw` | In USER_PROFILE | Notes |
|---|---|---|---|
| `email` | ✅ | ✅ | Plaintext in ephemeral system prompt only |
| `phone` | ✅ | ✅ | — |
| `mobile_number` | ✅ | ✅ | — |
| `personal_mobile` | ✅ | ✅ | — |
| `personal_mobile_number` | ✅ | ✅ | — |

## 3. Employment status
| Field | In `identity.raw` | In USER_PROFILE | Notes |
|---|---|---|---|
| `employment_status` | ✅ | ✅ | Active / Inactive |
| `employee_type` | ✅ | ✅ | Permanent / Contract |
| `confirmation_status` | ✅ | ✅ | Confirmed / Probation |
| `is_absconding` | ✅ | ✅ | — |
| `on_notice` | ✅ | ✅ | — |
| `on_notice_text` | ✅ | ✅ | Free text reason |
| `last_working_day` | ✅ | ✅ | — |
| `reactivated_employee` | ✅ | ✅ | — |

## 4. Timeline / tenure
| Field | In `identity.raw` | In USER_PROFILE | Notes |
|---|---|---|---|
| `date_of_joining` | ✅ | ✅ | DD-MM-YYYY |
| `date_of_confirmation` | ✅ | ✅ | — |
| `current_experience` | ✅ | ✅ | "1 years 1 months" |
| `probation_period` | ✅ | ✅ | Text |
| `probation_period_days` | ✅ | ✅ | — |
| `synced_at` | ✅ | ✅ | Record freshness |

## 5. Org hierarchy
| Field | In `identity.raw` | In USER_PROFILE | Notes |
|---|---|---|---|
| `department` / `department_code` | ✅ | ✅ | — |
| `designation` / `designation_code` | ✅ | ✅ | — |
| `business_unit` | ✅ | ✅ | — |
| `company` | ✅ | ✅ | — |
| `band` / `grade` | ✅ | ✅ | Often "N.A." |
| `reports_to_name` | ✅ | ✅ | — |
| `reports_to_employee_id` | ✅ | ✅ | — |
| `reports_to_email` | ✅ | ✅ | — |
| `reports_to_user_id` | ✅ | ✅ | For my_team walker |
| `hod_name` | ✅ | ✅ | Often == reports_to_name |
| `hod_employee_id` | ✅ | ✅ | — |
| `hod_email` | ✅ | ✅ | — |
| `hrbp_name` | ✅ | ✅ | — |
| `hrbp_employee_id` | ✅ | ✅ | — |
| `hrbp_email` | ✅ | ✅ | — |
| `direct_reports_count` | ✅ | ✅ | — |
| `total_team_size` | ✅ | ✅ | — |

## 6. Location
| Field | In `identity.raw` | In USER_PROFILE | Notes |
|---|---|---|---|
| `location` | ✅ | ✅ | Comma-sep "City, State, India, (Type)" |
| `location_type` | ✅ | ✅ | Branch / HO / Corporate |
| `location_pincode` | ✅ | ✅ | — |
| `office_location_code` | ✅ | ✅ | e.g. `BKC_TC_1` |
| `timezone` | ✅ | ✅ | — |
| `assigned_shift` | ✅ | ✅ | — |
| `assigned_weekly_off` | ✅ | ✅ | — |

## 7. Compensation (scope: `employees:compensation`)
| Field | In `identity.raw` | In USER_PROFILE | Notes |
|---|---|---|---|
| `fixed_ctc` | ✅ | ✅ | INR (number/string) |
| `total_ctc` | ✅ | ✅ | — |
| `salary_structure` | ✅ | ✅ | Free text |

## 8. Cost centres
| Field | In `identity.raw` | In USER_PROFILE | Notes |
|---|---|---|---|
| `bu_cost_center_id` / `bu_cost_center_name` | ✅ | ✅ | — |
| `dept_cost_center_id` / `dept_cost_center_name` | ✅ | ✅ | — |
| `emp_cost_center_id` / `emp_cost_center_name` | ✅ | ✅ | — |

## 9. Access flags
| Field | In `identity.raw` | In USER_PROFILE | Notes |
|---|---|---|---|
| `mobile_access` | ✅ | ✅ | Boolean |
| `checkin_access` | ✅ | ✅ | Boolean |

## 10. STRIPPED (never leaves OrgLens at verification)
These never make it into `identity.raw` or USER_PROFILE or any log:

| Field | Reason |
|---|---|
| `pan_number` | Verified via HMAC-SHA256 only |
| `aadhar_no` / `aadhar` / `aadhaar` | Government ID |
| `bank` / `bank_details` / `bank_account` | Financial credentials |
| `account` | Demat / bank account |

---

## Usage contract

- **USER_PROFILE** (ephemeral, per-turn system prompt): full JSON dump of
  `identity.raw` minus the STRIPPED list. Email + phone allowed here so
  the bot can answer "what's my work email?" type questions. **Not persisted**
  as part of the conversation — PII scrub on user turns still applies.
- **`sessions.identity.raw`** (30-day TTL): same subset as USER_PROFILE.
- **`conversations.messages[].content`** (user turns): PAN + email + phone
  scrubbed via `identity.redact_pii_in_text`.
- **`conversations.messages[].content`** (assistant turns): PAN-only scrub —
  the assistant answers using USER_PROFILE and will echo things like
  "your email on record is aaditya.jaiswal@smifs.com" — that's acceptable
  because the user themselves just verified and the thread is identity-bound.
- **`session_archives`**: pulls curated `identity_summary` only — does not
  copy `identity.raw`.
