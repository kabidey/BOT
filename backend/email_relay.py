"""Phase 19 — Live Office 365 SMTP relay + hierarchy-aware CC routing.

Phase 14 originated this module; Phase 19 rewrites the recipient resolution
to derive a *dynamic* CC chain from OrgLens. The submitting employee is the
sole TO; their reporting chain (Manager → L1 → L2 → … up to 10 levels) plus
a fixed Sales-Ops list (`CC_OPS_FIXED`) becomes CC.

Operational guarantees
======================
* `SMTP_PASSWORD` MUST never appear in any log line, screenshot, or response
  body. We only ever read it from `os.environ` and feed it directly into
  `aiosmtplib.send`. Any defensive log scrub still drops accidental leaks.
* The OrgLens chain walk is cached for 1 hour per `employee_id`. Cache miss /
  OrgLens 5xx during walk logs a `security_events` row of kind
  `email_relay_hierarchy_unresolved` and still attempts the send to (TO +
  fixed ops) so the back-office team isn't blocked.
* Send failure modes are surfaced as four statuses on `email_status`:
    - `sent`                      — SMTP relay accepted the message
    - `draft_only`                — SMTP not configured (or no recipients):
                                    HTML draft written to disk only
    - `smtp_auth_disabled`        — `aiosmtplib.SMTPAuthenticationError`:
                                    O365 Basic Auth disabled / wrong creds.
                                    HTML draft written as a fallback.
    - `failed_with_fallback`      — Any other SMTP / network error: HTML
                                    draft written, security event logged.

Env contract
============
    SMTP_HOST                 e.g. smtp.office365.com
    SMTP_PORT                 default 587
    SMTP_STARTTLS             "true"/"false" (default true)
    SMTP_USER                 mailbox login (e.g. wealth.guidance@smifs.com)
    SMTP_PASSWORD             mailbox password — NEVER logged
    FROM_EMAIL                envelope-from
    FROM_NAME                 optional display name
    CC_OPS_FIXED              comma-separated fixed ops CC list
    TO_EMAIL_MUTUAL_FUND ...  Legacy per-product fallbacks. Used ONLY when
                               the submitter has no plaintext email on file.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from email.utils import formataddr
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


_TEMPLATE_PATH = Path(__file__).parent / "templates" / "sale_notification.html"
_DRAFT_DIR = Path("/app/deliverables/phase14/email_drafts")
_PRODUCT_LABEL = {
    "mutual_fund": "Mutual Fund",
    "aif": "AIF",
    "pms": "PMS",
    "fd": "Fixed Deposit",
    "insurance": "Insurance",
    "ncd_primary": "NCD Primary Issue",
    "sif": "SIF",
}
_LEGACY_PRODUCT_ENV = {
    "mutual_fund": "TO_EMAIL_MUTUAL_FUND",
    "aif": "TO_EMAIL_AIF",
    "pms": "TO_EMAIL_PMS",
    "fd": "TO_EMAIL_FD",
    "insurance": "TO_EMAIL_INSURANCE",
    "ncd_primary": "TO_EMAIL_NCD_PRIMARY",
    "sif": "TO_EMAIL_SIF",
}

# Phase 19 — 1-hour TTL chain cache. Key = employee_id (string).
# Value = (expires_at_epoch_seconds, payload_dict).
_CHAIN_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CHAIN_CACHE_TTL_SECONDS = 3600
_CHAIN_MAX_HOPS = 10
_CHAIN_LOCK = asyncio.Lock()

# Phase 19 — in-process ring buffer of the most recent send attempts for the
# `/api/admin/email_relay/status` panel. Bounded; oldest entries are dropped.
_RECENT_ATTEMPTS: List[Dict[str, Any]] = []
_RECENT_ATTEMPTS_MAX = 25


# ----------------------- helpers ---------------------------------------------

def _mask_password_in(text: str) -> str:
    """Defensive scrubber against the env-sourced password. Mongo-sourced
    passwords are scrubbed via `_scrub_with(text, cfg['password'])` from
    the active config dict (kept in-process only).
    """
    pw = os.environ.get("SMTP_PASSWORD") or ""
    if pw and pw in text:
        return text.replace(pw, "***")
    return text


def _scrub_with(text: str, pwd: str) -> str:
    """Phase 19.2 — scrub against the in-process plaintext password
    (Mongo-sourced via `email_relay_config.get_smtp_config`)."""
    out = text or ""
    for candidate in (pwd, os.environ.get("SMTP_PASSWORD") or ""):
        if candidate and candidate in out:
            out = out.replace(candidate, "***")
    return out


def _is_configured() -> bool:
    """Env-only configuration check (legacy fast-path). `send_sale_notification`
    no longer calls this; it goes through `email_relay_config.get_smtp_config`
    which honours the Mongo config when present."""
    return bool(
        os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")
        and os.environ.get("SMTP_PASSWORD") and os.environ.get("FROM_EMAIL")
    )


def _ops_cc(cfg_ops: Optional[List[str]] = None) -> List[str]:
    """Phase 19.2 — prefer the explicit cfg_ops (typically pulled from the
    Mongo `app_config` doc) over the legacy env var. Falls back to env when
    `cfg_ops` is None / empty (preserves Phase 14/19 behaviour)."""
    if cfg_ops:
        return [a.strip().lower() for a in cfg_ops if a and a.strip()]
    raw = os.environ.get("CC_OPS_FIXED", "")
    return [a.strip().lower() for a in raw.split(",") if a.strip()]


def _legacy_route_to(product: str, subtype: Optional[str] = None) -> List[str]:
    """Legacy per-product TO fallback (used only when the submitter has no
    plaintext email — should be vanishingly rare with verified-employee
    sessions, but keeps Phase 14 behaviour intact).

    Phase 21 — optional `TO_EMAIL_PMS_APRN_TRANSFER` override routes PMS
    APRN-transfer notifications to a dedicated mailbox if set.
    """
    candidate = ""
    if subtype == "aprn_transfer" and product == "pms":
        candidate = os.environ.get("TO_EMAIL_PMS_APRN_TRANSFER", "").strip()
    if not candidate:
        candidate = os.environ.get(_LEGACY_PRODUCT_ENV.get(product, ""), "").strip()
    if not candidate:
        candidate = os.environ.get("TO_EMAIL", "").strip()
    return [a.strip() for a in candidate.split(",") if a.strip()]


def _fmt_inr(value: Any) -> str:
    try:
        n = float(value or 0)
    except Exception:
        return str(value)
    if n >= 1e7:
        return f"₹{n/1e7:.2f} Cr"
    if n >= 1e5:
        return f"₹{n/1e5:.2f} L"
    return f"₹{n:,.0f}"


def _now_epoch() -> float:
    return time.time()


def _record_attempt(rec: Dict[str, Any]) -> None:
    _RECENT_ATTEMPTS.append(rec)
    if len(_RECENT_ATTEMPTS) > _RECENT_ATTEMPTS_MAX:
        del _RECENT_ATTEMPTS[0 : len(_RECENT_ATTEMPTS) - _RECENT_ATTEMPTS_MAX]


# ----------------------- chain resolution ------------------------------------

async def _fetch_raw_employee(code: str) -> Optional[Dict[str, Any]]:
    """Direct OrgLens call that preserves raw `email` and `reports_to_*`
    fields (i.e., bypasses `directory._shape_employee` which strips them).
    Used only by the email relay, never echoed into chat surfaces.
    """
    import directory as _dir
    try:
        data = await _dir._get(f"/employee/by-code/{code}")
        return data.get("employee") if isinstance(data, dict) else None
    except _dir.DirectoryUnavailable:
        logger.warning("OrgLens unavailable for code=%s", code)
        return None
    except _dir.DirectoryForbidden:
        logger.warning("OrgLens forbidden for code=%s (employees:pii missing)", code)
        return None
    except _dir.DirectoryRateLimited:
        logger.warning("OrgLens rate-limited for code=%s", code)
        return None
    except Exception:
        logger.exception("OrgLens fetch failed for code=%s", code)
        return None


async def _walk_chain(employee_id: str) -> Dict[str, Any]:
    """Walk `reports_to_employee_id` upward from `employee_id`, capped at
    `_CHAIN_MAX_HOPS`. Returns `{chain: [...], max_hops_reached: bool,
    errors: [...]}` — `chain` does NOT include the submitter at index 0;
    index 0 is the direct manager (level 1)."""
    chain: List[Dict[str, Any]] = []
    errors: List[str] = []
    seen = set()

    submitter = await _fetch_raw_employee(employee_id)
    if not submitter:
        errors.append(f"submitter_not_found:{employee_id}")
        return {"chain": [], "max_hops_reached": False, "errors": errors,
                "submitter": None}

    code = submitter.get("reports_to_employee_id")
    submitter_view = {
        "employee_id": submitter.get("employee_id"),
        "name": submitter.get("name"),
        "email": (submitter.get("email") or "").strip().lower() or None,
        "designation": submitter.get("designation"),
    }

    hops = 0
    max_hops_reached = False
    while code and hops < _CHAIN_MAX_HOPS:
        if code in seen:
            errors.append(f"cycle_detected:{code}")
            break
        seen.add(code)
        rec = await _fetch_raw_employee(code)
        if not rec:
            errors.append(f"hop_unresolved:{code}")
            break
        chain.append({
            "level": hops + 1,
            "employee_id": rec.get("employee_id"),
            "name": rec.get("name"),
            "email": (rec.get("email") or "").strip().lower() or None,
            "designation": rec.get("designation"),
        })
        nxt = rec.get("reports_to_employee_id")
        if not nxt or nxt == rec.get("employee_id"):
            break
        code = nxt
        hops += 1
        if hops == _CHAIN_MAX_HOPS:
            # We hit the cap and there's still a `reports_to_*` pointer above.
            max_hops_reached = True

    return {"chain": chain, "max_hops_reached": max_hops_reached,
            "errors": errors, "submitter": submitter_view}


async def resolve_recipient_chain(
    employee_id: str,
    employee_email: Optional[str] = None,
    db=None,
    *,
    force_refresh: bool = False,
    ops_cc_override: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Public — returns a structured routing payload:

        {
          "employee_id": "SMWM-...",
          "to": ["submitter@smifs.com"],
          "cc": ["manager@smifs.com", ..., "ho.operations@smifs.com", ...],
          "chain": [{"level": 1, "employee_id": "...", "name": "...",
                     "email": "...", "designation": "..."}, ...],
          "ops_cc": [...],
          "max_hops_reached": bool,
          "cache_hit": bool,
          "resolved_at": "<ISO>",
          "errors": [...],
        }

    Cache hits are tagged `cache_hit=True` and serve the same payload up to
    1 hour. On chain resolution errors (OrgLens 5xx, employee missing) we
    still return a valid payload with `to` populated from the submitter so
    the back-office still gets the email — and the failure is recorded in
    `security_events` (kind=`email_relay_hierarchy_unresolved`) when a `db`
    handle is provided.

    Phase 19.2 — `ops_cc_override` lets the caller inject the fixed ops list
    pulled from the Mongo `app_config` doc so the chain payload reflects the
    UI-managed config rather than the legacy `CC_OPS_FIXED` env var.
    """
    employee_id = (employee_id or "").strip()
    if not employee_id:
        return {
            "employee_id": "",
            "to": [employee_email.lower()] if employee_email else [],
            "cc": _ops_cc(ops_cc_override),
            "chain": [],
            "ops_cc": _ops_cc(ops_cc_override),
            "max_hops_reached": False,
            "cache_hit": False,
            "resolved_at": _iso_now(),
            "errors": ["no_employee_id"],
        }

    # Cache lookup (1h TTL). NOTE: cached payloads are tied to a specific
    # ops_cc snapshot; if the ops list has changed we still serve the cache
    # but refresh the `ops_cc`/`cc` fields below.
    if not force_refresh:
        hit = _CHAIN_CACHE.get(employee_id)
        if hit and hit[0] > _now_epoch():
            payload = {**hit[1], "cache_hit": True}
            # Refresh ops_cc with the live override so a Mongo-config change
            # propagates without waiting a full hour.
            if ops_cc_override is not None:
                live_ops = _ops_cc(ops_cc_override)
                payload = dict(payload)
                payload["ops_cc"] = live_ops
                chain_emails = [c["email"] for c in payload.get("chain") or [] if c.get("email")]
                seen = set(payload.get("to") or [])
                cc_list: List[str] = []
                for e in chain_emails + live_ops:
                    if e and e not in seen:
                        cc_list.append(e)
                        seen.add(e)
                payload["cc"] = cc_list
            return payload

    async with _CHAIN_LOCK:
        # Double-check inside the lock to avoid duplicate walks under load.
        if not force_refresh:
            hit = _CHAIN_CACHE.get(employee_id)
            if hit and hit[0] > _now_epoch():
                return {**hit[1], "cache_hit": True}
        walk = await _walk_chain(employee_id)

        submitter = walk.get("submitter") or {}
        chain = walk.get("chain") or []
        errors = walk.get("errors") or []

        # TO = submitter email if known; else session-supplied email; else legacy.
        submitter_email = (submitter.get("email")
                           or (employee_email or "").strip().lower() or "")
        to_list = [submitter_email] if submitter_email else []

        chain_emails = [c["email"] for c in chain if c.get("email")]
        ops = _ops_cc(ops_cc_override)
        # De-dupe & strip the submitter from CC if it ever appears (defence).
        cc_list: List[str] = []
        seen_cc = set(to_list)
        for e in chain_emails + ops:
            if e and e not in seen_cc:
                cc_list.append(e)
                seen_cc.add(e)

        payload = {
            "employee_id": employee_id,
            "to": to_list,
            "cc": cc_list,
            "chain": chain,
            "ops_cc": ops,
            "max_hops_reached": walk.get("max_hops_reached", False),
            "cache_hit": False,
            "resolved_at": _iso_now(),
            "errors": errors,
        }

        # Cache successful & partial resolutions for the full TTL — errors
        # are typically tenant-config issues (e.g., chain hop missing) that
        # don't self-heal on retry within an hour.
        _CHAIN_CACHE[employee_id] = (_now_epoch() + _CHAIN_CACHE_TTL_SECONDS,
                                     payload)

        # Persist a security event when the chain walk was incomplete so the
        # admin panel surfaces it (kind handled in resilience.py).
        if errors and db is not None:
            try:
                import resilience as _r
                await _r.log_security_event(
                    db,
                    kind="email_relay_hierarchy_unresolved",
                    session_id=None,
                    role_state_value="system",
                    user_message=f"employee_id={employee_id} errors={','.join(errors)[:200]}",
                    action="chain_partial_resolution",
                )
            except Exception:
                logger.exception("security_events insert failed (non-fatal)")

        return payload


