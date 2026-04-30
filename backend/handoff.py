"""Phase 11 — One-tap handoff builder.

Given a verified session, produce a WhatsApp / email deep-link to the correct
target (client → RM, employee → HRBP, visitor → falls back to callback form
upstream). Writes a `handoffs` row and a paired `leads` row so the admin
Leads tab surfaces every handoff as a warm lead.

All user-supplied text (question / context) is PII-scrubbed before it is
embedded in the outgoing message. Only the TARGET's contact is used in the
deep-link; the end user's PAN / email / phone never leave the session.
"""
from __future__ import annotations
import logging
import re
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import identity as _id

logger = logging.getLogger(__name__)

SIGN_OFF = "— Sent via Mackertich ONE Advisor"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_e164_india(phone: Optional[str]) -> Optional[str]:
    """Normalise an Indian mobile number to E.164 (+91XXXXXXXXXX).

    Accepts '9238040800', '+919238040800', '91-92380-40800', '09238040800',
    etc. Returns None if we can't confidently produce 10-digit base.
    """
    if not phone:
        return None
    digits = re.sub(r"\D+", "", str(phone))
    if not digits:
        return None
    # Drop leading country code variants
    if digits.startswith("0091"):
        digits = digits[4:]
    elif digits.startswith("91") and len(digits) > 10:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"+91{digits}"


def build_whatsapp_link(phone_e164: str, message: str) -> str:
    """wa.me accepts a bare number (digits only, no '+'). The text param
    must be URL-encoded."""
    digits = phone_e164.lstrip("+")
    return f"https://wa.me/{digits}?text={urllib.parse.quote(message)}"


def build_mailto_link(email: str, subject: str, body: str) -> str:
    q = urllib.parse.urlencode({"subject": subject, "body": body})
    return f"mailto:{email}?{q}"


def _compose_message(
    handoff_type: str,
    *,
    target_first_name: str,
    sender_first_name: Optional[str],
    ucc_or_id: Optional[str],
    user_question: str,
    context_snippet: Optional[str] = None,
) -> Tuple[str, str]:
    """Return (subject, body)."""
    # Skip "Hi a," if target_first_name is a stopword from a display name.
    first_clean = (target_first_name or "").strip().title()
    if not first_clean or first_clean.lower() in {"a", "an", "the"}:
        first_clean = "there"
    greeting = f"Hi {first_clean},"
    who = sender_first_name or "a Mackertich ONE client"
    id_bit = f" (UCC {ucc_or_id})" if ucc_or_id else ""
    ask = user_question.strip() or "I had a question I'd like to discuss."
    ctx = f"\n\nContext from the chat:\n{context_snippet.strip()}" if context_snippet else ""
    body = (
        f"{greeting}\n\n"
        f"This is {who}{id_bit} reaching out via Mackertich ONE chat. "
        f"I had a question:\n\n\"{ask}\"\n\n"
        f"Could you please assist?{ctx}\n\n"
        f"{SIGN_OFF}"
    )
    subject = f"Mackertich ONE chat handoff{(' · UCC ' + ucc_or_id) if ucc_or_id else ''}"
    return subject, body


def _resolve_target(session: Dict[str, Any], channel_target: str) -> Dict[str, Any]:
    """Return a dict with at minimum: display_name, phone_e164?, email?,
    first_name?, kind. Empty dict if no target could be resolved (caller
    should then fall back to callback form)."""
    identity = (session or {}).get("identity") or {}
    sess_type = session.get("session_type")

    # Visitors — no personal target. Caller should use callback form path.
    if sess_type in (None, "visitor"):
        return {"kind": "advisor", "display_name": "a Mackertich ONE Wealth Manager"}

    if channel_target == "rm" and sess_type == "client":
        return {
            "kind": "rm",
            "display_name": identity.get("rm_name") or "your Relationship Manager",
            "first_name": (identity.get("rm_name") or "").split()[0].title() if identity.get("rm_name") else None,
            "phone_e164": to_e164_india(identity.get("rm_mobile")),
            "email": identity.get("rm_email"),
        }
    if channel_target == "hrbp" and sess_type == "employee":
        # Employee record carries hrbp_name / hrbp_email (plaintext in raw).
        raw = identity.get("raw") or {}
        return {
            "kind": "hrbp",
            "display_name": identity.get("hrbp_name") or raw.get("hrbp_name") or "your HR Business Partner",
            "first_name": ((identity.get("hrbp_name") or raw.get("hrbp_name") or "").split() or [None])[0],
            "phone_e164": to_e164_india(raw.get("hrbp_mobile") or raw.get("hrbp_phone")),
            "email": raw.get("hrbp_email"),
        }
    if channel_target == "advisor":
        return {"kind": "advisor", "display_name": "a senior Mackertich ONE advisor"}
    return {}


