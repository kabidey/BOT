# OrgLens Response Samples (PAN redacted)

Captured live from the OrgLens API on 2026-04-29 with our key
(`employees:pii`, `employees:compensation`, `clients:pii`, `clients:financial`).

These samples document **exactly** which fields the API returns so we can decide
which to surface in the verified card, the LLM context, and the persisted
`identity.raw` blob. All PAN values have been redacted to `XXXXX####X`.

---

## 1. `GET /employee/by-email/aaditya.jaiswal@smifs.com`

```json
{
  "employee": {
    "user_id": "1024761",
    "confirmation_status": "Confirmed",
    "date_of_joining": "03-03-2025",
    "department": "Institutional Equities",
    "designation": "Research Associate",
    "direct_reports_count": 0,
    "email": "aaditya.jaiswal@smifs.com",
    "employee_id": "SMWM-25031054",
    "employment_status": "Active",
    "last_working_day": "",
    "location": ", (Branch)",
    "manager_mongo_id": "a68feea8fcd56a",
    "mobile_number": "9594549293",
    "mongo_id": "a68feeab433ba6",
    "name": "Aaditya Rajesh Jaiswal",
    "on_notice": false,
    "on_notice_text": "",
    "pan_number": "XXXXX8323X",
    "phone": "",
    "reports_to_name": "Awanish Chandra",
    "reports_to_user_id": "1024740",
    "synced_at": "2026-04-28T03:17:24.431000",
    "total_team_size": 0,
    "date_of_confirmation": "30-08-2025",
    "has_children": false,
    "personal_mobile": "9594549293",
    "probation_period": "180 Days Probation Period",
    "profile_pic": true,
    "assigned_shift": "8:30 to 5:30",
    "assigned_weekly_off": "Saturday_Sunday (All Saturday, All Sunday)",
    "bank_details": "<<REDACTED>>",
    "bu_cost_center_id": "Capital Markets & Advisory 001",
    "bu_cost_center_name": "Capital Markets & Advisory 001",
    "business_unit": "Capital Markets & Advisory",
    "checkin_access": true,
    "company": "SMIFS LIMITED",
    "current_experience": "1 years 1 months",
    "date_of_birth": "11-01-2000",
    "department_code": "DEP_2",
    "dept_cost_center_id": "Capital Markets & Advisory 001",
    "dept_cost_center_name": "Capital Markets & Advisory 001",
    "designation_code": "(001_SALES_IE_INSTRERCH_ASSO_RA)",
    "emp_cost_center_id": "FIN001",
    "emp_cost_center_name": "Banking and Finance Cost",
    "employee_type": "Permanent",
    "first_name": "Aaditya",
    "fixed_ctc": "1400004",
    "gender": "Male",
    "hod_email": "awanish.chandra@smifs.com",
    "hod_employee_id": "SMWM-2107631",
    "hod_name": "Awanish Chandra",
    "hrbp_email": "kabita.banerjee@smifs.com",
    "hrbp_employee_id": "SMWM-1903185",
    "hrbp_name": "Kabita Banerjee",
    "is_absconding": false,
    "last_name": "Jaiswal",
    "location_pincode": "400051",
    "location_type": "Branch",
    "mobile_access": true,
    "office_location_code": "BKC_TC_1",
    "personal_mobile_number": "9594549293",
    "physically_handicapped": false,
    "probation_period_days": "180 Day(s)",
    "professional_tax_state": "Maharashtra",
    "reactivated_employee": false,
    "reports_to_email": "awanish.chandra@smifs.com",
    "reports_to_employee_id": "SMWM-2107631",
    "salary_structure": "Salary Structure",
    "timezone": "(UTC+05:30) Chennai, Kolkata, Mumbai, New Delhi",
    "total_ctc": "1400004",
    "band": "N.A.",
    "grade": "N.A."
  }
}
```

**Total fields returned: 65**
**Stripped before persistence in `identity.raw`:** `pan_number`, `bank_details`
(plus `aadhar_no` / `account` / `bank` if they appear in any record).

---

## 2. `GET /client/by-ucc/63876`

```json
{
  "client": {
    "ucc": "63876",
    "aadhar_no": "<<REDACTED>>",
    "account": "<<REDACTED>>",
    "address1": "C 76 (2), BANK COLONY- 1",
    "address2": "GATE BAZAR, BRAHMAPUR SADAR",
    "address3": "BERHAMPUR (GM)",
    "bank": "<<REDACTED>>",
    "bfo": "Yes",
    "bmfs": "No",
    "bse": "Yes",
    "cbfo": "Yes",
    "city": "GANJAM-760001",
    "cnfo": "Yes",
    "crm_name": "SUDHIR KUMAR PATRA [BP299]",
    "cse": "No",
    "dp_id": "IN301629",
    "dp_name": "SMIFS LIMITED",
    "email": "balaram.patro143@gmail.com",
    "gender": "MALE",
    "icfo": "Yes",
    "income_range": "Rs. 500000 - 1000000",
    "mcfo": "Yes",
    "mxeq": "No",
    "mxfo": "No",
    "nbfc": "No",
    "ncfo": "Yes",
    "nfo": "Yes",
    "nmfs": "No",
    "notes_remarks": "BranchId: BHB2 changed to: BHUB on 01/08/25 ... KRA DONE ON: 25-09-2019 ...",
    "nse": "Yes",
    "nsel": "No",
    "occupation": "Business",
    "pan": "XXXXX3602X",
    "pms": "No",
    "poa": "yes",
    "risk_profile": "Medium Risk",
    "rm_code": "SMIFSSR259",
    "rm_name": "JITEN SAHOO",
    "state": "Odisha",
    "status": "Active",
    "sub_broker_code": "BP299",
    "sub_broker_name": "SUDHIR KUMAR PATRA [BP299]",
    "telephone": "9238040800"
  }
}
```

**Total fields returned: 41**
**Stripped before persistence in `identity.raw`:** `pan`, `aadhar_no`, `account`,
`bank` (financial credentials).
**Notable absences from this dataset:** no top-level `client_name`, no
`active_date`, no `rm_email` / `rm_mobile`, no `nominee_*`, no
`demat_account`, no `aum` / `turnover`. The card and the LLM context render
those fields conditionally so newer records that include them light up
automatically without code changes.

---

## How these are used downstream

| Surface | Source |
|---|---|
| `sessions.identity` (curated top-level fields) | hand-picked subset of the response — fields actually used by the bot |
| `sessions.identity.raw` | full response **minus** sensitive credentials (PAN, Aadhaar, bank account, raw bank string) |
| LLM `USER CONTEXT` block | curated fields wrapped in a structured summary + personalization rule |
| `employee_card` / `client_card` UI | curated fields, conditional rendering for missing fields |

PAN is **never** persisted plaintext. The verification flow holds it in memory
only (HMAC-SHA256 hash kept on the session row for the comparison). All log
records pass through the `PanScrubFilter` + `SecretScrubFilter` before the
handler emits.
