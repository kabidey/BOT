"""Phase 14 — SMTP relay for sales-pipeline notifications.

Graceful no-op when SMTP env vars are missing — the upstream caller
(`sales_api.create_sale`) still returns 200 with the saved submission_id; only
the email delivery is skipped.

Env:
    SMTP_HOST                 e.g. smtp.office365.com
    SMTP_PORT                 default 587
    SMTP_USER                 mailbox user
    SMTP_PASSWORD             mailbox password
    FROM_EMAIL                envelope-from / display-from
    TO_EMAIL                  fallback destination (single address or comma list)
    TO_EMAIL_MUTUAL_FUND      per-product overrides — optional
    TO_EMAIL_AIF              ...
    TO_EMAIL_PMS
    TO_EMAIL_FD
    TO_EMAIL_INSURANCE

Logs every attempt: configured / skipped / sent / failed.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_TEMPLATE_PATH = Path(__file__).parent / "templates" / "sale_notification.html"
_PRODUCT_LABEL = {
    "mutual_fund": "Mutual Fund",
    "aif": "AIF",
    "pms": "PMS",
    "fd": "Fixed Deposit",
    "insurance": "Insurance",
    "ncd_primary": "NCD Primary Issue",
}
_PRODUCT_ENV = {
    "mutual_fund": "TO_EMAIL_MUTUAL_FUND",
    "aif": "TO_EMAIL_AIF",
    "pms": "TO_EMAIL_PMS",
    "fd": "TO_EMAIL_FD",
    "insurance": "TO_EMAIL_INSURANCE",
    "ncd_primary": "TO_EMAIL_NCD_PRIMARY",
}


def _is_configured() -> bool:
    return bool(
        os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")
        and os.environ.get("SMTP_PASSWORD") and os.environ.get("FROM_EMAIL")
        and (os.environ.get("TO_EMAIL") or any(os.environ.get(v) for v in _PRODUCT_ENV.values()))
    )


def _route_to(product: str) -> List[str]:
    per_product = os.environ.get(_PRODUCT_ENV.get(product, ""), "").strip()
    fallback = os.environ.get("TO_EMAIL", "").strip()
    candidate = per_product or fallback
    return [a.strip() for a in candidate.split(",") if a.strip()]


def _fmt_inr(value: Any) -> str:
    """₹ formatting with lakhs/crores suffix."""
    try:
        n = float(value or 0)
    except Exception:
        return str(value)
    if n >= 1e7:
        return f"₹{n/1e7:.2f} Cr"
    if n >= 1e5:
        return f"₹{n/1e5:.2f} L"
    return f"₹{n:,.0f}"


def _mask_pan(pan: Optional[str]) -> str:
    if not pan or len(pan) < 10:
        return pan or ""
    return f"{pan[:5]}{pan[5:9]}{pan[-1].upper()}"  # full PAN — internal email only


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


async def send_sale_notification(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Returns `{ok: bool, reason: str, recipients: [...]}`.

    Never raises — failures are logged and reported via the return dict so the
    sales endpoint can still return 200.
    """
    product = entry.get("product") or ""
    if not _is_configured():
        logger.info("SMTP not configured, skipping email for submission_id=%s",
                    entry.get("submission_id"))
        # Best-effort render so the user can inspect what WOULD have gone out.
        try:
            html = _render_html(entry)
            out_dir = Path("/app/deliverables/phase14/email_drafts")
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"{entry.get('submission_id','sale')}.html"
            out_file.write_text(html, encoding="utf-8")
            logger.info("Sale notification HTML rendered to disk: %s", out_file)
        except Exception:
            logger.exception("HTML render to disk failed (non-fatal)")
        return {"ok": False, "reason": "smtp_not_configured", "recipients": []}

    recipients = _route_to(product)
    if not recipients:
        logger.info("No recipient routing for product=%s, skipping email", product)
        return {"ok": False, "reason": "no_recipient", "recipients": []}

    subject = (
        f"[Mackertich ONE] New {_PRODUCT_LABEL.get(product, product.title())} "
        f"sale logged · {_fmt_inr(entry.get('amount_inr'))} · "
        f"by {(entry.get('employee') or {}).get('name') or 'unknown'}"
    )
    # Phase 15 — NCD primary issue gets a Sales-Ops-styled subject so the
    # mailbox rules can route NCD applications to the bond-desk inbox.
    if product == "ncd_primary":
        client_name = (entry.get("client") or {}).get("client_name") or "unknown"
        amount = (entry.get("product_details") or {}).get("application_amount_inr") or entry.get("amount_inr")
        subject = (
            f"[SMIFS Sales-Ops] NCD Primary Issue — {client_name} — "
            f"{_fmt_inr(amount)}"
        )
    html = _render_html(entry)

    # Late-import aiosmtplib so the module loads even when the dep is missing.
    try:
        import aiosmtplib
        from email.message import EmailMessage
    except ImportError:
        logger.error("aiosmtplib not installed — install requirements.txt")
        return {"ok": False, "reason": "aiosmtplib_missing", "recipients": recipients}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["FROM_EMAIL"]
    msg["To"] = ", ".join(recipients)
    msg.set_content("This is the Mackertich ONE sale-pipeline notification. "
                    "Please view in an HTML-capable mail client.")
    msg.add_alternative(html, subtype="html")

    try:
        await aiosmtplib.send(
            msg,
            hostname=os.environ["SMTP_HOST"],
            port=int(os.environ.get("SMTP_PORT") or 587),
            username=os.environ["SMTP_USER"],
            password=os.environ["SMTP_PASSWORD"],
            start_tls=True,
            timeout=15,
        )
        logger.info("Sale notification sent: submission_id=%s to=%s",
                    entry.get("submission_id"), recipients)
        return {"ok": True, "reason": "sent", "recipients": recipients}
    except Exception as e:
        logger.exception("SMTP send failed for %s: %s", entry.get("submission_id"), e)
        return {"ok": False, "reason": f"smtp_error:{type(e).__name__}",
                "recipients": recipients}