def chain_cache_snapshot() -> Dict[str, Any]:
    """Used by the status endpoint."""
    now = _now_epoch()
    items = []
    for emp_id, (exp, payload) in list(_CHAIN_CACHE.items()):
        items.append({
            "employee_id": emp_id,
            "expires_in_seconds": max(0, int(exp - now)),
            "chain_levels": len(payload.get("chain") or []),
            "cached_at_iso": payload.get("resolved_at"),
        })
    return {"size": len(items), "ttl_seconds": _CHAIN_CACHE_TTL_SECONDS,
            "items": items[:50]}


def recent_attempts() -> List[Dict[str, Any]]:
    """Newest-first."""
    return list(reversed(_RECENT_ATTEMPTS))


# ----------------------- template render -------------------------------------

def _mask_pan(pan: Optional[str]) -> str:
    if not pan or len(pan) < 10:
        return pan or ""
    return f"{pan[:5]}{pan[5:9]}{pan[-1].upper()}"


def _render_html(entry: Dict[str, Any]) -> str:
    """Pure-string template interpolation — no Jinja dependency."""
    try:
        tpl = _TEMPLATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        tpl = "<p>{{TITLE}}</p><pre>{{DUMP}}</pre>"

    product = entry.get("product") or ""
    product_label = _PRODUCT_LABEL.get(product, product.title())
    employee = entry.get("employee") or {}
    client = entry.get("client") or {}
    details = entry.get("product_details") or {}
    amount_str = _fmt_inr(entry.get("amount_inr"))

    def _row(label: str, value: Any) -> str:
        if value is None or value == "" or value == []:
            return ""
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        return (
            f"<tr><td style='padding:8px 14px;color:#808080;"
            f"border-bottom:1px solid #E8F5EF;width:38%;'>{label}</td>"
            f"<td style='padding:8px 14px;color:#191A15;"
            f"border-bottom:1px solid #E8F5EF;'>{value}</td></tr>"
        )

    client_rows = (
        _row("Client name", client.get("client_name"))
        + _row("Client PAN", client.get("client_pan"))
        + _row("Client phone", client.get("client_phone"))
        + _row("Client email", client.get("client_email"))
    )
    if details.get("arn_transfer"):
        arn = details.pop("arn_transfer", {}) or {}
        # Phase 21 — extended key list covers MF + AIF + SIF flavours. Legacy
        # rows may still carry `existing_arn`/`new_arn`/`transfer_effective_date`
        # — keep them in the loop so old email re-sends render the way they
        # were captured.
        for k in ("existing_arn", "new_arn", "folio_numbers",
                  "amc_name", "scheme_name", "aif_name", "sif_name",
                  "commitment_account_id", "folio_account_id",
                  "transfer_effective_date", "aum_inr", "arn_remarks"):
            if k in arn:
                details.setdefault(k, arn[k])
        details.setdefault("subtype", "ARN Transfer")
    if details.get("aprn_transfer"):
        # Phase 21 — PMS APRN Transfer.
        aprn = details.pop("aprn_transfer", {}) or {}
        for k in ("pms_provider", "strategy_name", "portfolio_account_id",
                  "corpus_inr", "aprn_remarks"):
            if k in aprn:
                details.setdefault(k, aprn[k])
        details.setdefault("subtype", "APRN Transfer")
    if entry.get("vehicle_name"):
        details.setdefault("deck_vehicle", entry["vehicle_name"])
    product_rows = "".join(_row(k.replace("_", " ").title(), v) for k, v in details.items())
    common_rows = (
        _row("Amount (INR)", amount_str)
        + _row("Expected login date", entry.get("expected_login_date"))
        + _row("Expected payment date", entry.get("expected_payment_date"))
        + _row("Remarks", entry.get("remarks"))
    )
    employee_rows = (
        _row("Submitted by", employee.get("name"))
        + _row("Employee ID", employee.get("employee_id"))
        + _row("Designation", employee.get("designation"))
        + _row("Department", employee.get("department"))
        + _row("Work email", employee.get("email"))
    )

    return (
        tpl
        .replace("{{SUBMISSION_ID}}", str(entry.get("submission_id") or ""))
        .replace("{{PRODUCT_LABEL}}", product_label)
        .replace("{{AMOUNT}}", amount_str)
        .replace("{{EMPLOYEE_NAME}}", str(employee.get("name") or "—"))
        .replace("{{CREATED_AT}}", str(entry.get("created_at") or ""))
        .replace("{{CLIENT_ROWS}}", client_rows)
        .replace("{{PRODUCT_ROWS}}", product_rows)
        .replace("{{COMMON_ROWS}}", common_rows)
        .replace("{{EMPLOYEE_ROWS}}", employee_rows)
        .replace("{{ADMIN_URL}}", os.environ.get("ADMIN_BASE_URL", "") + "/admin?tab=sales")
    )


