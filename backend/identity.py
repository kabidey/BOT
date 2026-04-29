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
def sanitize_employee_for_storage(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Strip PAN, Aadhaar, bank, personal mobile. Keep only fields needed to
    personalize replies and render the verified employee_card."""
    if not rec:
        return {}
    name = rec.get("name") or ""
    first = (rec.get("first_name") or "").strip() or (name.split()[0] if name else "")
    return {
        "type": "employee",
        "user_id": rec.get("user_id"),
        "employee_id": rec.get("employee_id"),
        "name": name,
        "first_name": first,
        "designation": rec.get("designation"),
        "department": rec.get("department"),
        "location": rec.get("location"),
        "employment_status": rec.get("employment_status"),
        "date_of_joining": rec.get("date_of_joining"),
        "email": rec.get("email"),
        "company": rec.get("company"),
        "business_unit": rec.get("business_unit"),
        "reports_to_name": rec.get("reports_to_name"),
    }


def sanitize_client_for_storage(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Strip PAN, Aadhaar, bank, account, full address, telephone. Keep
    fields needed to render the verified client_card and personalize replies.
    Note: OrgLens client records carry no `client_name` field — `first_name`
    is heuristically derived from the registered email when available."""
    if not rec:
        return {}
    email = (rec.get("email") or "").strip() or None
    first = derive_first_name_from_email(email)
    segments = {
        k: rec.get(k)
        for k in ("nse", "bse", "pms", "nfo", "bfo", "mcfo", "ncfo", "nbfc")
        if rec.get(k)
    }
    return {
        "type": "client",
        "ucc": rec.get("ucc"),
        "name": None,  # OrgLens client schema has no name field
        "first_name": first,
        "email": email,
        "status": rec.get("status"),
        "branch_name": rec.get("dp_name"),
        "rm_code": rec.get("rm_code"),
        "rm_name": rec.get("rm_name"),
        "sub_broker_code": rec.get("sub_broker_code"),
        "sub_broker_name": rec.get("sub_broker_name"),
        "risk_profile": rec.get("risk_profile"),
        "city": rec.get("city"),
        "state": rec.get("state"),
        "occupation": rec.get("occupation"),
        "income_range": rec.get("income_range"),
        "segments": segments,
    }


# =========================
# System-prompt context blocks
# =========================
def employee_context_block(identity: Dict[str, Any]) -> str:
    first = identity.get("first_name") or "the colleague"
    return (
        "\n\n--- VERIFIED EMPLOYEE CONTEXT ---\n"
        f"Name: {identity.get('name')}\n"
        f"First name: {first}\n"
        f"Employee ID: {identity.get('employee_id')}\n"
        f"Designation: {identity.get('designation')}\n"
        f"Department: {identity.get('department')}\n"
        f"Location: {identity.get('location')}\n"
        f"Employment status: {identity.get('employment_status')}\n"
        "PERSONALIZATION RULE: Open with a respectful peer salutation using the "
        f"first name (e.g. 'Hi {first},'). You may discuss internal SMIFS product "
        "specifics, internal processes, and KB content as a knowledgeable colleague. "
        "Do not invent compensation or HR-specific information."
        "\n--- END EMPLOYEE CONTEXT ---"
    )


def client_context_block(identity: Dict[str, Any]) -> str:
    first = identity.get("first_name") or "valued investor"
    rm = identity.get("rm_name") or "your relationship manager"
    risk = identity.get("risk_profile") or "your risk profile"
    return (
        "\n\n--- VERIFIED CLIENT CONTEXT ---\n"
        f"UCC: {identity.get('ucc')}\n"
        f"First name (heuristic): {first}\n"
        f"Branch: {identity.get('branch_name')}\n"
        f"Relationship Manager: {rm}\n"
        f"Risk profile: {risk}\n"
        f"Status: {identity.get('status')}\n"
        "PERSONALIZATION RULE: Address the client by their first name once at the "
        "start of the reply. When suggesting next-best actions, reference their RM "
        f"({rm}) for execution. Do not invent specific holdings, NAVs, or transactions; "
        "for portfolio specifics, offer to involve their RM."
        "\n--- END CLIENT CONTEXT ---"
    )
