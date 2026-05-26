"""Phase 26c — Dedicated email sender for dynamic form submissions.

Reuses Phase 19 SMTP config (Mongo-first, env fallback) but sends a
single dedicated email per submission to `FORMS_INBOX_EMAIL`
(default `brand@smifs.com`). Failure mode: write the submission to
disk under /tmp/form_drafts and mark the DB row `email_status=failed`
so the admin retry button can pick it up later.
"""
from __future__ import annotations
import os
import json
import smtplib
import logging
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates", "forms")

SUBJECT_BUILDERS = {
    "demand_capture":   lambda d: f"[Mackertich ONE / Demand] from {d.get('persona') or 'visitor'}",
    "referral_capture": lambda d: f"[Mackertich ONE / Referral] {(d.get('form_data') or {}).get('referrer_name','—')} → {(d.get('form_data') or {}).get('lead_name','—')}",
    "feedback_capture": lambda d: f"[Mackertich ONE / Feedback] {(d.get('form_data') or {}).get('rating','—')}★ from {d.get('persona') or 'visitor'}",
    "complaint_capture": lambda d: f"[URGENT-COMPLAINT] {(d.get('form_data') or {}).get('category','—')} from {d.get('persona') or 'visitor'} (Session {(d.get('session_id') or '')[:8]})",
    "callback_request": lambda d: f"[Mackertich ONE / Callback] {(d.get('form_data') or {}).get('name','—')} — {(d.get('form_data') or {}).get('preferred_time','—')}",
}


def _smtp_cfg_from_env() -> Optional[Dict[str, Any]]:
    if not os.environ.get("SMTP_HOST") or not os.environ.get("SMTP_USER"):
        return None
    return {
        "host":       os.environ["SMTP_HOST"],
        "port":       int(os.environ.get("SMTP_PORT") or 587),
        "starttls":   (os.environ.get("SMTP_STARTTLS") or "true").lower() != "false",
        "user":       os.environ["SMTP_USER"],
        "password":   os.environ.get("SMTP_PASSWORD") or "",
        "from_email": os.environ.get("FROM_EMAIL") or os.environ["SMTP_USER"],
        "from_name":  os.environ.get("FROM_NAME") or "Mackertich ONE",
    }


def _load_template(form_id: str) -> str:
    path = os.path.join(TEMPLATE_DIR, f"{form_id}.html")
    if not os.path.exists(path):
        return "<html><body><pre>{{json}}</pre></body></html>"
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _esc(s: Any) -> str:
    """Tiny HTML escape — avoids pulling in jinja."""
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _render_form_rows(fields: Dict[str, Any]) -> str:
    rows = []
    for k, v in fields.items():
        rows.append(
            f'<tr><td style="padding:6px 12px;border-bottom:1px solid #EEF2EE;'
            f'font-weight:600;color:#0B3B2C;width:34%">{_esc(k.replace("_"," ").title())}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #EEF2EE;color:#1A1A1A">{_esc(v) or "—"}</td></tr>'
        )
    return "\n".join(rows)


def _render_excerpt(turns: List[Dict[str, Any]]) -> str:
    if not turns:
        return '<p style="color:#888">No prior conversation captured.</p>'
    parts = []
    for t in turns[-8:]:
        role = t.get("role") or "?"
        content = t.get("content") or t.get("text") or ""
        bg = "#F1F5F2" if role == "user" else "#FFFFFF"
        parts.append(
            f'<div style="padding:8px 12px;background:{bg};margin:4px 0;border-radius:6px;'
            f'border-left:3px solid {"#065B40" if role == "user" else "#098C62"}">'
            f'<div style="font-size:11px;color:#0B3B2C;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:.05em">{_esc(role)}</div>'
            f'<div style="margin-top:4px;color:#1A1A1A;white-space:pre-wrap">{_esc(content[:600])}'
            f'{"…" if len(content) > 600 else ""}</div></div>'
        )
    return "\n".join(parts)


def _render(form_id: str, submission: Dict[str, Any]) -> str:
    tpl = _load_template(form_id)
    form_data = submission.get("form_data") or {}
    excerpt = submission.get("conversation_excerpt") or []
    ctx = {
        "form_id":         form_id,
        "submission_id":   submission.get("submission_id") or submission.get("_id") or "",
        "persona":         submission.get("persona") or "visitor",
        "session_id":      submission.get("session_id") or "",
        "submitted_at":    submission.get("submitted_at") or datetime.now(timezone.utc).isoformat(),
        "form_rows":       _render_form_rows(form_data),
        "conversation":    _render_excerpt(excerpt),
        "admin_link":      submission.get("admin_link") or "—",
        "raw_json":        _esc(json.dumps(form_data, indent=2, default=str)),
    }
    for k, v in ctx.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


def build_subject(submission: Dict[str, Any]) -> str:
    form_id = submission.get("form_id") or ""
    builder = SUBJECT_BUILDERS.get(form_id)
    if not builder:
        return f"[Mackertich ONE] {form_id}"
    try:
        return builder(submission)
    except Exception:
        return f"[Mackertich ONE] {form_id}"


def _write_draft(submission: Dict[str, Any], html: str) -> Optional[str]:
    try:
        out_dir = "/tmp/form_drafts"
        os.makedirs(out_dir, exist_ok=True)
        sid = submission.get("submission_id") or "no-id"
        path = os.path.join(out_dir, f"{sid}.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return path
    except Exception:
        logger.exception("write_draft failed")
        return None


def send(submission: Dict[str, Any]) -> Dict[str, Any]:
    """Send the submission email synchronously. Returns
    {ok, status: 'sent'|'pending'|'failed', detail: '...'}. Never raises.
    Caller is expected to fire-and-forget from an asyncio task.
    """
    cfg = _smtp_cfg_from_env()
    html = _render(submission.get("form_id", ""), submission)
    subject = build_subject(submission)
    to_addr = (os.environ.get("FORMS_INBOX_EMAIL") or "brand@smifs.com").strip()

    if cfg is None:
        draft = _write_draft(submission, html)
        return {"ok": False, "status": "pending", "detail": "SMTP not configured",
                "draft_path": draft}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg["from_name"], cfg["from_email"]))
    msg["To"] = to_addr
    # Plain-text fallback (strip tags crudely)
    import re as _re
    text = _re.sub(r"<[^>]+>", "", html)
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if cfg["starttls"]:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as s:
                s.ehlo()
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
                s.login(cfg["user"], cfg["password"])
                s.sendmail(cfg["from_email"], [to_addr], msg.as_string())
        else:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15,
                                  context=ssl.create_default_context()) as s:
                s.login(cfg["user"], cfg["password"])
                s.sendmail(cfg["from_email"], [to_addr], msg.as_string())
        return {"ok": True, "status": "sent", "detail": f"to {to_addr}"}
    except Exception as e:
        logger.exception("Form email send failed (form=%s)", submission.get("form_id"))
        draft = _write_draft(submission, html)
        return {"ok": False, "status": "failed",
                "detail": f"{type(e).__name__}: {e}", "draft_path": draft}
