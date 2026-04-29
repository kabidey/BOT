"""Phase 8 — Directory agent.

Dispatches directory_* tool calls chosen by the Router (only for verified
employee sessions) and produces structured blocks the FE renders directly.

Caching
=======
Per-(session_id, tool_name, args_key) in-memory TTL cache (5 min) so
the same question asked twice in a row hits OrgLens once.

Error handling
==============
* DirectoryForbidden → polite "access denied" block
* DirectoryRateLimited → "please retry shortly" block
* Any 404/empty result → honest "no matches" reply, no fabrication
"""
from __future__ import annotations
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import directory
from identity import mask_email_display

logger = logging.getLogger(__name__)

TTL_SECONDS = 300  # 5 min
_cache: Dict[Tuple[str, str, str], Tuple[float, Dict[str, Any]]] = {}


def _key(session_id: str, tool: str, args: Dict[str, Any]) -> Tuple[str, str, str]:
    h = hashlib.sha1(json.dumps(args or {}, sort_keys=True).encode()).hexdigest()[:12]
    return (session_id, tool, h)


def _cache_get(k) -> Optional[Dict[str, Any]]:
    e = _cache.get(k)
    if not e:
        return None
    ts, v = e
    if time.time() - ts > TTL_SECONDS:
        _cache.pop(k, None)
        return None
    return v


def _cache_put(k, v: Dict[str, Any]) -> None:
    _cache[k] = (time.time(), v)


# ---------- block builders ----------
def _employee_line(e: Dict[str, Any]) -> str:
    bits = [e.get("name") or "(unknown)"]
    if e.get("designation"):
        bits.append(e["designation"])
    if e.get("department"):
        bits.append(e["department"])
    if e.get("location"):
        bits.append(e["location"].split(",")[0])
    return " · ".join(bits)


def _directory_card(emp: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "directory_card", "data": emp}


def _directory_list(title: str, items: List[Dict[str, Any]], total: int,
                    summary_fields: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "directory_list",
        "data": {
            "title": title,
            "total": total,
            "items": items,
            "summary_fields": summary_fields or ["name", "designation", "department", "location"],
        },
    }


def _org_stats_card(stats: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "org_stats_card", "data": stats}


def _reporting_chain_card(chain: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"type": "reporting_chain_card", "data": {"chain": chain}}


