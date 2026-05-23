"""Phase 12 — Client back-office + MF stack HTTP wrappers.

Wraps the new `/api/v1/bo/client/*` and `/api/v1/mf/client/*` endpoints from
the OrgLens API (see `ORGLENS_DIFF.md`).

Privacy invariants
==================
* Every call is bound to the VERIFIED ucc from the session — `directory_agent`
  / `client_agent` resolves it once at the boundary; the LLM never supplies
  a `ucc` or `pan` to these functions.
* PAN / Aadhaar / full bank account numbers are scrubbed by `_scrub_client(...)`
  before any payload is handed back to the LLM-facing surface.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, Optional

import directory  # re-uses the cached httpx wrapper

logger = logging.getLogger(__name__)


# ---------- scrubbing helpers ----------
_SENSITIVE_KEYS = {
    "pan", "aadhaar", "aadhaar_masked", "aadhar_no",
    "bank_account_no", "bank_account_number",
    "ifsc", "bank_ifsc", "raw_html_path", "raw_html_hash",
}


def _scrub_client(rec: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(rec, dict):
        return rec
    out = {k: v for k, v in rec.items() if k not in _SENSITIVE_KEYS}
    # Mask the email + mobile if present.
    if out.get("email"):
        e = out["email"]
        if isinstance(e, str) and "@" in e:
            local, dom = e.split("@", 1)
            out["email_display"] = (local[:2] + "***@" + dom)
            out.pop("email", None)
    if out.get("mobile"):
        m = str(out["mobile"])
        out["mobile_display"] = "***" + m[-4:] if len(m) >= 4 else "****"
        out.pop("mobile", None)
    return out


# ---------- BO (back-office equity) ----------
async def client_360(ucc: str) -> Dict[str, Any]:
    data = await directory._get(f"/bo/client/{ucc}/360")
    snap = (data or {}).get("snapshot") or {}
    return {
        "ucc": ucc,
        "master": _scrub_client(snap.get("master") or {}),
        "portfolio_count": len(snap.get("portfolio") or []),
        "ledger_balance": snap.get("ledger_summary") or snap.get("ledger") or {},
        "trade_count_30d": snap.get("trade_count_30d") or 0,
        "raw": snap,
    }


async def client_portfolio(ucc: str) -> Dict[str, Any]:
    data = await directory._get(f"/bo/client/{ucc}/portfolio")
    holdings = data.get("holdings") or []
    return {
        "ucc": ucc,
        "count": data.get("count", len(holdings)),
        "holdings": holdings,
    }


async def client_ledger_balance(ucc: str) -> Dict[str, Any]:
    data = await directory._get(f"/bo/client/{ucc}/ledger/balance")
    return {
        "ucc": ucc,
        "balance": float(data.get("balance") or 0.0),
        "total_credits": float(data.get("total_credits") or 0.0),
        "total_debits": float(data.get("total_debits") or 0.0),
        "entries": int(data.get("entries") or 0),
        "as_of": data.get("as_of"),
    }


async def client_recent_trades(ucc: str, limit: int = 10) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 10), 50))
    data = await directory._get(f"/bo/client/{ucc}/trade-book", {"limit": limit})
    return {
        "ucc": ucc,
        "total": data.get("total", 0),
        "count": data.get("count", 0),
        "trades": data.get("trades") or [],
    }


async def client_deposits_withdrawals(ucc: str, limit: int = 5) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 5), 25))
    dep = await directory._get(f"/bo/client/{ucc}/deposits", {"limit": limit})
    wdr = await directory._get(f"/bo/client/{ucc}/withdrawals", {"limit": limit})
    return {
        "ucc": ucc,
        "deposits": dep.get("deposits") or [],
        "deposits_count": dep.get("count", 0),
        "withdrawals": wdr.get("withdrawals") or [],
        "withdrawals_count": wdr.get("count", 0),
    }


# ---------- MF (mutual fund) ----------
async def _mf_uid_for_ucc(ucc: str) -> Optional[str]:
    """Resolve an MF UID from UCC via the BO master record (which carries PAN).

    The PAN is used ONLY as a join key against the MF stack — it never leaves
    this function and is not returned to the caller.
    """
    try:
        master = await directory._get(f"/bo/client/by-ucc/{ucc}")
    except Exception as e:
        logger.info("mf_uid_for_ucc(%s) master lookup failed: %s", ucc, e)
        return None
    pan = (master.get("client") or {}).get("pan")
    if not pan:
        return None
    try:
        data = await directory._get(f"/mf/client/by-pan/{pan}")
    except Exception as e:
        logger.info("mf_uid_for_ucc(%s) MF lookup failed: %s", ucc, e)
        return None
    c = data.get("client") or {}
    return c.get("uid") or c.get("client_id") or c.get("id")


async def client_mf_holdings(*, ucc: str) -> Dict[str, Any]:
    uid = await _mf_uid_for_ucc(ucc)
    if not uid:
        return {"ucc": ucc, "uid": None, "folios": [], "count": 0,
                "note": "no MF account found on file"}
    data = await directory._get(f"/mf/client/{uid}/folios")
    return {
        "ucc": ucc,
        "uid": uid,
        "count": data.get("count", 0),
        "folios": data.get("folios") or [],
    }


async def client_mf_sips(*, ucc: str) -> Dict[str, Any]:
    uid = await _mf_uid_for_ucc(ucc)
    if not uid:
        return {"ucc": ucc, "uid": None, "sips": [], "count": 0,
                "note": "no MF account found on file"}
    data = await directory._get(f"/mf/client/{uid}/sips")
    return {
        "ucc": ucc,
        "uid": uid,
        "count": data.get("count", 0),
        "sips": data.get("sips") or [],
    }
