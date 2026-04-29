"""Phase 6 Identity module — OrgLens API client + privacy helpers.

Privacy guardrails enforced here (not optional):
  * PAN plaintext NEVER persisted to MongoDB. Only HMAC-SHA256 fingerprint stored.
  * sanitize_for_log() must be applied to any text that may have a PAN before
    it's emitted to a log line.
  * Stored identity blobs strip Aadhaar, full bank/account details, full address,
    and personal mobile — keep only what's needed to personalize replies and
    emit the role-specific verified card.
"""
from __future__ import annotations
import hashlib
import hmac
import logging
import os
import re
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# ---- regexes ----
_PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")
_PAN_FULLMATCH_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_PAN_SCRUB_RE = re.compile(r"\b([A-Za-z]{5}[0-9]{4}[A-Za-z])\b")  # case-insensitive scrubber

_EMAIL_RE = re.compile(r"\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")
_SMIFS_DOMAIN_RE = re.compile(r"@(?:[a-z0-9-]+\.)*(?:smifs|pesmifs)\.com$", re.I)

# UCCs in the OrgLens dataset are pure digit runs. Range observed: 4–8 digits.
_UCC_TOKEN_RE = re.compile(r"\b(\d{4,8})\b")

_EMPLOYEE_HINT_RE = re.compile(
    r"\b(employee|staff|colleague|i\s*work\s*at\s*smifs|i\'?m\s*(?:an?\s*)?smifs)\b",
    re.I,
)
_CLIENT_HINT_RE = re.compile(
    r"\b(client|ucc|client\s*code|account\s*number|my\s*portfolio|my\s*holdings|"
    r"i\'?m\s*(?:a|an)\s*client|i\s*am\s*(?:a|an)\s*client)\b",
    re.I,
)
_VERIFY_HINT_RE = re.compile(
    r"\b(verify\s*(?:me|my\s*(?:identity|account))|log\s*(?:me\s*)?in|"
    r"authenticate\s*(?:me)?|sign\s*in)\b",
    re.I,
)

# ---- env ----
ORGLENS_BASE_URL = (os.environ.get("ORGLENS_BASE_URL") or "").rstrip("/")
ORGLENS_API_KEY = os.environ.get("ORGLENS_API_KEY") or ""

# HMAC key combines two server-side secrets so a leaked DB alone is insufficient
# to brute-force PAN→fingerprint.
_HMAC_KEY = (
    (os.environ.get("LLMHUB_API_KEY", "") + "|" + ORGLENS_API_KEY).encode("utf-8")
    or b"smifs-fallback-pan-hmac-key"
)

# Phase 7 — identity-keyed rehydration. Dedicated secret so operators can
# rotate independently of the LLM / OrgLens keys. Falls back to the combined
# PAN HMAC key when unset (logged as a warning by server.startup).
IDENTITY_HASH_SECRET = os.environ.get("IDENTITY_HASH_SECRET") or ""
_IDENTITY_KEY = (IDENTITY_HASH_SECRET.encode("utf-8") if IDENTITY_HASH_SECRET else _HMAC_KEY)


# =========================
# PAN privacy helpers
# =========================
def normalize_pan(s: str) -> str:
    return (s or "").strip().upper().replace(" ", "")


def is_valid_pan(s: str) -> bool:
    return bool(_PAN_FULLMATCH_RE.match(normalize_pan(s)))


def pan_hash(pan: str) -> str:
    """HMAC-SHA256 fingerprint of the normalized PAN. Never reversible."""
    norm = normalize_pan(pan)
    return hmac.new(_HMAC_KEY, norm.encode("utf-8"), hashlib.sha256).hexdigest()


# =========================
# Identity-key hashes for rehydration lookup (Phase 7)
# =========================
def _identity_hmac(value: str) -> str:
    if not value:
        return ""
    return hmac.new(_IDENTITY_KEY, value.encode("utf-8"), hashlib.sha256).hexdigest()


def email_hash(email: Optional[str]) -> str:
    if not email:
        return ""
    return _identity_hmac(email.strip().lower())


def phone_hash(phone: Optional[str]) -> str:
    """Normalize to last-10-digits (Indian mobile convention) and HMAC."""
    if not phone:
        return ""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) > 10:
        digits = digits[-10:]
    if len(digits) < 10:
        return ""
    return _identity_hmac(digits)


