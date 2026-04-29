"""Phase 8 — OrgLens directory HTTP wrappers for live employee-only tool-calling.

This module is the low-level bridge: pure HTTP + light shaping. The
`agents.directory_agent` layer is what the router dispatches to.

Privacy posture
===============
* All raw OrgLens fields that contain PAN / bank / Aadhaar / DOB are STRIPPED
  before the data leaves this module — we expose only directory-relevant
  fields (name, designation, dept, location, reporting, tenure, masked
  email / phone).
* Emails and phones are already masked in the OrgLens record when surfaced
  via `identity.mask_email_display` / `mask_phone_display`.
* Never log raw PAN or full email. `identity.sanitize_for_log` scrubs PAN; we
  log only employee_id + truncated name.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

import httpx

import identity

logger = logging.getLogger(__name__)

_TIMEOUT = 20.0


class DirectoryForbidden(Exception):
    """OrgLens returned 403 — permission scope missing."""


class DirectoryRateLimited(Exception):
    """OrgLens returned 429."""


class DirectoryUnavailable(Exception):
    """Network / 5xx error."""


def _check() -> None:
    if not identity.ORGLENS_BASE_URL or not identity.ORGLENS_API_KEY:
        raise DirectoryUnavailable("OrgLens directory not configured")


def _headers() -> Dict[str, str]:
    return {"X-API-Key": identity.ORGLENS_API_KEY, "Accept": "application/json"}


async def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _check()
    url = f"{identity.ORGLENS_BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.get(url, headers=_headers(), params=params or {})
    if r.status_code == 403:
        raise DirectoryForbidden(f"403 on {path}")
    if r.status_code == 429:
        raise DirectoryRateLimited(f"429 on {path}")
    if r.status_code == 404:
        return {}
    if r.status_code >= 500:
        raise DirectoryUnavailable(f"{r.status_code} on {path}")
    r.raise_for_status()
    return r.json() or {}


# ---------- shaping helpers ----------
_SAFE_EMP_FIELDS = (
    "user_id", "employee_id", "name", "first_name", "last_name",
    "designation", "department", "business_unit", "company",
    "location", "location_type", "employment_status", "employee_type",
    "confirmation_status", "date_of_joining", "current_experience",
    "reports_to_name", "reports_to_user_id", "reports_to_employee_id",
    "direct_reports_count", "total_team_size", "hrbp_name", "gender",
    "on_notice", "is_absconding", "synced_at",
)


def _shape_employee(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Strip sensitive fields, mask the few contact fields we keep."""
    if not rec:
        return {}
    out = {k: rec.get(k) for k in _SAFE_EMP_FIELDS if rec.get(k) is not None}
    if rec.get("email"):
        out["email_display"] = identity.mask_email_display(rec["email"])
    if rec.get("reports_to_email"):
        out["reports_to_email_display"] = identity.mask_email_display(rec["reports_to_email"])
    if rec.get("hrbp_email"):
        out["hrbp_email_display"] = identity.mask_email_display(rec["hrbp_email"])
    return out


# ---------- public API ----------
async def get_stats() -> Dict[str, Any]:
    data = await _get("/stats")
    return {
        "total_employees": data.get("total_employees"),
        "active_employees": data.get("active_employees"),
        "inactive_employees": data.get("inactive_employees"),
        "total_departments": data.get("total_departments"),
        "total_locations": data.get("total_locations"),
        "status_breakdown": data.get("status_breakdown") or {},
        "last_sync": data.get("last_sync"),
    }


async def list_departments(limit: int = 30) -> Dict[str, Any]:
    data = await _get("/departments")
    items = data.get("departments") or []
    return {"total": data.get("total", len(items)), "items": items[:limit]}


async def list_locations(limit: int = 30) -> Dict[str, Any]:
    data = await _get("/locations")
    items = data.get("locations") or []
    return {"total": data.get("total", len(items)), "items": items[:limit]}