# ---------- dispatch ----------
async def execute(tool_name: str, args: Dict[str, Any],
                  session_id: str, identity_obj: Dict[str, Any]) -> Dict[str, Any]:
    """Run a single directory_* tool and return an `out` dict shaped like the
    other orchestrator branches: {blocks, citations, model, intent_hint?}.
    """
    args = args or {}
    ck = _key(session_id, tool_name, args)
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    try:
        if tool_name == "directory_lookup_employee":
            q = (args.get("query") or "").strip()
            if not q:
                out = _text("Please share a name, email, or employee ID to look up.")
            else:
                emp = await directory.lookup_employee(q)
                if not emp:
                    out = _text(f"No match found for '{q}' in the SMIFS directory.")
                else:
                    intro = f"Here's what I pulled from the directory for {emp.get('name', q)}:"
                    out = {"blocks": [{"type": "text", "text": intro}, _directory_card(emp)],
                           "citations": [], "model": None}

        elif tool_name == "directory_search_employees":
            res = await directory.search_employees(
                name=args.get("name") or args.get("query"),
                department=args.get("department"),
                designation=args.get("designation"),
                location=args.get("location"),
                employment_status=args.get("status") or args.get("employment_status"),
                employee_type=args.get("employee_type"),
                confirmation_status=args.get("confirmation_status"),
                business_unit=args.get("business_unit"),
                company=args.get("company"),
                gender=args.get("gender"),
                on_notice=args.get("on_notice"),
                is_absconding=args.get("is_absconding"),
                reports_to_name=args.get("reports_to_name"),
                reports_to_email=args.get("reports_to_email"),
                reports_to_user_id=args.get("reports_to_user_id"),
                hrbp_name=args.get("hrbp_name"),
                limit=int(args.get("limit") or 10),
            )
            items = res["items"]
            total = res["total"]
            filters = [f for f in (
                _fmt("name", args.get("name")),
                _fmt("dept", args.get("department")),
                _fmt("designation", args.get("designation")),
                _fmt("location", args.get("location")),
                _fmt("status", args.get("status") or args.get("employment_status")),
                _fmt("bu", args.get("business_unit")),
                _fmt("gender", args.get("gender")),
                _fmt("hrbp", args.get("hrbp_name")),
                _fmt("reports_to", args.get("reports_to_name")),
                _fmt("on_notice", args.get("on_notice")),
                _fmt("is_absconding", args.get("is_absconding")),
                _fmt("confirmation", args.get("confirmation_status")),
            ) if f]
            filt_label = " · ".join(filters) if filters else "all filters"
            title = f"Employees matching {filt_label}"
            if not items:
                out = _text(f"No employees matched those filters ({filt_label}).")
            else:
                intro = f"Found {total} employee{'s' if total != 1 else ''} · showing {len(items)}."
                out = {"blocks": [{"type": "text", "text": intro}, _directory_list(title, items, total)],
                       "citations": [], "model": None}

        elif tool_name == "directory_my_team":
            user_id = identity_obj.get("user_id")
            if not user_id:
                out = _text("I couldn't determine your user ID to look up your team.")
            else:
                res = await directory.my_team(str(user_id))
                items = res["items"]
                if not items:
                    out = _text("You have no direct reports on record. If that seems wrong, please flag it to HRBP.")
                else:
                    intro = f"You have {len(items)} direct report{'s' if len(items) != 1 else ''}:"
                    out = {"blocks": [{"type": "text", "text": intro},
                                      _directory_list("Your direct reports", items, len(items))],
                           "citations": [], "model": None}

        elif tool_name == "directory_my_reporting_chain":
            emp_code = identity_obj.get("employee_id")
            if not emp_code:
                out = _text("I couldn't determine your employee ID.")
            else:
                chain = await directory.reporting_chain(emp_code)
                if len(chain) <= 1:
                    out = _text("You're at the top of the reporting chain on record.")
                else:
                    out = {
                        "blocks": [
                            {"type": "text", "text": f"Your reporting chain ({len(chain)} levels):"},
                            _reporting_chain_card(chain),
                        ],
                        "citations": [], "model": None,
                    }

        elif tool_name == "directory_departments":
            res = await directory.list_departments()
            intro = f"SMIFS currently has **{res['total']}** departments. Top departments by headcount:"
            item_rows = [{"name": d.get("name"), "designation": f"{d.get('count')} employees"} for d in res["items"]]
            out = {"blocks": [{"type": "text", "text": intro},
                              _directory_list("Departments", item_rows, res["total"], summary_fields=["name", "designation"])],
                   "citations": [], "model": None}

        elif tool_name == "directory_locations":
            res = await directory.list_locations()
            intro = f"SMIFS operates across **{res['total']}** locations. Top offices by headcount:"
            item_rows = [{"name": d.get("name"), "designation": f"{d.get('count')} employees"} for d in res["items"]]
            out = {"blocks": [{"type": "text", "text": intro},
                              _directory_list("Locations", item_rows, res["total"], summary_fields=["name", "designation"])],
                   "citations": [], "model": None}

        elif tool_name == "directory_designations":
            res = await directory.list_designations()
            intro = f"SMIFS has **{res['total']}** designations. Most common:"
            item_rows = [{"name": d.get("name"), "designation": f"{d.get('count')} employees"} for d in res["items"]]
            out = {"blocks": [{"type": "text", "text": intro},
                              _directory_list("Designations", item_rows, res["total"], summary_fields=["name", "designation"])],
                   "citations": [], "model": None}

        elif tool_name == "directory_org_stats":
            stats = await directory.get_stats()
            intro = (
                f"SMIFS headcount snapshot: **{stats.get('total_employees')}** total · "
                f"{stats.get('active_employees')} active across "
                f"{stats.get('total_departments')} departments and {stats.get('total_locations')} locations."
            )
            out = {"blocks": [{"type": "text", "text": intro}, _org_stats_card(stats)],
                   "citations": [], "model": None}

        elif tool_name == "directory_org_tree":
            code = args.get("employee_code")
            res = await directory.org_tree(anchor_code=code, limit=int(args.get("limit") or 50))
            items = res["items"]
            if not items:
                out = _text("No org-tree data returned.")
            else:
                intro = f"Org tree (showing {len(items)} of {res['total']} employees):"
                out = {"blocks": [{"type": "text", "text": intro},
                                  _directory_list("Org tree", items, res["total"])],
                       "citations": [], "model": None}

        elif tool_name == "directory_filter_by_status":
            res = await directory.search_employees(
                employment_status=args.get("status") or args.get("employment_status"),
                on_notice=args.get("on_notice"),
                is_absconding=args.get("is_absconding"),
                confirmation_status=args.get("confirmation_status"),
                limit=int(args.get("limit") or 15),
            )
            filters = [f for f in (
                _fmt("on_notice", args.get("on_notice")),
                _fmt("is_absconding", args.get("is_absconding")),
                _fmt("confirmation", args.get("confirmation_status")),
                _fmt("status", args.get("status") or args.get("employment_status")),
            ) if f]
            filt_label = " · ".join(filters) if filters else "no filter"
            if not res["items"]:
                out = _text(f"No employees match: {filt_label}.")
            else:
                intro = f"**{res['total']}** employee{'s' if res['total'] != 1 else ''} match: {filt_label}."
                out = {"blocks": [{"type": "text", "text": intro},
                                  _directory_list(f"By status: {filt_label}", res["items"], res["total"])],
                       "citations": [], "model": None}

        elif tool_name == "directory_recent_joins":
            days = int(args.get("days") or 30)
            res = await directory.recent_joins(days=days, limit=int(args.get("limit") or 15))
            if not res["items"]:
                out = _text(f"No new joiners in the last {days} days.")
            else:
                intro = f"**{res['total']}** employee{'s' if res['total'] != 1 else ''} joined in the last {days} days."
                out = {"blocks": [{"type": "text", "text": intro},
                                  _directory_list(f"Joined in last {days} days", res["items"], res["total"],
                                                  summary_fields=["name", "designation", "department", "location"])],
                       "citations": [], "model": None}

        elif tool_name == "directory_upcoming_confirmations":
            days = int(args.get("days") or 60)
            res = await directory.upcoming_confirmations(days=days, limit=int(args.get("limit") or 15))
            if not res["items"]:
                out = _text(f"No upcoming confirmations in the next {days} days.")
            else:
                intro = f"**{res['total']}** confirmation{'s' if res['total'] != 1 else ''} due in the next {days} days."
                out = {"blocks": [{"type": "text", "text": intro},
                                  _directory_list(f"Upcoming confirmations (next {days} days)", res["items"], res["total"])],
                       "citations": [], "model": None}

        elif tool_name == "directory_by_tenure":
            res = await directory.by_tenure(
                min_years=args.get("min_years"),
                max_years=args.get("max_years"),
                limit=int(args.get("limit") or 15),
                sort_desc=bool(args.get("sort_desc", True)),
            )
            if not res["items"]:
                out = _text("No employees match those tenure bounds.")
            else:
                label = _tenure_label(args.get("min_years"), args.get("max_years"))
                intro = f"**{res['total']}** employee{'s' if res['total'] != 1 else ''} with tenure {label}."
                # Add tenure years into the designation slot for the list UI
                items_view = [{**e, "designation": f"{e.get('designation') or ''} · {e.get('tenure_years')}y" if e.get('tenure_years') is not None else e.get('designation')} for e in res["items"]]
                out = {"blocks": [{"type": "text", "text": intro},
                                  _directory_list(f"Tenure {label}", items_view, res["total"])],
                       "citations": [], "model": None}

        elif tool_name == "directory_aggregate":
            res = await directory.aggregate(
                group_by=args.get("group_by") or "department",
                department=args.get("department"),
                location=args.get("location"),
                employment_status=args.get("employment_status"),
            )
            if res.get("error"):
                out = _text(res["error"])
            elif not res["items"]:
                out = _text("No data to aggregate with those filters.")
            else:
                pre_filters = [f for f in (
                    _fmt("dept", args.get("department")),
                    _fmt("loc", args.get("location")),
                    _fmt("status", args.get("employment_status")),
                ) if f]
                pre = f" (filtered by {' · '.join(pre_filters)})" if pre_filters else ""
                intro = f"Headcount by **{res['group_by']}**{pre} · {res['total']} employees total."
                rows = [{"name": it["name"], "designation": f"{it['count']} employees"} for it in res["items"][:20]]
                out = {"blocks": [{"type": "text", "text": intro},
                                  _directory_list(f"By {res['group_by']}", rows, res["total"],
                                                  summary_fields=["name", "designation"])],
                       "citations": [], "model": None}

        elif tool_name == "directory_field_value":
            identifier = args.get("identifier") or args.get("person")
            field = args.get("field")
            if not identifier or not field:
                out = _text("I need both a person identifier and a field name.")
            else:
                res = await directory.field_value(identifier, field)
                if not res.get("found"):
                    out = _text(f"No employee matched '{identifier}'.")
                elif res.get("redacted"):
                    out = _text(f"The field '{field}' is restricted and not exposed via directory lookups.")
                else:
                    p = res.get("person") or {}
                    v = res.get("value")
                    if v is None or v == "":
                        out = _text(f"{p.get('name') or identifier} has no value for **{field}** on record.")
                    else:
                        text = f"**{p.get('name') or identifier}** · {field} = `{v}`"
                        out = {"blocks": [{"type": "text", "text": text}], "citations": [], "model": None}

        else:
            out = _text(f"Unknown directory tool: {tool_name}")

    except directory.DirectoryForbidden:
        out = _text("I don't have directory access for that request (permission scope missing).")
    except directory.DirectoryRateLimited:
        out = _text("The directory service is rate-limited right now. Please retry in a few seconds.")
    except directory.DirectoryUnavailable as e:
        logger.warning("Directory unavailable: %s", e)
        out = _text("The directory service is briefly unavailable. Please try again shortly.")
    except Exception:
        logger.exception("Directory tool %s failed", tool_name)
        out = _text("I couldn't access that information; let me know if I should try something else.")

    _cache_put(ck, out)
    return out


