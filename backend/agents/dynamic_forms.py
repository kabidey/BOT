"""Phase 26c — Dynamic Form Schemas + Trigger Detection.

Five form types surface inline in chat based on conversational signal:

    demand_capture   — anti-bluff fired / we don't offer this / user volunteered info
    referral_capture — client mentions referring someone / end-of-session prompt
    feedback_capture — satisfaction signal ("thanks", "great") / idle-end
    complaint_capture — frustration / "complaint" / "escalate" keywords
    callback_request  — existing Phase 17 flow (rewired to surface from this layer too)

Trigger detection is a lightweight keyword/regex layer with confidence
scoring — fast, deterministic, no extra LLM call per turn. Per-persona
thresholds live in `composer_prompts.threshold_for`.

Cooldowns are enforced per session via the `sessions` Mongo doc:
    sessions[sid].forms_seen = {
        "referral_capture": "<iso ts>",   # 7-day cooldown
        "feedback_capture": "<iso ts>",   # 1 per session
        "demand_capture":   {"last_turn_idx": int, "last_ts": iso},  # 1 per 3 turns
        # complaint + callback: no cooldown
    }
"""
from __future__ import annotations
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------- Schemas ----------

def demand_capture_schema(intent_text: str = "", persona: str = "visitor") -> Dict[str, Any]:
    return {
        "form_id": "demand_capture",
        "title": "Help us understand what you needed",
        "subtitle": "We couldn't fully answer that one. Tell us a bit more and our research desk will get back to you.",
        "fields": [
            {"name": "intent", "label": "What were you trying to learn or accomplish?",
             "type": "textarea", "required": True, "placeholder": "e.g. dress code at SMIFS Mumbai office",
             "default": intent_text[:240] if intent_text else ""},
            {"name": "urgency", "label": "How urgent is this?", "type": "select", "required": True,
             "options": ["Low — just curious", "Medium — within a week", "High — within 24 hours"]},
            {"name": "name", "label": "Your name", "type": "text", "required": True},
            {"name": "email", "label": "Email", "type": "email", "required": True,
             "placeholder": "you@example.com"},
            {"name": "phone", "label": "Phone (optional)", "type": "tel", "required": False,
             "placeholder": "+91 98765 43210"},
        ],
        "submit_label": "Send to our research desk",
        "submit_endpoint": "/api/forms/submit",
        "success_message": "Submitted. Our research desk will get back to you within 24h.",
    }


def referral_capture_schema(referrer_name: str = "", persona: str = "client") -> Dict[str, Any]:
    return {
        "form_id": "referral_capture",
        "title": "Introduce someone to Mackertich ONE",
        "subtitle": "We'll reach out to them gently and reference your introduction.",
        "fields": [
            {"name": "referrer_name", "label": "Your name", "type": "text", "required": True,
             "default": referrer_name or ""},
            {"name": "lead_name", "label": "Their name", "type": "text", "required": True},
            {"name": "lead_phone", "label": "Their phone", "type": "tel", "required": True,
             "placeholder": "+91 98765 43210"},
            {"name": "lead_email", "label": "Their email (optional)", "type": "email", "required": False},
            {"name": "relationship", "label": "How do you know them?", "type": "select", "required": True,
             "options": ["Family", "Friend", "Colleague", "Other"]},
            {"name": "note", "label": "Anything we should know? (optional)", "type": "textarea",
             "required": False, "placeholder": "Their context, what they're looking for, etc."},
        ],
        "submit_label": "Send introduction",
        "submit_endpoint": "/api/forms/submit",
        "success_message": "Thank you for trusting us with their future. We'll reach out gently and reference your introduction.",
    }


def feedback_capture_schema(persona: str = "visitor") -> Dict[str, Any]:
    return {
        "form_id": "feedback_capture",
        "title": "How was your experience?",
        "subtitle": "Your feedback shapes how we serve.",
        "fields": [
            {"name": "rating", "label": "Overall rating", "type": "rating", "required": True, "max": 5},
            {"name": "what_went_well", "label": "What went well? (optional)",
             "type": "textarea", "required": False},
            {"name": "what_could_improve", "label": "What could be better? (optional)",
             "type": "textarea", "required": False},
            {"name": "would_recommend", "label": "Would you recommend Mackertich ONE?",
             "type": "select", "required": True, "options": ["Yes", "Maybe", "No"]},
        ],
        "submit_label": "Share feedback",
        "submit_endpoint": "/api/forms/submit",
        "success_message": "Thank you — your feedback shapes how we serve.",
    }