async def list_designations(limit: int = 30) -> Dict[str, Any]:
    data = await _get("/designations")
    items = data.get("designations") or []
    return {"total": data.get("total", len(items)), "items": items[:limit]}


async def lookup_employee(query: str) -> Optional[Dict[str, Any]]:
    """Smart lookup: email → /employee/by-email; code pattern → /employee/by-code;
    else /employees?search=<query> and take the top hit.
    """
    if not query:
        return None
    q = query.strip()
    # 1) email
    if "@" in q:
        try:
            rec = await identity.lookup_employee_by_email(q)
        except identity.OrgLensForbidden:
            raise DirectoryForbidden("employees:pii missing")
        return _shape_employee(rec) if rec else None
    # 2) employee_id pattern "SMWM-XXXXXXXX"
    if q.upper().startswith("SMWM-"):
        data = await _get(f"/employee/by-code/{q}")
        rec = data.get("employee")
        return _shape_employee(rec) if rec else None
    # 3) search by name
    data = await _get("/employees", {"search": q, "limit": 5})
    emps = data.get("employees") or []
    if not emps:
        return None
    return _shape_employee(emps[0])


async def search_employees(
    name: Optional[str] = None,
    department: Optional[str] = None,
    designation: Optional[str] = None,
    location: Optional[str] = None,
    employment_status: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """Call /employees with supported filters. For location (API-side filter
    is exact-match), we fall back to client-side substring filter across a
    larger pull when nothing else narrows the query."""
    params: Dict[str, Any] = {"limit": max(1, min(limit, 25))}
    if name:
        params["search"] = name
    if department:
        params["department"] = department
    if designation:
        params["designation"] = designation
    if employment_status:
        params["employment_status"] = employment_status
    data = await _get("/employees", params)
    total = data.get("total", 0)
    items = [_shape_employee(e) for e in (data.get("employees") or [])]
    # Client-side location filter pass (API doesn't filter reliably)
    if location:
        loc_lc = location.lower()
        items = [e for e in items if loc_lc in (e.get("location") or "").lower()]
        # If we over-filtered because the first page didn't contain matches,
        # pull a bigger window once.
        if not items and not name and not department and not designation:
            data2 = await _get("/employees", {"limit": 100})
            all_items = [_shape_employee(e) for e in (data2.get("employees") or [])]
            items = [e for e in all_items if loc_lc in (e.get("location") or "").lower()][:limit]
            total = len(items)
    return {"total": total, "items": items[:limit]}


async def my_team(reports_to_user_id: str, limit: int = 25) -> Dict[str, Any]:
    """Return direct reports of `reports_to_user_id` via /org-tree
    (API-side reports_to filter is broken — we filter client-side)."""
    if not reports_to_user_id:
        return {"total": 0, "items": []}
    data = await _get("/org-tree", {"limit": 800})
    emps = data.get("employees") or []
    team = [_shape_employee(e) for e in emps
            if str(e.get("reports_to_user_id") or "") == str(reports_to_user_id)]
    return {"total": len(team), "items": team[:limit]}


async def reporting_chain(employee_code: str, max_hops: int = 6) -> List[Dict[str, Any]]:
    """Walk `reports_to_employee_id` upward up to `max_hops` times."""
    chain: List[Dict[str, Any]] = []
    seen = set()
    code = (employee_code or "").strip()
    hops = 0
    while code and hops < max_hops and code not in seen:
        seen.add(code)
        data = await _get(f"/employee/by-code/{code}")
        rec = data.get("employee")
        if not rec:
            break
        shaped = _shape_employee(rec)
        chain.append(shaped)
        nxt = rec.get("reports_to_employee_id")
        if not nxt or nxt == rec.get("employee_id"):
            break
        code = nxt
        hops += 1
    return chain


async def org_tree(anchor_code: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    params: Dict[str, Any] = {"limit": limit}
    if anchor_code:
        params["employee_code"] = anchor_code
    data = await _get("/org-tree", params)
    emps = data.get("employees") or []
    return {"total": data.get("total", len(emps)), "items": [_shape_employee(e) for e in emps[:limit]]}
