"""Phase 20 — PII masking for OrgLens tool responses.

Lightweight, deterministic. Each function masks a single field; the
adapter layer chains them based on the manifest's `mask_fields` declaration.
Never raises — masking is best-effort and falls back to the empty string
if the input is unexpected.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional


def mask_pan(pan: Optional[str]) -> str:
    """`ABCDE1234F` → `ABCDE****F` (preserves first 5 + last 1 for context)."""
    if not pan or len(pan) < 10:
        return pan or ""
    return f"{pan[:5]}{'*' * 4}{pan[-1].upper()}"


def mask_account(acct: Optional[str]) -> str:
    """Bank account → last-4 only."""
    if not acct:
        return ""
    s = str(acct).strip()
    if len(s) <= 4:
        return "*" * len(s)
    return f"{'*' * (len(s) - 4)}{s[-4:]}"


def mask_phone(phone: Optional[str]) -> str:
    """`9876543210` → `98****3210` (middle-4 masked)."""
    if not phone:
        return ""
    s = "".join(c for c in str(phone) if c.isdigit())
    if len(s) <= 4:
        return s
    if len(s) <= 6:
        return s[:2] + "*" * (len(s) - 4) + s[-2:]
    return s[:2] + "*" * (len(s) - 6) + s[-4:]


def mask_email(addr: Optional[str]) -> str:
    if not addr or "@" not in str(addr):
        return addr or ""
    local, _, dom = str(addr).partition("@")
    if len(local) <= 2:
        return f"{local}***@{dom}"
    return f"{local[:2]}***@{dom}"


def apply_field_masks(obj: Any, fields: List[str]) -> Any:
    """Walk a JSON value and mask the listed field names wherever they appear.
    Recurses into nested dicts + lists. Field-name match is case-sensitive
    against the manifest declaration; OrgLens field names are stable.
    """
    if not fields:
        return obj
    fset = set(fields)
    return _walk(obj, fset)


def _walk(obj: Any, fset: set) -> Any:
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if k in fset:
                out[k] = _mask_for_key(k, v)
            else:
                out[k] = _walk(v, fset)
        return out
    if isinstance(obj, list):
        return [_walk(v, fset) for v in obj]
    return obj


def _mask_for_key(key: str, value: Any) -> Any:
    if value is None:
        return None
    lk = key.lower()
    if "pan" in lk:
        return mask_pan(str(value))
    if "account" in lk or "acct" in lk:
        return mask_account(str(value))
    if "phone" in lk or "mobile" in lk:
        return mask_phone(str(value))
    if "email" in lk:
        return mask_email(str(value))
    if "aadhaar" in lk:
        return "*** never returned ***"
    if lk == "banks" and isinstance(value, list):
        # Nested object — recurse with bank-specific masks.
        out = []
        for b in value:
            if not isinstance(b, dict):
                out.append(b)
                continue
            row = dict(b)
            if "accountNo" in row:
                row["accountNo"] = mask_account(row.get("accountNo"))
            if "IFSC" in row and row.get("IFSC"):
                row["IFSC"] = "****" + str(row["IFSC"])[-4:]
            out.append(row)
        return out
    return value  # unknown shape — leave as-is


def redact_keys(obj: Any, keys: List[str]) -> Any:
    """Hard-redact (replace with `<redacted>`) the listed top-level keys.
    Used for CTC / salary fields that must never leak below admin role.
    """
    if not keys or not isinstance(obj, dict):
        return obj
    out = dict(obj)
    for k in keys:
        if k in out:
            out[k] = "<redacted>"
    return out
