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
                employment_status=args.get("status"),
                limit=int(args.get("limit") or 10),
            )
            items = res["items"]
            total = res["total"]
            filters = [f for f in (
                _fmt("name", args.get("name")),
                _fmt("dept", args.get("department")),
                _fmt("designation", args.get("designation")),
                _fmt("location", args.get("location")),
                _fmt("status", args.get("status")),
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
    return f"{label}='{val}'" if val else None


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
            "description": "Search SMIFS employees with filters. Use when user asks for a list of colleagues by department, designation, location, or name fragment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Free-text name fragment."},
                    "department": {"type": "string", "description": "Exact department, e.g. 'COMPLIANCE', 'Institutional Equities'."},
                    "designation": {"type": "string", "description": "Exact designation, e.g. 'Senior Manager', 'Research Associate'."},
                    "location": {"type": "string", "description": "City or office substring, e.g. 'Mumbai', 'Kolkata'."},
                    "status": {"type": "string", "enum": ["Active", "Inactive"], "description": "Employment status filter."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 25, "description": "Max results to return (default 10)."},
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
]

DIRECTORY_TOOL_NAMES = {t["function"]["name"] for t in DIRECTORY_TOOLS}