async def create_handoff(
    db,
    *,
    session_id: str,
    handoff_type: str,             # "whatsapp" | "email"
    channel_target: str,           # "rm" | "hrbp" | "advisor"
    user_question: str,
    context_snippet: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist the handoff + lead and return the deep-link payload."""
    if handoff_type not in ("whatsapp", "email"):
        raise ValueError("handoff_type must be 'whatsapp' or 'email'")
    if channel_target not in ("rm", "hrbp", "advisor"):
        raise ValueError("channel_target must be 'rm' | 'hrbp' | 'advisor'")

    session = await db.sessions.find_one({"_id": session_id}, {"_id": 0})
    if not session:
        raise ValueError("session not found")
    identity = session.get("identity") or {}
    target = _resolve_target(session, channel_target)

    # PII scrub the user text before it leaves our system.
    q_masked = _id.redact_pii_in_text(user_question or "")
    ctx_masked = _id.redact_pii_in_text(context_snippet) if context_snippet else None

    subject, body = _compose_message(
        handoff_type,
        target_first_name=target.get("first_name") or (target.get("display_name") or "there").split()[0],
        sender_first_name=identity.get("first_name") or identity.get("name"),
        ucc_or_id=identity.get("ucc") or identity.get("employee_id"),
        user_question=q_masked,
        context_snippet=ctx_masked,
    )

    deep_link: Optional[str] = None
    fallback_link: Optional[str] = None
    target_contact_masked: Optional[str] = None
    target_has_contact = False

    if handoff_type == "whatsapp":
        phone = target.get("phone_e164")
        if phone:
            deep_link = build_whatsapp_link(phone, body)
            target_has_contact = True
            target_contact_masked = _id.mask_phone_display(phone)
        if target.get("email"):
            fallback_link = build_mailto_link(target["email"], subject, body)
    else:  # email
        email = target.get("email")
        if email:
            deep_link = build_mailto_link(email, subject, body)
            target_has_contact = True
            target_contact_masked = _id.mask_email_display(email)
        if target.get("phone_e164"):
            fallback_link = build_whatsapp_link(target["phone_e164"], body)

    handoff_id = str(uuid.uuid4())
    lead_id = str(uuid.uuid4())
    now = _now_iso()

    # Extract a hint at an asset_class from the question for Leads grouping.
    asset_class = _guess_asset_class(q_masked)

    handoff_doc = {
        "_id": handoff_id,
        "handoff_id": handoff_id,
        "session_id": session_id,
        "type": handoff_type,                      # whatsapp | email | callback_form_fallback
        "channel_target": channel_target,
        "target_kind": target.get("kind"),
        "target_display_name": target.get("display_name"),
        "target_contact_masked": target_contact_masked,
        "target_has_contact": target_has_contact,
        "message_excerpt": (body[:500] + ("…" if len(body) > 500 else "")),
        "message_length": len(body),
        "has_deep_link": bool(deep_link),
        "asset_class": asset_class,
        "created_at": now,
        "lead_id": lead_id,
        "status": "initiated",
    }
    await db.handoffs.insert_one(handoff_doc)

    # Companion lead row — surfaces in the existing Leads tab.
    lead_doc = {
        "_id": lead_id,
        "lead_id": lead_id,
        "brand": "Mackertich ONE",
        "parent_company": "SMIFS Ltd",
        "session_id": session_id,
        "form_type": "chat_handoff",
        "source": "chat_handoff",
        "priority": "warm",
        "asset_class": asset_class,
        "handoff_id": handoff_id,
        "status": "new",
        "fields": {
            "target": target.get("display_name"),
            "target_contact_masked": target_contact_masked,
            "session_type": session.get("session_type"),
            "question_preview": q_masked[:220],
        },
        "context": {
            "session_id": session_id,
            "handoff_type": handoff_type,
            "channel_target": channel_target,
        },
        "email_hash": "",
        "phone_hash": "",
        "email_display": identity.get("email_display"),
        "phone_display": identity.get("telephone_display"),
        "created_at": now,
    }
    await db.leads.insert_one(lead_doc)

    return {
        "handoff_id": handoff_id,
        "lead_id": lead_id,
        "target_display_name": target.get("display_name"),
        "target_kind": target.get("kind"),
        "target_has_contact": target_has_contact,
        "target_contact_masked": target_contact_masked,
        "handoff_type": handoff_type,
        "deep_link": deep_link,
        "fallback_link": fallback_link,
        "should_callback_form": (not target_has_contact),
        "message_preview": body[:400],
    }


_ASSET_HINTS = [
    ("AIF", re.compile(r"\b(aif|alternative\s+investment)\b", re.I)),
    ("PMS", re.compile(r"\b(pms|portfolio\s+management)\b", re.I)),
    ("IPO", re.compile(r"\bipo\b", re.I)),
    ("NCD", re.compile(r"\bncd\b", re.I)),
    ("MutualFund", re.compile(r"\b(mutual\s+fund|mf|sip|elss)\b", re.I)),
    ("SGB", re.compile(r"\b(sgb|sovereign\s+gold\s+bond)\b", re.I)),
]


def _guess_asset_class(text: str) -> Optional[str]:
    for label, pat in _ASSET_HINTS:
        if pat.search(text or ""):
            return label
    return None


async def list_handoffs(db, *, limit: int = 50) -> List[Dict[str, Any]]:
    cur = db.handoffs.find({}, {"_id": 0}).sort("created_at", -1).limit(max(1, min(limit, 200)))
    return await cur.to_list(length=limit)