def emp_id_hash(eid: Optional[str]) -> str:
    if not eid:
        return ""
    return _identity_hmac(eid.strip().upper())


def ucc_hash(ucc: Optional[str]) -> str:
    if not ucc:
        return ""
    return _identity_hmac(str(ucc).strip())


def mask_email_display(email: Optional[str]) -> str:
    if not email or "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    return f"{local[:2]}***@{domain}"


def mask_phone_display(phone: Optional[str]) -> str:
    if not phone:
        return ""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


def mask_pan(pan: str) -> str:
    n = normalize_pan(pan)
    if not is_valid_pan(n):
        return "XXXXX####X"
    return f"XXXXX{n[5:9]}X"


def redact_pan_in_text(text: str) -> str:
    """Replace any PAN-shaped tokens in `text` with their masked form
    (preserves last 4 digits)."""
    if not text:
        return text
    return _PAN_SCRUB_RE.sub(lambda m: mask_pan(m.group(1).upper()), text)


# Phase 8 — email + phone redaction for persisted conversation text
_PHONE_SCRUB_RE = re.compile(r"(?<!\d)(\+?\d{1,3}[-\s]?)?(\d{10})(?!\d)")


def _email_mask_cb(m: re.Match) -> str:
    local = m.group(1)
    domain = m.group(2)
    # Preserve first 2 chars of local + full domain → "aa***@smifs.com"
    head = local[:2] if len(local) >= 2 else local
    return f"{head}***@{domain}"


def _phone_mask_cb(m: re.Match) -> str:
    cc = (m.group(1) or "").strip()
    ten = m.group(2)
    return f"{cc}*****{ten[-4:]}"