def _text(msg: str) -> Dict[str, Any]:
    return {"blocks": [{"type": "text", "text": msg}], "citations": [], "model": None}


def _fmt(label: str, val: Optional[str]) -> Optional[str]:
    if val is None or val == "":
        return None
    return f"{label}='{val}'"


def _tenure_label(min_years, max_years) -> str:
    if min_years is not None and max_years is not None:
        return f"{min_years}–{max_years} years"
    if min_years is not None:
        return f"≥{min_years} years"
    if max_years is not None:
        return f"≤{max_years} years"
    return "(any)"


# Tool definitions shared with the router — one dict list
DIRECTORY_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "directory_lookup_employee",
            "description": "Look up a single SMIFS employee by name, work email, or employee ID. Use when the user asks about a specific named colleague.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Name, email, or employee ID (e.g. 'Suman Mukherjee', 'awanish.chandra@smifs.com', 'SMWM-25031054')."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_search_employees",
            "description": "Search SMIFS employees with rich filters. Use when user asks for a list of colleagues by any combination of: name, department, designation, location, employment status, employee type, confirmation status, business unit, company, gender, on_notice flag, is_absconding flag, reports_to (name or user id), hrbp_name. Returns up to `limit` results and the total matching.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Free-text name fragment (server-side search)."},
                    "department": {"type": "string", "description": "Exact department, e.g. 'COMPLIANCE', 'Institutional Equities'."},
                    "designation": {"type": "string", "description": "Exact designation, e.g. 'Senior Manager'."},
                    "location": {"type": "string", "description": "City or office substring, e.g. 'Mumbai'."},
                    "employment_status": {"type": "string", "enum": ["Active", "Inactive"], "description": "Employment status filter."},
                    "employee_type": {"type": "string", "description": "e.g. 'Permanent', 'Contract'."},
                    "confirmation_status": {"type": "string", "description": "e.g. 'Confirmed', 'Probation'."},
                    "business_unit": {"type": "string", "description": "BU substring, e.g. 'Capital Markets'."},
                    "company": {"type": "string", "description": "Company substring."},
                    "gender": {"type": "string", "enum": ["Male", "Female", "Other"]},
                    "on_notice": {"type": "boolean", "description": "Only people currently on notice."},
                    "is_absconding": {"type": "boolean", "description": "Only people flagged absconding."},
                    "reports_to_name": {"type": "string", "description": "Manager name substring."},
                    "reports_to_user_id": {"type": "string", "description": "Exact manager user_id."},
                    "hrbp_name": {"type": "string", "description": "HRBP name substring."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Max results to return (default 10)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_my_team",
            "description": "Return the verified user's direct reports.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_my_reporting_chain",
            "description": "Return the verified user's upward reporting chain (up to 6 hops).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_departments",
            "description": "List all SMIFS departments with headcount.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_locations",
            "description": "List all SMIFS office locations with headcount.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_designations",
            "description": "List all SMIFS designations with headcount.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_org_stats",
            "description": "Return overall SMIFS org stats (total employees, active count, departments, locations).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_org_tree",
            "description": "Return a subtree of the SMIFS org, optionally anchored at an employee_code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "employee_code": {"type": "string", "description": "Optional anchor employee_id (e.g. 'SMWM-25031054')."},
                    "limit": {"type": "integer", "minimum": 5, "maximum": 200},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_filter_by_status",
            "description": "Count and list employees matching a status flag — on_notice, is_absconding, confirmation_status, or employment_status. Use for 'how many people are on notice?', 'show me everyone absconding', 'people still on probation', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "on_notice": {"type": "boolean"},
                    "is_absconding": {"type": "boolean"},
                    "confirmation_status": {"type": "string", "description": "e.g. 'Probation', 'Confirmed'."},
                    "employment_status": {"type": "string", "enum": ["Active", "Inactive"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_recent_joins",
            "description": "Employees whose date_of_joining falls within the last N days. Use for 'who joined this month', 'new hires in the last 2 weeks'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "Look-back window in days (default 30)."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_upcoming_confirmations",
            "description": "Employees whose date_of_confirmation falls in the next N days and are not yet confirmed. Use for 'who's up for confirmation soon', 'upcoming confirmations'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "Forward window in days (default 60)."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_by_tenure",
            "description": "Employees filtered by tenure in years (current_experience). Use for 'who's been here more than 10 years', 'newest employees', 'top 10 by tenure'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "min_years": {"type": "number", "description": "Minimum tenure in years (inclusive)."},
                    "max_years": {"type": "number", "description": "Maximum tenure in years (inclusive)."},
                    "sort_desc": {"type": "boolean", "description": "True = longest tenure first (default), False = newest first."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_aggregate",
            "description": "Count employees grouped by a single field (department, location, designation, employment_status, confirmation_status, gender, employee_type, business_unit). Optional pre-filter by department / location / employment_status. Use for 'headcount by location', 'gender breakdown in Compliance', 'how many on probation vs confirmed'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "group_by": {"type": "string", "enum": ["department", "location", "designation", "employment_status", "confirmation_status", "gender", "employee_type", "business_unit"]},
                    "department": {"type": "string", "description": "Optional pre-filter — only count within this department."},
                    "location": {"type": "string", "description": "Optional pre-filter — only count within locations matching this substring."},
                    "employment_status": {"type": "string", "enum": ["Active", "Inactive"]},
                },
                "required": ["group_by"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_field_value",
            "description": "Look up a specific field for a specific employee. Use for 'what is Suman's HRBP?', 'who is Rahul's manager?', 'what department is Awanish in?'. The `field` parameter must match an OrgLens field name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "Name, email, or employee_id of the person."},
                    "field": {"type": "string", "description": "OrgLens field name — e.g. 'hrbp_name', 'reports_to_name', 'department', 'designation', 'location', 'date_of_joining', 'confirmation_status', 'on_notice', 'hod_name', 'business_unit'."},
                },
                "required": ["identifier", "field"],
            },
        },
    },
]

DIRECTORY_TOOL_NAMES = {t["function"]["name"] for t in DIRECTORY_TOOLS}