def complaint_capture_schema(persona: str = "visitor", affected_rm: str = "") -> Dict[str, Any]:
    return {
        "form_id": "complaint_capture",
        "title": "Register a complaint",
        "subtitle": "We take this seriously. A senior advisor will personally reach out within 4 business hours.",
        "fields": [
            {"name": "category", "label": "Category", "type": "select", "required": True,
             "options": ["Account", "Trade", "Service", "RM (Relationship Manager)", "Product", "Other"]},
            {"name": "description", "label": "What happened? (50+ characters)", "type": "textarea",
             "required": True, "minLength": 50,
             "placeholder": "Please describe the issue in detail so we can investigate properly."},
            {"name": "affected_product", "label": "Product affected (optional)", "type": "text",
             "required": False, "placeholder": "e.g. PMS, AIF, MF SIP"},
            {"name": "affected_rm", "label": "RM involved (if applicable)", "type": "text",
             "required": False, "default": affected_rm or ""},
            {"name": "contact_preference", "label": "How should we reach you?", "type": "select",
             "required": True, "options": ["Phone call", "Email", "WhatsApp"]},
            {"name": "acceptable_resolution", "label": "What outcome would resolve this for you?",
             "type": "textarea", "required": False,
             "placeholder": "Tell us what 'fixed' looks like for you."},
        ],
        "submit_label": "Register complaint",
        "submit_endpoint": "/api/forms/submit",
        "priority": "high",
        "success_message": "We take this seriously. A senior advisor will personally reach out within 4 business hours.",
    }


def callback_request_schema(persona: str = "visitor", topic: str = "") -> Dict[str, Any]:
    return {
        "form_id": "callback_request",
        "title": "Request a Mackertich ONE callback",
        "subtitle": "A senior advisor will call at your preferred time.",
        "fields": [
            {"name": "name", "label": "Your name", "type": "text", "required": True},
            {"name": "phone", "label": "Phone", "type": "tel", "required": True,
             "placeholder": "+91 98765 43210"},
            {"name": "email", "label": "Email (optional)", "type": "email", "required": False},
            {"name": "preferred_time", "label": "Preferred time", "type": "select", "required": True,
             "options": ["Today, 10am–1pm", "Today, 2pm–6pm", "Tomorrow morning",
                         "Tomorrow afternoon", "Next available slot"]},
            {"name": "topic", "label": "Topic (optional)", "type": "text", "required": False,
             "default": topic[:120] if topic else "", "placeholder": "e.g. PMS review"},
        ],
        "submit_label": "Request callback",
        "submit_endpoint": "/api/forms/submit",
        "success_message": "Got it — a senior advisor will reach out at your preferred time.",
    }


SCHEMA_BUILDERS = {
    "demand_capture":   demand_capture_schema,
    "referral_capture": referral_capture_schema,
    "feedback_capture": feedback_capture_schema,
    "complaint_capture": complaint_capture_schema,
    "callback_request": callback_request_schema,
}


def build_schema(form_id: str, **kwargs) -> Optional[Dict[str, Any]]:
    builder = SCHEMA_BUILDERS.get(form_id)
    if not builder:
        return None
    # Filter to only kwargs the builder accepts (signature-tolerant).
    import inspect
    sig = inspect.signature(builder)
    accepted = {k: v for k, v in kwargs.items() if k in sig.parameters}
    return builder(**accepted)


# ---------- Trigger detection ----------

