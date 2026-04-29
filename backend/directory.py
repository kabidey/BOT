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
    "confirmation_status", "date_of_joining", "date_of_confirmation",
    "current_experience",
    "reports_to_name", "reports_to_user_id", "reports_to_employee_id",
    "direct_reports_count", "total_team_size", "hrbp_name", "gender",
    "on_notice", "on_notice_text", "is_absconding",
    "last_working_day", "synced_at",
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
    employee_type: Optional[str] = None,
    confirmation_status: Optional[str] = None,
    business_unit: Optional[str] = None,
    company: Optional[str] = None,
    gender: Optional[str] = None,
    on_notice: Optional[bool] = None,
    is_absconding: Optional[bool] = None,
    reports_to_name: Optional[str] = None,
    reports_to_email: Optional[str] = None,
    reports_to_user_id: Optional[str] = None,
    hrbp_name: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """Rich employee search. Server-side accepts: search, department,
    designation, employment_status, employee_type. Everything else we apply
    client-side on a wider pull (up to 800 rows from /org-tree or /employees)."""
    server_params: Dict[str, Any] = {}
    if name:
        server_params["search"] = name
    if department:
        server_params["department"] = department
    if designation:
        server_params["designation"] = designation
    if employment_status:
        server_params["employment_status"] = employment_status
    if employee_type:
        server_params["employee_type"] = employee_type

    # Any client-side filter (location, bool fields, reports_to_*, hrbp, gender, bu, company, confirmation)
    client_filters_on = any(v is not None for v in (
        location, on_notice, is_absconding, reports_to_name, reports_to_email,
        reports_to_user_id, hrbp_name, gender, business_unit, company, confirmation_status,
    ))

    if client_filters_on:
        # Need full fields → paginate across /employees (OrgLens caps page at 500)
        all_emps: List[Dict[str, Any]] = []
        skip = 0
        page = 500
        while True:
            data = await _get("/employees", {**server_params, "limit": page, "skip": skip})
            batch = data.get("employees") or []
            if not batch:
                break
            all_emps.extend(batch)
            total = data.get("total") or len(all_emps)
            skip += len(batch)
            if skip >= total or len(all_emps) >= 1000:
                break
        emps = all_emps
    else:
        params = {**server_params, "limit": max(1, min(limit, 25))}
        data = await _get("/employees", params)
        emps = data.get("employees") or []

    # Client-side filtering
    def _match(e: Dict[str, Any]) -> bool:
        if location and location.lower() not in (e.get("location") or "").lower():
            return False
        if on_notice is not None and bool(e.get("on_notice")) != bool(on_notice):
            return False
        if is_absconding is not None and bool(e.get("is_absconding")) != bool(is_absconding):
            return False
        if reports_to_name and reports_to_name.lower() not in (e.get("reports_to_name") or "").lower():
            return False
        if reports_to_email and reports_to_email.lower() != (e.get("reports_to_email") or "").lower():
            return False
        if reports_to_user_id and str(reports_to_user_id) != str(e.get("reports_to_user_id") or ""):
            return False
        if hrbp_name and hrbp_name.lower() not in (e.get("hrbp_name") or "").lower():
            return False
        if gender and gender.lower() != (e.get("gender") or "").lower():
            return False
        if business_unit and business_unit.lower() not in (e.get("business_unit") or "").lower():
            return False
        if company and company.lower() not in (e.get("company") or "").lower():
            return False
        if confirmation_status and confirmation_status.lower() != (e.get("confirmation_status") or "").lower():
            return False
        return True

    if client_filters_on:
        emps = [e for e in emps if _match(e)]

    total = data.get("total", 0) if not client_filters_on else len(emps)
    items = [_shape_employee(e) for e in emps[:max(1, limit)]]
    return {"total": total, "items": items}


# --- Phase 8.1 helpers ---

def _parse_dmy(s: str):
    """Parse OrgLens DD-MM-YYYY. Returns datetime.date or None."""
    if not s or not isinstance(s, str):
        return None
    try:
        from datetime import datetime as _dt
        return _dt.strptime(s.strip(), "%d-%m-%Y").date()
    except Exception:
        return None


def _experience_years(ce: str) -> Optional[float]:
    if not ce or not isinstance(ce, str):
        return None
    import re as _re
    y = _re.search(r"(\d+)\s*year", ce)
    m = _re.search(r"(\d+)\s*month", ce)
    if not y and not m:
        return None
    return round((int(y.group(1)) if y else 0) + (int(m.group(1)) if m else 0) / 12.0, 2)


async def _fetch_all_employees(max_rows: int = 800) -> List[Dict[str, Any]]:
    """Paginate /employees until max_rows or exhausted. OrgLens caps limit=500."""
    all_emps: List[Dict[str, Any]] = []
    page = 500
    skip = 0
    while len(all_emps) < max_rows:
        data = await _get("/employees", {"limit": page, "skip": skip})
        batch = data.get("employees") or []
        if not batch:
            break
        all_emps.extend(batch)
        total = data.get("total") or len(all_emps)
        skip += len(batch)
        if skip >= total:
            break
    return all_emps[:max_rows]


