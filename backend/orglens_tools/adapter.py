"""Phase 20 — Generic OrgLens tool adapter.

`execute(tool, params, session)` does everything the LLM doesn't need to
think about:

* Validates `role` against the tool's `allowed_roles`.
* Auto-binds `binds_session` params (ucc → verified_ucc, etc.).
* Runs the employee RM-relationship check when the tool declares one.
* Reads the cache (in-process + Mongo) before hitting OrgLens.
* Calls OrgLens via `directory._get(...)`.
* Applies field masks + redactions per the manifest.
* Records the call to `tool_calls` collection.

Never raises uncaught: all failure modes return a `{ok: false, ...}` dict
so the LLM can see + recover.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import directory as _dir

from . import cache as _cache
from . import masking as _mask
from . import registry as _reg

logger = logging.getLogger(__name__)


class ToolForbidden(Exception):
    pass


def _role_of(session: Dict[str, Any]) -> str:
    """Resolve the caller's role from the auth/identity blob.

    Accepts either the legacy `role_state` (verified_employee / verified_client)
    or the modern (auth_state, session_type, identity) triple. Identity-derived
    type wins if present — that's the source of truth for the adapter.
    """
    ident = session.get("identity") or {}
    itype = (ident.get("type") or "").lower()
    if itype == "admin":
        return "admin"
    if itype in ("employee", "client"):
        return itype
    state = (session.get("role_state") or session.get("auth_state") or "").lower()
    stype = (session.get("session_type") or "").lower()
    if state == "verified":
        if stype == "employee":
            return "employee"
        if stype == "client":
            return "client"
    if state == "verified_employee":
        return "employee"
    if state in ("verified_client", "client"):
        return "client"
    if state == "admin":
        return "admin"
    return "visitor"


def _session_binding(session: Dict[str, Any], param: str, role: str) -> Optional[str]:
    """Resolve the auto-bound value (verified_ucc, employee_code, etc.)."""
    ident = session.get("identity") or {}
    if role == "client":
        if param == "ucc":
            return ident.get("ucc") or ident.get("verified_ucc")
        if param == "pan":
            return ident.get("pan") or ident.get("verified_pan")
    if role == "employee":
        if param == "employee_code":
            return ident.get("employee_id") or ident.get("employee_code")
        if param == "rm" or param == "rm_name":
            return ident.get("name")
    return None


async def _rm_owns_client(emp_session: Dict[str, Any], ucc: str) -> bool:
    """Verify the calling employee owns `ucc` in their RM book.

    We hit `/api/v1/bo/clients?rm=<emp.name>` once and cache the UCC list
    in-process per employee for 5 min. Defence-in-depth on top of any LLM
    guardrail.
    """
    ident = emp_session.get("identity") or {}
    name = ident.get("name") or ""
    if not name:
        return False
    cache_key = f"_rm_ucc_book::{name}"
    now = time.time()
    cached = getattr(_rm_owns_client, "_cache", {}).get(cache_key)
    if cached and cached[0] > now:
        return ucc in cached[1]
    try:
        data = await _dir._get(f"/bo/clients?rm={name}&limit=200")
        ucc_list = {c.get("client_code") for c in (data.get("clients") or []) if c.get("client_code")}
        ucc_list.update(c.get("ucc") for c in (data.get("clients") or []) if c.get("ucc"))
    except Exception:
        return False
    if not hasattr(_rm_owns_client, "_cache"):
        _rm_owns_client._cache = {}
    _rm_owns_client._cache[cache_key] = (now + 300, ucc_list)
    return ucc in ucc_list


def _params_to_path(path_template: str, params: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """Substitute `{ucc}` style path params; return (url, remaining_qs)."""
    url = path_template
    remaining = dict(params)
    for key, val in list(params.items()):
        token = "{" + key + "}"
        if token in url:
            url = url.replace(token, str(val))
            remaining.pop(key, None)
    # OrgLens base path is `/api/v1`; the manifest already includes that prefix.
    # `directory._get` expects paths beginning with `/`; strip the `/api/v1`
    # prefix because `_get` re-adds it.
    if url.startswith("/api/v1/"):
        url = url[len("/api/v1"):]
    return url, remaining


async def _record(db, *, turn_id: str, session_id: Optional[str],
                  tool_name: str, params_redacted: Dict[str, Any],
                  latency_ms: int, hit_cache: bool, ok: bool,
                  error_kind: Optional[str], role: str) -> None:
    if db is None:
        return
    try:
        await db.tool_calls.insert_one({
            "turn_id": turn_id,
            "session_id": session_id,
            "tool_name": tool_name,
            "params_redacted": params_redacted,
            "latency_ms": int(latency_ms),
            "hit_cache": bool(hit_cache),
            "ok": bool(ok),
            "error_kind": error_kind,
            "role_state": role,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        logger.exception("tool_calls insert failed (non-fatal)")


def _redact_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """For telemetry — never log raw PAN/UCC/email."""
    out = {}
    for k, v in (params or {}).items():
        if not v:
            out[k] = v
            continue
        lk = k.lower()
        if "pan" in lk and isinstance(v, str) and len(v) >= 10:
            out[k] = _mask.mask_pan(v)
        elif lk in ("ucc",) and isinstance(v, str) and len(v) > 3:
            out[k] = "***" + v[-3:]
        elif "email" in lk:
            out[k] = _mask.mask_email(str(v))
        else:
            out[k] = v
    return out


async def execute(
    db,
    *,
    tool_name: str,
    params: Dict[str, Any],
    session: Dict[str, Any],
    session_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Public entry. Returns `{ok, value|error, cache_hit, latency_ms, tool_name}`."""
    t0 = time.monotonic()
    turn_id = turn_id or str(uuid.uuid4())
    tool = _reg.get(tool_name)
    role = _role_of(session)

    if tool is None:
        await _record(db, turn_id=turn_id, session_id=session_id, tool_name=tool_name,
                      params_redacted=_redact_params(params), latency_ms=0,
                      hit_cache=False, ok=False, error_kind="unknown_tool", role=role)
        return {"ok": False, "tool_name": tool_name, "error": "unknown_tool"}

    # 1. role gate
    allowed = [r.lower() for r in tool.get("allowed_roles", [])]
    if role not in allowed:
        await _record(db, turn_id=turn_id, session_id=session_id, tool_name=tool_name,
                      params_redacted=_redact_params(params), latency_ms=0,
                      hit_cache=False, ok=False, error_kind="forbidden_role", role=role)
        # Log a security event for unauthorized tool attempts.
        try:
            import resilience as _r
            await _r.log_security_event(
                db, kind="unauthorized_tool_call", session_id=session_id,
                role_state_value=role,
                user_message=f"tool={tool_name} role={role} allowed={allowed}",
                action="tool_role_denied",
            )
        except Exception:
            pass
        return {"ok": False, "tool_name": tool_name, "error": "forbidden_role"}

    # 2. session binding (override LLM-supplied params for safety)
    params = dict(params or {})
    binds = tool.get("binds_session") or {}
    bound_key = binds.get(role)
    clamped_from: Optional[str] = None
    if bound_key:
        forced = _session_binding(session, bound_key, role)
        if not forced:
            await _record(db, turn_id=turn_id, session_id=session_id, tool_name=tool_name,
                          params_redacted=_redact_params(params), latency_ms=0,
                          hit_cache=False, ok=False, error_kind="session_binding_missing", role=role)
            return {"ok": False, "tool_name": tool_name, "error": "session_binding_missing"}
        if params.get(bound_key) and str(params[bound_key]) != str(forced):
            # LLM tried to use a different UCC/PAN — clamp + log a security event.
            clamped_from = str(params[bound_key])
            try:
                import resilience as _r
                await _r.log_security_event(
                    db, kind="cross_identity_attempt", session_id=session_id,
                    role_state_value=role,
                    user_message=f"tool={tool_name} bound={bound_key} llm={params[bound_key]} forced={forced}",
                    action="tool_param_clamped",
                )
            except Exception:
                pass
        params[bound_key] = forced

    # 3. employee RM-relationship check (only for client-financial tools)
    if role == "employee" and tool.get("employee_rm_check"):
        ucc = params.get("ucc")
        if ucc and not await _rm_owns_client(session, ucc):
            await _record(db, turn_id=turn_id, session_id=session_id, tool_name=tool_name,
                          params_redacted=_redact_params(params), latency_ms=int((time.monotonic()-t0)*1000),
                          hit_cache=False, ok=False, error_kind="not_in_rm_book", role=role)
            try:
                import resilience as _r
                await _r.log_security_event(
                    db, kind="rm_relationship_violation", session_id=session_id,
                    role_state_value=role,
                    user_message=f"tool={tool_name} ucc={ucc} emp={(session.get('identity') or {}).get('employee_id')}",
                    action="rm_book_denied",
                )
            except Exception:
                pass
            return {"ok": False, "tool_name": tool_name, "error": "not_in_rm_book"}

    # 4. cache lookup
    role_scope = role  # encode role so we never serve a client's cache row to a visitor
    cached = await _cache.get(db, tool_name=tool_name, params=params, role=role_scope)
    if cached is not None:
        latency_ms = int((time.monotonic() - t0) * 1000)
        await _record(db, turn_id=turn_id, session_id=session_id, tool_name=tool_name,
                      params_redacted=_redact_params(params), latency_ms=latency_ms,
                      hit_cache=True, ok=True, error_kind=None, role=role)
        result = {"ok": True, "tool_name": tool_name, "value": cached["value"],
                  "cache_hit": True, "cache_tier": cached.get("tier"),
                  "latency_ms": latency_ms}
        if clamped_from is not None:
            result["clamped"] = True
            result["clamped_from"] = clamped_from
            result["clamped_to"] = str(params.get(bound_key))
            result["clamp_note"] = ("The caller requested data for a different "
                                     "identifier; the system silently substituted the "
                                     "caller's own verified identifier. The data below "
                                     "belongs to the caller, NOT to the identifier they asked about.")
        return result

    # 5. live call to OrgLens (bypass `directory._shape_employee` to retain raw fields)
    url, qs = _params_to_path(tool["_path"], params)
    if qs:
        from urllib.parse import urlencode
        url = url + ("&" if "?" in url else "?") + urlencode({k: v for k, v in qs.items() if v is not None})
    try:
        raw = await _dir._get(url)
    except _dir.DirectoryUnavailable:
        latency_ms = int((time.monotonic() - t0) * 1000)
        await _record(db, turn_id=turn_id, session_id=session_id, tool_name=tool_name,
                      params_redacted=_redact_params(params), latency_ms=latency_ms,
                      hit_cache=False, ok=False, error_kind="orglens_unavailable", role=role)
        return {"ok": False, "tool_name": tool_name, "error": "orglens_unavailable"}
    except _dir.DirectoryForbidden:
        latency_ms = int((time.monotonic() - t0) * 1000)
        await _record(db, turn_id=turn_id, session_id=session_id, tool_name=tool_name,
                      params_redacted=_redact_params(params), latency_ms=latency_ms,
                      hit_cache=False, ok=False, error_kind="orglens_forbidden", role=role)
        return {"ok": False, "tool_name": tool_name, "error": "orglens_forbidden"}
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        await _record(db, turn_id=turn_id, session_id=session_id, tool_name=tool_name,
                      params_redacted=_redact_params(params), latency_ms=latency_ms,
                      hit_cache=False, ok=False, error_kind=f"unhandled:{type(e).__name__}", role=role)
        logger.exception("tool %s execute failed", tool_name)
        return {"ok": False, "tool_name": tool_name, "error": "execution_failed"}

    # 6. masking + redaction
    mask_fields = tool.get("mask_fields") or []
    if mask_fields:
        raw = _mask.apply_field_masks(raw, mask_fields)
    if role != "admin":
        redact_keys = tool.get("redact_for_non_admin") or []
        if redact_keys and isinstance(raw, dict):
            # Walk into known nested payloads as well.
            raw = _mask.redact_keys(raw, redact_keys)
            for k in ("employee", "client", "snapshot"):
                if isinstance(raw.get(k), dict):
                    raw[k] = _mask.redact_keys(raw[k], redact_keys)

    # 7. cache + record + return
    ttl = int(tool.get("cache_ttl_seconds") or 90)
    await _cache.put(db, tool_name=tool_name, params=params, role=role_scope,
                      value=raw, ttl_seconds=ttl)
    latency_ms = int((time.monotonic() - t0) * 1000)
    await _record(db, turn_id=turn_id, session_id=session_id, tool_name=tool_name,
                  params_redacted=_redact_params(params), latency_ms=latency_ms,
                  hit_cache=False, ok=True, error_kind=None, role=role)
    result = {"ok": True, "tool_name": tool_name, "value": raw,
              "cache_hit": False, "latency_ms": latency_ms}
    if clamped_from is not None:
        result["clamped"] = True
        result["clamped_from"] = clamped_from
        result["clamped_to"] = str(params.get(bound_key))
        result["clamp_note"] = ("The caller requested data for a different "
                                 "identifier; the system silently substituted the "
                                 "caller's own verified identifier. The data below "
                                 "belongs to the caller, NOT to the identifier they asked about.")
    return result
