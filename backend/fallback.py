"""Phase 10 — central wealth-manager / escalation fallback builder."""
from __future__ import annotations
from typing import Any, Dict, List, Optional

import identity as _id


def make_wealth_manager_fallback(
    session_type: Optional[str],
    auth_state: Optional[str],
    client_context: Optional[Dict[str, Any]] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a ready-to-emit reply for questions the bot can't / shouldn't answer.

    Shape: {reply_text, extra_blocks, intent_hint}
    """
    # Verified client → personalised RM fallback
    if session_type == "client" and auth_state == "verified" and client_context:
        text = _id.wealth_manager_fallback_text(client_context)
        extra_blocks = [{
            "type": "escalation_card",
            "data": {
                "reason": "rm_required",
                "rm_name": client_context.get("rm_name"),
                "rm_email": client_context.get("rm_email"),
                "rm_mobile": client_context.get("rm_mobile"),
                "rm_email_display": client_context.get("rm_email_display"),
                "rm_mobile_display": client_context.get("rm_mobile_display"),
                "rm_code": client_context.get("rm_code"),
            },
        }]
        return {"reply_text": text, "extra_blocks": extra_blocks, "intent_hint": "ESCALATION"}

    # Visitor → callback form
    if session_type in (None, "visitor"):
        text = (
            "I don't have that specific information. Please connect with a Mackertich ONE "
            "Wealth Manager — I can submit a callback request for you right here."
        )
        extra_blocks = [{
            "type": "form",
            "schema": {
                "form_type": "callback",
                "brand": "Mackertich ONE",
                "title": "Request a callback",
                "subtitle": "A senior Mackertich ONE advisor will call you at your preferred time.",
                "fields": [
                    {"name": "name", "label": "Your name", "type": "text", "required": True, "placeholder": "Your name"},
                    {"name": "phone", "label": "Phone", "type": "tel", "required": True, "placeholder": "+91 98765 43210",
                     "pattern": r"^[+0-9 \-]{10,18}$"},
                    {"name": "email", "label": "Email", "type": "email", "required": False, "placeholder": "you@example.com"},
                    {"name": "interest", "label": "What are you interested in?", "type": "text", "required": False},
                ],
                "submit_label": "Request callback",
                "context": {"source": "wm_fallback"},
            },
        }]
        return {"reply_text": text, "extra_blocks": extra_blocks, "intent_hint": "CALLBACK_REQUEST"}

    # Verified employee → escalate via advisor
    text = (
        "I don't have verified information on this in the knowledge base. Let me connect "
        "you to a senior advisor — they'll have the current, accurate details."
    )
    extra_blocks = [{"type": "escalation_card", "data": {"reason": "advisor_required"}}]
    return {"reply_text": text, "extra_blocks": extra_blocks, "intent_hint": "ESCALATION"}