def _write_draft(entry: Dict[str, Any], html: str) -> Optional[str]:
    """Write the rendered HTML to `/app/deliverables/phase14/email_drafts/`.
    Used as a fallback when SMTP send fails or is disabled.
    """
    try:
        _DRAFT_DIR.mkdir(parents=True, exist_ok=True)
        out_file = _DRAFT_DIR / f"{entry.get('submission_id','sale')}.html"
        out_file.write_text(html, encoding="utf-8")
        return str(out_file)
    except Exception:
        logger.exception("HTML draft write failed (non-fatal)")
        return None


# ----------------------- send -------------------------------------------------

def _iso_now() -> str:
    from datetime import datetime, timezone as _tz
    return datetime.now(_tz.utc).isoformat()


def _build_subject(entry: Dict[str, Any]) -> str:
    product = entry.get("product") or ""
    subject = (
        f"[SMIFS Sales-Ops] New {_PRODUCT_LABEL.get(product, product.title())} "
        f"sale logged · {_fmt_inr(entry.get('amount_inr'))} · "
        f"by {(entry.get('employee') or {}).get('name') or 'unknown'}"
    )
    if product == "ncd_primary":
        client_name = (entry.get("client") or {}).get("client_name") or "unknown"
        amount = (entry.get("product_details") or {}).get("application_amount_inr") or entry.get("amount_inr")
        subject = (
            f"[SMIFS Sales-Ops] NCD Primary Issue — {client_name} — "
            f"{_fmt_inr(amount)}"
        )
    # Phase 17 / 21 — ARN Transfer family: MF / AIF / SIF use the
    # `arn_transfer` subtype; product label changes the subject prefix.
    if entry.get("subtype") == "arn_transfer":
        client_name = (entry.get("client") or {}).get("client_name") or "unknown"
        arn = (entry.get("product_details") or {}).get("arn_transfer") or {}
        amount = arn.get("aum_inr") or entry.get("amount_inr")
        product_label = {
            "mutual_fund": "MF",
            "aif":         "AIF",
            "sif":         "SIF",
        }.get(product, _PRODUCT_LABEL.get(product, product.title()))
        subject = (
            f"[SMIFS Sales-Ops] {product_label} — ARN Transfer — {client_name} — "
            f"{_fmt_inr(amount)}"
        )
    # Phase 21 — PMS APRN Transfer (separate subtype).
    if entry.get("subtype") == "aprn_transfer":
        client_name = (entry.get("client") or {}).get("client_name") or "unknown"
        aprn = (entry.get("product_details") or {}).get("aprn_transfer") or {}
        amount = aprn.get("corpus_inr") or entry.get("amount_inr")
        subject = (
            f"[SMIFS Sales-Ops] PMS — APRN Transfer — {client_name} — "
            f"{_fmt_inr(amount)}"
        )
    return subject