async def recent_joins(days: int = 30, limit: int = 25) -> Dict[str, Any]:
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=max(1, days))
    emps = await _fetch_all_employees()
    hits = []
    for e in emps:
        d = _parse_dmy(e.get("date_of_joining") or "")
        if d and d >= cutoff:
            shaped = _shape_employee(e)
            shaped["date_of_joining"] = e.get("date_of_joining")
            hits.append(shaped)
    hits.sort(key=lambda x: _parse_dmy(x.get("date_of_joining") or "") or date.min, reverse=True)
    return {"total": len(hits), "items": hits[:limit]}


async def upcoming_confirmations(days: int = 60, limit: int = 25) -> Dict[str, Any]:
    from datetime import date, timedelta
    today = date.today()
    horizon = today + timedelta(days=max(1, days))
    emps = await _fetch_all_employees()
    hits = []
    for e in emps:
        d = _parse_dmy(e.get("date_of_confirmation") or "")
        if d and today <= d <= horizon and (e.get("confirmation_status") or "").lower() != "confirmed":
            shaped = _shape_employee(e)
            shaped["date_of_confirmation"] = e.get("date_of_confirmation")
            hits.append(shaped)
    hits.sort(key=lambda x: _parse_dmy(x.get("date_of_confirmation") or "") or date.max)
    return {"total": len(hits), "items": hits[:limit]}


async def by_tenure(min_years: Optional[float] = None,
                    max_years: Optional[float] = None,
                    limit: int = 25,
                    sort_desc: bool = True) -> Dict[str, Any]:
    emps = await _fetch_all_employees()
    hits: List[Dict[str, Any]] = []
    for e in emps:
        y = _experience_years(e.get("current_experience") or "")
        if y is None:
            continue
        if min_years is not None and y < min_years:
            continue
        if max_years is not None and y > max_years:
            continue
        shaped = _shape_employee(e)
        shaped["tenure_years"] = y
        hits.append(shaped)
    hits.sort(key=lambda x: x.get("tenure_years") or 0, reverse=sort_desc)
    return {"total": len(hits), "items": hits[:limit]}


async def aggregate(group_by: str,
                    department: Optional[str] = None,
                    location: Optional[str] = None,
                    employment_status: Optional[str] = None) -> Dict[str, Any]:
    """Group employees by one of: department, location, designation,
    employment_status, confirmation_status, gender, employee_type,
    business_unit. Optional pre-filters narrow the universe."""
    allowed = {"department", "location", "designation", "employment_status",
               "confirmation_status", "gender", "employee_type", "business_unit"}
    key = (group_by or "").strip()
    if key not in allowed:
        return {"group_by": group_by, "error": f"unsupported group_by; use one of {sorted(allowed)}", "items": [], "total": 0}
    emps = await _fetch_all_employees()
    # Pre-filter
    if department:
        emps = [e for e in emps if (e.get("department") or "").lower() == department.lower()]
    if location:
        emps = [e for e in emps if location.lower() in (e.get("location") or "").lower()]
    if employment_status:
        emps = [e for e in emps if (e.get("employment_status") or "").lower() == employment_status.lower()]
    buckets: Dict[str, int] = {}
    for e in emps:
        v = e.get(key) or "Unknown"
        if key == "location":
            v = (v or "Unknown").split(",")[0].strip() or "Unknown"
        buckets[v] = buckets.get(v, 0) + 1
    items = [{"name": k, "count": v} for k, v in sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)]
    return {"group_by": key, "total": sum(v for _, v in buckets.items()),
            "filter": {"department": department, "location": location, "employment_status": employment_status},
            "items": items}


async def field_value(identifier: str, field: str) -> Dict[str, Any]:
    """Return a single field's value for a specific employee. `identifier`
    may be a name, email, or employee_code."""
    emp_raw = None
    q = (identifier or "").strip()
    if not q:
        return {"found": False, "reason": "empty identifier"}
    if "@" in q:
        emp_raw = await identity.lookup_employee_by_email(q)
    elif q.upper().startswith("SMWM-"):
        data = await _get(f"/employee/by-code/{q}")
        emp_raw = data.get("employee")
    else:
        data = await _get("/employees", {"search": q, "limit": 1})
        emps = data.get("employees") or []
        emp_raw = emps[0] if emps else None
    if not emp_raw:
        return {"found": False, "identifier": identifier}
    # Block sensitive fields
    sensitive = {"pan_number", "aadhar_no", "aadhar", "bank_details", "bank", "account"}
    if field in sensitive:
        return {"found": True, "identifier": identifier, "field": field,
                "value": None, "redacted": True,
                "reason": "sensitive field — not exposed via directory"}
    val = emp_raw.get(field)
    shaped = _shape_employee(emp_raw)
    return {"found": True, "person": {"name": shaped.get("name"), "employee_id": shaped.get("employee_id")},
            "field": field, "value": val}


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