# Regex patterns — fast, deterministic. Confidence ramps with specificity.
_COMPLAINT_RE = re.compile(
    r"\b(complain|complaint|file\s+a\s+complaint|register\s+a\s+complaint|"
    r"escalat|frustrat|angry|furious|protest|supervisor|manager|"
    r"unacceptable|outrageous|ridiculous|terrible\s+service|"
    r"poor\s+service|bad\s+service|disgust|appalling)\b",
    re.I,
)
_REFERRAL_RE = re.compile(
    r"\b(refer\s+(?:a\s+)?(?:friend|family|colleague|someone)|"
    r"introduce\s+(?:you\s+to\s+)?(?:my|a)\s+(?:friend|family|colleague|cousin|brother|sister)|"
    r"my\s+(?:friend|colleague|cousin|brother|sister|wife|husband|spouse)\s+(?:would|might|is)|"
    r"recommend\s+you\s+to)\b",
    re.I,
)
_FEEDBACK_POS_RE = re.compile(
    r"\b(thanks|thank\s+you|thx|appreciate|great|perfect|excellent|wonderful|"
    r"helpful|brilliant|amazing|good\s+job|well\s+done)\b!?\.?\s*$",
    re.I,
)


def detect_trigger(message: str, persona: str, conv_turns_count: int,
                   anti_bluff_fired: bool, escalation_fired: bool,
                   forms_seen: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Return `{trigger, confidence, reason}` or None.

    Inputs:
      message        — the user's last message
      persona        — 'visitor' | 'client' | 'employee'
      conv_turns_count — total turns in this session (for demand cooldown)
      anti_bluff_fired — True if rag_agent set the low-confidence flag this turn
      escalation_fired — True if any escalation block was emitted this turn
      forms_seen     — the session doc's cooldown tracker
    """
    forms_seen = forms_seen or {}
    msg = message or ""

    # ----- Cooldown helpers -----
    def _within_days(iso_ts: Optional[str], days: int) -> bool:
        if not iso_ts:
            return False
        try:
            ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - ts) < timedelta(days=days)
        except Exception:
            return False

    # 1. COMPLAINT — no cooldown, highest priority signal.
    if _COMPLAINT_RE.search(msg):
        # Stronger confidence when the explicit "complaint"/"escalate" verbs appear.
        explicit = bool(re.search(r"\b(complaint|escalat|file\s+a)\b", msg, re.I))
        return {"trigger": "complaint_capture",
                "confidence": 0.85 if explicit else 0.70,
                "reason": "frustration / complaint keyword"}

    # 2. DEMAND — anti-bluff or escalation rail fired this turn.
    if anti_bluff_fired or escalation_fired:
        # Cooldown: 1 demand per 3 turns
        last = forms_seen.get("demand_capture") or {}
        if isinstance(last, dict) and (conv_turns_count - (last.get("last_turn_idx") or -10)) < 3:
            pass  # cooldown active — skip
        else:
            return {"trigger": "demand_capture",
                    "confidence": 0.80,
                    "reason": "anti-bluff rail fired"}

    # 3. REFERRAL — only clients/visitors; cooldown 7 days.
    if _REFERRAL_RE.search(msg) and persona in ("client", "visitor"):
        if not _within_days(forms_seen.get("referral_capture"), 7):
            return {"trigger": "referral_capture",
                    "confidence": 0.75,
                    "reason": "referral keyword detected"}

    # 4. FEEDBACK — satisfaction micro-signal, 1 per session.
    if _FEEDBACK_POS_RE.search(msg) and conv_turns_count >= 2:
        if not forms_seen.get("feedback_capture"):
            return {"trigger": "feedback_capture",
                    "confidence": 0.70,
                    "reason": "positive satisfaction signal"}

    return None


# ---------- Cooldown bookkeeping ----------
async def mark_form_seen(db, session_id: str, form_id: str, turn_idx: int = 0) -> None:
    now = datetime.now(timezone.utc).isoformat()
    if form_id == "demand_capture":
        update = {"$set": {f"forms_seen.{form_id}":
                           {"last_turn_idx": turn_idx, "last_ts": now}}}
    else:
        update = {"$set": {f"forms_seen.{form_id}": now}}
    try:
        await db.sessions.update_one({"_id": session_id}, update)
    except Exception:
        logger.exception("mark_form_seen failed for %s/%s", session_id, form_id)


# ---------- Block builder ----------
def build_form_block(form_id: str, session_id: str, persona: str,
                     **schema_kwargs) -> Optional[Dict[str, Any]]:
    schema = build_schema(form_id, **schema_kwargs)
    if not schema:
        return None
    schema["context"] = {
        "session_id": session_id,
        "persona": persona,
    }
    return {"type": "dynamic_form", **schema}