async def send_sale_notification(entry: Dict[str, Any], db=None) -> Dict[str, Any]:
    """Returns `{ok, reason, recipients, routing}` — never raises.

    `routing` is the rich structured payload (TO + CC + chain). `recipients`
    is the flat de-duped list (TO ∪ CC) kept for backward compatibility with
    Phase 14 callers / dashboards.

    The four `reason` values used by Phase 19:
        * "sent"
        * "draft_only"            (SMTP disabled or no recipients)
        * "smtp_auth_disabled"    (O365 Basic Auth refused)
        * "failed_with_fallback"  (other SMTP / network error)
    """
    submission_id = entry.get("submission_id")
    employee = entry.get("employee") or {}
    product = entry.get("product") or ""
    started_at = _iso_now()

    # --- 0. resolve the active SMTP config (Mongo first, env fallback) ---
    import email_relay_config as _cfg_mod
    cfg = await _cfg_mod.get_smtp_config(db) if db is not None else None
    if cfg is None and _is_configured():
        # No db handle but env is wired — synthesize a legacy cfg so the rest
        # of the function can be uniform.
        cfg = {
            "host": os.environ["SMTP_HOST"],
            "port": int(os.environ.get("SMTP_PORT") or 587),
            "starttls": (os.environ.get("SMTP_STARTTLS") or "true").lower() != "false",
            "user": os.environ["SMTP_USER"],
            "password": os.environ["SMTP_PASSWORD"],
            "from_email": os.environ["FROM_EMAIL"],
            "from_name": os.environ.get("FROM_NAME") or "SMIFS Sales-Ops",
            "cc_ops_fixed": [a.strip().lower() for a in (os.environ.get("CC_OPS_FIXED") or "").split(",") if a.strip()],
            "source": "env",
        }
    ops_cc_override = (cfg or {}).get("cc_ops_fixed") if cfg else None

    # --- 1. resolve TO + CC ---
    routing = await resolve_recipient_chain(
        employee_id=(employee.get("employee_id") or "").strip(),
        employee_email=(employee.get("email") or "").strip().lower() or None,
        db=db,
        ops_cc_override=ops_cc_override,
    )

    # Legacy fallback: if the submitter genuinely has no resolvable email,
    # fall back to the product-specific TO_EMAIL_* env so the back-office
    # still receives the notification (rare; verified-employee flow gives
    # us a plaintext work email almost always).
    if not routing["to"]:
        legacy = _legacy_route_to(product, entry.get("subtype"))
        if legacy:
            routing["to"] = legacy
            routing["errors"] = (routing.get("errors") or []) + ["legacy_to_used"]

    flat_recipients = list(routing["to"]) + list(routing["cc"])

    # --- 2. always render the HTML so we can fallback to draft ---
    html = _render_html(entry)

    # --- 3. SMTP not configured → draft_only ---
    if cfg is None:
        draft_path = _write_draft(entry, html)
        logger.info("SMTP not configured — wrote draft for %s (recipients=%s)",
                    submission_id, len(flat_recipients))
        result = {
            "ok": False, "reason": "draft_only", "recipients": flat_recipients,
            "routing": routing, "draft_path": draft_path,
        }
        _record_attempt({
            "submission_id": submission_id, "started_at": started_at,
            "ended_at": _iso_now(), "reason": "draft_only",
            "to": routing["to"], "cc_count": len(routing["cc"]),
            "chain_levels": len(routing["chain"]),
        })
        return result

    if not routing["to"]:
        draft_path = _write_draft(entry, html)
        logger.info("No TO recipient resolved for %s — wrote draft", submission_id)
        result = {
            "ok": False, "reason": "draft_only", "recipients": flat_recipients,
            "routing": routing, "draft_path": draft_path,
        }
        _record_attempt({
            "submission_id": submission_id, "started_at": started_at,
            "ended_at": _iso_now(), "reason": "draft_only",
            "to": [], "cc_count": len(routing["cc"]),
            "chain_levels": len(routing["chain"]),
        })
        return result

    # --- 4. SMTP send ---
    try:
        import aiosmtplib
        from email.message import EmailMessage
    except ImportError:
        logger.error("aiosmtplib not installed — install requirements.txt")
        draft_path = _write_draft(entry, html)
        return {
            "ok": False, "reason": "failed_with_fallback",
            "recipients": flat_recipients, "routing": routing,
            "draft_path": draft_path, "error": "aiosmtplib_missing",
        }

    msg = EmailMessage()
    msg["Subject"] = _build_subject(entry)
    msg["From"] = formataddr(
        (cfg.get("from_name") or "SMIFS Sales-Ops", cfg["from_email"]),
    )
    msg["To"] = ", ".join(routing["to"])
    if routing["cc"]:
        msg["Cc"] = ", ".join(routing["cc"])
    msg.set_content(
        "SMIFS Sales-Ops sale-pipeline notification. "
        "Please view this email in an HTML-capable mail client.",
    )
    msg.add_alternative(html, subtype="html")

    smtp_host = cfg["host"]
    smtp_port = int(cfg.get("port") or 587)
    smtp_starttls = bool(cfg.get("starttls", True))
    smtp_user = cfg["user"]
    smtp_password = cfg["password"]  # NEVER log; sourced from Mongo (decrypted) or env

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=smtp_port,
            username=smtp_user,
            password=smtp_password,
            start_tls=smtp_starttls,
            timeout=30,
            recipients=routing["to"] + routing["cc"],
        )
        logger.info(
            "Sale notification SENT submission_id=%s to=%d cc=%d host=%s source=%s",
            submission_id, len(routing["to"]), len(routing["cc"]), smtp_host,
            cfg.get("source") or "env",
        )
        _record_attempt({
            "submission_id": submission_id, "started_at": started_at,
            "ended_at": _iso_now(), "reason": "sent",
            "to": routing["to"], "cc_count": len(routing["cc"]),
            "chain_levels": len(routing["chain"]),
        })
        return {
            "ok": True, "reason": "sent", "recipients": flat_recipients,
            "routing": routing, "draft_path": None,
        }

    except Exception as e:
        # Late-classify the exception. aiosmtplib defines
        # SMTPAuthenticationError; older versions may emit a generic
        # SMTPException with status_code=535. We branch on both.
        exc_name = type(e).__name__
        is_auth_error = False
        try:
            import aiosmtplib
            if isinstance(e, getattr(aiosmtplib, "SMTPAuthenticationError",
                                     tuple())):
                is_auth_error = True
        except Exception:
            pass
        # Fallback heuristic: O365 Basic-Auth-disabled message ("535 5.7.139
        # Authentication unsuccessful, basic authentication is disabled").
        msg_text = _scrub_with(str(e), smtp_password)
        if "535" in msg_text or "basic authentication is disabled" in msg_text.lower():
            is_auth_error = True

        draft_path = _write_draft(entry, html)
        reason = "smtp_auth_disabled" if is_auth_error else "failed_with_fallback"
        kind = ("email_relay_basic_auth_disabled" if is_auth_error
                else "email_relay_send_failed")
        logger.warning(
            "Sale notification %s submission_id=%s exc=%s msg=%s",
            reason, submission_id, exc_name, msg_text[:300],
        )

        if db is not None:
            try:
                import resilience as _r
                await _r.log_security_event(
                    db,
                    kind=kind,
                    session_id=entry.get("session_id"),
                    role_state_value="employee",
                    user_message=(
                        f"submission_id={submission_id} exc={exc_name} "
                        f"msg={msg_text[:200]}"
                    ),
                    action="email_relay_fallback_to_draft",
                )
            except Exception:
                logger.exception("security_events insert failed (non-fatal)")

        _record_attempt({
            "submission_id": submission_id, "started_at": started_at,
            "ended_at": _iso_now(), "reason": reason,
            "to": routing["to"], "cc_count": len(routing["cc"]),
            "chain_levels": len(routing["chain"]),
            "exc": exc_name,
        })
        return {
            "ok": False, "reason": reason, "recipients": flat_recipients,
            "routing": routing, "draft_path": draft_path,
            "error": exc_name,
        }