def redact_email_in_text(text: str) -> str:
    if not text:
        return text
    return re.sub(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b", _email_mask_cb, text)


def redact_phone_in_text(text: str) -> str:
    if not text:
        return text
    return _PHONE_SCRUB_RE.sub(_phone_mask_cb, text)


def redact_pii_in_text(text: str) -> str:
    """Run all persistence-time PII scrubs: PAN, email, phone."""
    if not text:
        return text
    return redact_phone_in_text(redact_email_in_text(redact_pan_in_text(text)))


def sanitize_for_log(text: str) -> str:
    """Aggressive scrub for log lines: any PAN-shaped token → XXXXX####X."""
    if not text:
        return text
    return _PAN_SCRUB_RE.sub("XXXXX####X", text)


def extract_pan(message: str) -> Optional[str]:
    if not message:
        return None
    # Search the raw message (uppercased) — do NOT strip whitespace, otherwise
    # word-boundary assertions in _PAN_RE break for surrounded tokens.
    m = _PAN_RE.search((message or "").upper())
    return m.group(1) if m else None


# =========================
# Identifier extraction
# =========================
def extract_email(message: str) -> Optional[str]:
    m = _EMAIL_RE.search(message or "")
    return m.group(1) if m else None


def extract_smifs_email(message: str) -> Optional[str]:
    e = extract_email(message)
    if e and _SMIFS_DOMAIN_RE.search(e):
        return e.lower()
    return None


def extract_ucc(message: str, require_client_context: bool = False) -> Optional[str]:
    """Extract a plausible UCC (4–8 digit run). Skips long phone-like runs.
    When require_client_context=True, only fires if the message also contains
    a client-y hint (avoids treating arbitrary numbers as UCCs)."""
    if not message:
        return None
    if require_client_context and not _CLIENT_HINT_RE.search(message):
        return None
    # Reject if the message contains a 10+ digit run (likely phone)
    long_runs = re.findall(r"\d{10,}", message)
    if long_runs and not _CLIENT_HINT_RE.search(message):
        return None
    for d in _UCC_TOKEN_RE.findall(message):
        # skip leading-zero runs (likely zip/year-padding) and obvious 4-digit years
        if d.startswith("0"):
            continue
        if len(d) == 4 and d.startswith(("19", "20")):
            continue
        return d
    return None


def derive_first_name_from_email(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    handle = email.split("@", 1)[0]
    # Split on common separators; first non-trivial token wins.
    for tok in re.split(r"[._\-+]", handle):
        tok = re.sub(r"\d+$", "", tok).strip()
        if len(tok) >= 2 and tok.isalpha():
            return tok.capitalize()
    return None


def detect_role_intent(message: str) -> Optional[str]:
    """Return 'employee' | 'client' | 'ambiguous_verify' | None."""
    if not message:
        return None
    if extract_smifs_email(message):
        return "employee"
    if _EMPLOYEE_HINT_RE.search(message):
        return "employee"
    if _CLIENT_HINT_RE.search(message):
        return "client"
    if _VERIFY_HINT_RE.search(message):
        return "ambiguous_verify"
    return None


# =========================
# OrgLens API client
# =========================
class OrgLensError(Exception):
    pass


class OrgLensForbidden(OrgLensError):
    pass


class OrgLensConfigError(OrgLensError):
    pass


def _check_config() -> None:
    if not ORGLENS_BASE_URL or not ORGLENS_API_KEY:
        raise OrgLensConfigError("OrgLens not configured (missing ORGLENS_BASE_URL or ORGLENS_API_KEY).")


def _headers() -> Dict[str, str]:
    return {"X-API-Key": ORGLENS_API_KEY, "Accept": "application/json"}


async def lookup_employee_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Returns the raw `employee` record on 200, None on 404. Raises OrgLensForbidden on 403."""
    _check_config()
    url = httpx.URL(f"{ORGLENS_BASE_URL}/employee/by-email/{email.strip()}")
    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.get(url, headers=_headers())
    if r.status_code == 404:
        return None
    if r.status_code == 403:
        raise OrgLensForbidden(f"403: {r.text[:160]}")
    r.raise_for_status()
    return (r.json() or {}).get("employee")


async def lookup_client_by_ucc(ucc: str) -> Optional[Dict[str, Any]]:
    _check_config()
    url = httpx.URL(f"{ORGLENS_BASE_URL}/client/by-ucc/{ucc.strip()}")
    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.get(url, headers=_headers())
    if r.status_code == 404:
        return None
    if r.status_code == 403:
        raise OrgLensForbidden(f"403: {r.text[:160]}")
    r.raise_for_status()
    return (r.json() or {}).get("client")


async def probe_permissions() -> Dict[str, Any]:
    """One-shot helper to verify our key's scope (used by /api/health and startup)."""
    _check_config()
    url = httpx.URL(f"{ORGLENS_BASE_URL}/permissions")
    async with httpx.AsyncClient(timeout=10.0) as cli:
        r = await cli.get(url, headers=_headers())
    r.raise_for_status()
    return r.json()


# =========================
# Storage sanitizers
# =========================
# Phase 8.1 — Fields we STRIP from `identity.raw` (never persisted).
# Narrowed to truly sensitive credentials — email/phone/DOB/hrbp_email/etc
# stay in raw so the chat LLM's USER_PROFILE can answer self-queries.
# The persistence-time PII scrubber (redact_pii_in_text) still masks these
# values anywhere they appear in user-typed message text.
_RAW_STRIP_FIELDS = {
    "pan", "pan_number",                       # PAN — HMAC fingerprint only
    "aadhar_no", "aadhar", "aadhaar",          # government ID
    "bank", "bank_details", "bank_account",    # raw bank account info
    "account",                                 # demat / bank account number
}


def _strip_sensitive_for_raw(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in (rec or {}).items() if k not in _RAW_STRIP_FIELDS}


def sanitize_employee_for_storage(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Curated top-level fields + the full record under `raw` (PAN/bank stripped).
    Curated = what the cards and the LLM context block actually consume."""
    if not rec:
        return {}
    raw = _strip_sensitive_for_raw(rec)
    name = (rec.get("name") or " ".join(
        s for s in (rec.get("first_name"), rec.get("last_name")) if s
    )).strip()
    first = (rec.get("first_name") or "").strip() or (name.split()[0] if name else "")
    return {
        "type": "employee",
        "user_id": rec.get("user_id"),
        "employee_id": rec.get("employee_id"),
        "name": name,
        "first_name": first,
        "last_name": rec.get("last_name"),
        "designation": rec.get("designation"),
        "department": rec.get("department"),
        "business_unit": rec.get("business_unit"),
        "company": rec.get("company"),
        "location": rec.get("location"),
        "location_type": rec.get("location_type"),
        "office_location_code": rec.get("office_location_code"),
        "employment_status": rec.get("employment_status"),
        "employee_type": rec.get("employee_type"),
        "confirmation_status": rec.get("confirmation_status"),
        "date_of_joining": rec.get("date_of_joining"),
        "date_of_confirmation": rec.get("date_of_confirmation"),
        "current_experience": rec.get("current_experience"),
        "reports_to_name": rec.get("reports_to_name") or rec.get("hod_name"),
        "reports_to_email_display": mask_email_display(rec.get("reports_to_email") or rec.get("hod_email")),
        "reports_to_employee_id": rec.get("reports_to_employee_id") or rec.get("hod_employee_id"),
        "direct_reports_count": rec.get("direct_reports_count"),
        "total_team_size": rec.get("total_team_size"),
        "hrbp_name": rec.get("hrbp_name"),
        "hrbp_email_display": mask_email_display(rec.get("hrbp_email")),
        "email_display": mask_email_display(rec.get("email")),
        "gender": rec.get("gender"),
        "on_notice": rec.get("on_notice"),
        "is_absconding": rec.get("is_absconding"),
        "synced_at": rec.get("synced_at"),
        "raw": raw,
    }


def sanitize_client_for_storage(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Curated top-level fields + the full record under `raw` (PAN/Aadhaar/bank
    account stripped). The OrgLens client record contains no top-level
    `client_name` field — `first_name` is heuristically derived from the
    registered email when available."""
    if not rec:
        return {}
    raw = _strip_sensitive_for_raw(rec)
    email = (rec.get("email") or "").strip() or None
    first = derive_first_name_from_email(email)

    # Trading segments: keep all "Yes" markers so the FE can pill them.
    SEG_KEYS = ("nse", "bse", "pms", "nfo", "bfo", "mcfo", "ncfo", "cnfo",
                "icfo", "cbfo", "nbfc", "bmfs", "nmfs", "nsel", "cse",
                "mxeq", "mxfo")
    segments = {k: rec.get(k) for k in SEG_KEYS if rec.get(k)}
    return {
        "type": "client",
        "ucc": rec.get("ucc"),
        "name": rec.get("client_name"),  # may be absent in current schema
        "first_name": first,
        "email_display": mask_email_display(email),
        "telephone_display": mask_phone_display(rec.get("telephone")),
        "gender": rec.get("gender"),
        "status": rec.get("status"),
        "branch_name": rec.get("dp_name"),
        "branch_code": rec.get("dp_id"),
        "sub_broker_code": rec.get("sub_broker_code"),
        "sub_broker_name": rec.get("sub_broker_name"),
        "rm_code": rec.get("rm_code"),
        "rm_name": rec.get("rm_name"),
        "rm_email_display": mask_email_display(rec.get("rm_email")),
        "rm_mobile_display": mask_phone_display(rec.get("rm_mobile")),
        "crm_name": rec.get("crm_name"),
        "risk_profile": rec.get("risk_profile"),
        "city": rec.get("city"),
        "state": rec.get("state"),
        "occupation": rec.get("occupation"),
        "income_range": rec.get("income_range"),
        "active_date": rec.get("active_date"),  # may be absent
        "kra_done_on": rec.get("kra_done_on"),  # may be absent — newer records
        "poa": rec.get("poa"),
        "segments": segments,
        "raw": raw,
    }


# =========================
# System-prompt context blocks (rich)
# =========================
def employee_context_block(identity_obj: Dict[str, Any]) -> str:
    """Phase 8.1 — emit a compact JSON USER_PROFILE dump so the chat LLM can
    answer any self-profile question directly without a tool call.

    Source = curated top-level fields ∪ identity.raw (already stripped of
    PAN/Aadhaar/bank/account at verification time). Nothing here is
    persisted per-turn — this is an ephemeral system-prompt injection.
    """
    import json as _json
    raw = dict(identity_obj.get("raw") or {})
    # Merge curated top-level fields on TOP of raw so display-masked emails etc.
    # don't override the plaintext raw values the LLM needs.
    merged: Dict[str, Any] = {}
    merged.update(raw)
    for k, v in (identity_obj or {}).items():
        if k == "raw":
            continue
        # Don't overwrite plaintext email/phone from raw with *_display fakes.
        if k.endswith("_display") and k[:-8] in merged:
            continue
        if v is not None and v != "":
            merged[k] = v

    # Derive a numeric tenure in years if current_experience is a string like
    # "1 years 1 months" — helps the LLM answer "how long have I been here?"
    ce = merged.get("current_experience") or ""
    if isinstance(ce, str) and ce:
        import re as _re
        y = _re.search(r"(\d+)\s*year", ce)
        m = _re.search(r"(\d+)\s*month", ce)
        years = int(y.group(1)) if y else 0
        months = int(m.group(1)) if m else 0
        merged["current_experience_years"] = round(years + months / 12.0, 2)

    first = merged.get("first_name") or "the colleague"
    compact = _json.dumps(merged, default=str, ensure_ascii=False, separators=(",", ": "))

    return (
        "\n\n--- VERIFIED EMPLOYEE · USER_PROFILE ---\n"
        f"USER_PROFILE = {compact}\n"
        "--- END USER_PROFILE ---\n"
        "INSTRUCTIONS FOR THIS USER:\n"
        f"- Open with a respectful peer salutation using the first name (e.g. 'Hi {first},').\n"
        "- When the user asks ANYTHING about THEMSELVES — their employee id, designation, "
        "department, manager, HRBP, team, location, office, tenure, date of joining, "
        "confirmation status, notice status, employment status, compensation, CTC, cost "
        "centres, shift, weekly off, email on record, phone on record, etc. — the answer "
        "is in USER_PROFILE. Answer DIRECTLY from that object, citing the specific field "
        "value. Be concise and warm.\n"
        "- NEVER say 'I don't have that information about you' when the field exists in USER_PROFILE.\n"
        "- NEVER punt to a directory lookup for self-queries.\n"
        "- NEVER fabricate values. If a field is missing or null in USER_PROFILE, say so honestly.\n"
        "- For questions about OTHER people or org-wide queries, the router will have already "
        "dispatched a directory_* tool; you will see the structured result in context.\n"
        "- PAN, Aadhaar, bank account numbers are intentionally NOT in USER_PROFILE. If asked, "
        "state that those details aren't accessible here for security reasons.\n"
    )


def client_context_block(identity_obj: Dict[str, Any]) -> str:
    first = identity_obj.get("first_name") or "valued investor"
    rm = identity_obj.get("rm_name") or "your relationship manager"
    risk = identity_obj.get("risk_profile") or "your risk profile"
    parts = [
        f"First name (heuristic, derived from registered email): {first}",
        f"UCC: {identity_obj.get('ucc')} · Status: {identity_obj.get('status')}",
        f"Risk profile: {risk}",
    ]
    branch = identity_obj.get("branch_name")
    if branch:
        bline = f"Branch: {branch}"
        if identity_obj.get("branch_code"):
            bline += f" ({identity_obj.get('branch_code')})"
        parts.append(bline)
    rm_line = f"Relationship Manager: {rm}"
    if identity_obj.get("rm_code"):
        rm_line += f" ({identity_obj.get('rm_code')})"
    if identity_obj.get("rm_email"):
        rm_line += f" · {identity_obj.get('rm_email')}"
    if identity_obj.get("rm_mobile"):
        rm_line += f" · {identity_obj.get('rm_mobile')}"
    parts.append(rm_line)
    if identity_obj.get("sub_broker_name"):
        parts.append(f"Sub-broker: {identity_obj.get('sub_broker_name')}")
    seg_yes = [k.upper() for k, v in (identity_obj.get("segments") or {}).items() if v == "Yes"]
    if seg_yes:
        parts.append(f"Active segments: {', '.join(seg_yes)}")
    if identity_obj.get("city") or identity_obj.get("state"):
        region = " · ".join(s for s in (identity_obj.get("city"), identity_obj.get("state")) if s)
        parts.append(f"Region: {region}")
    if identity_obj.get("occupation"):
        parts.append(f"Occupation: {identity_obj.get('occupation')}")
    if identity_obj.get("active_date"):
        parts.append(f"Active since: {identity_obj.get('active_date')}")
    return (
        "\n\n--- VERIFIED CLIENT CONTEXT ---\n"
        + "\n".join(parts)
        + f"\nPERSONALIZATION RULE: Address the client by '{first}' once at the start. "
        "Reference 1–2 specific facts from above (RM by name, risk profile, active "
        "segments, region) naturally in the reply — warm and professional, not clingy. "
        f"When suggesting next-best actions or escalating, refer them to {rm} for "
        "execution. Do not invent specific holdings, NAVs, or transactions; for portfolio "
        "specifics, offer to involve their RM."
        "\n--- END CLIENT CONTEXT ---"
    )