# ----------------------- status panel ----------------------------------------

async def relay_status(db=None) -> Dict[str, Any]:
    """Snapshot consumed by `/api/admin/email_relay/status`.

    Phase 19.2 — pulls the live config via `email_relay_config.get_smtp_config`
    so a Mongo-stored config is reflected. Falls back to env values when
    nothing is set in Mongo.

    Never leaks the password — only `password_set: bool` is exposed.
    """
    import email_relay_config as _cfg_mod
    cfg = await _cfg_mod.get_smtp_config(db) if db is not None else None
    if cfg is None and _is_configured():
        # Synthesize env view when db isn't available.
        cfg = {
            "host": os.environ.get("SMTP_HOST"),
            "port": int(os.environ.get("SMTP_PORT") or 587),
            "starttls": (os.environ.get("SMTP_STARTTLS") or "true").lower() != "false",
            "user": os.environ.get("SMTP_USER"),
            "password": os.environ.get("SMTP_PASSWORD") or "",
            "from_email": os.environ.get("FROM_EMAIL"),
            "from_name": os.environ.get("FROM_NAME"),
            "cc_ops_fixed": _ops_cc(),
            "source": "env",
        }
    configured = bool(cfg and cfg.get("host") and cfg.get("user")
                      and cfg.get("password") and cfg.get("from_email"))
    return {
        "configured": configured,
        "source": (cfg or {}).get("source") or "none",
        "host": (cfg or {}).get("host"),
        "port": (cfg or {}).get("port"),
        "starttls": bool((cfg or {}).get("starttls", True)),
        "user": (cfg or {}).get("user"),
        "password_set": bool((cfg or {}).get("password")),
        "from_email": (cfg or {}).get("from_email"),
        "from_name": (cfg or {}).get("from_name"),
        "ops_cc_fixed": list((cfg or {}).get("cc_ops_fixed") or []),
        "chain_cache": chain_cache_snapshot(),
        "recent_attempts": recent_attempts(),
    }
