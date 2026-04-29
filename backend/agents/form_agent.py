"""Form orchestrator — returns UI form schemas. No LLM call needed."""
from __future__ import annotations
from typing import Any, Dict, Optional


def lead_capture_form(asset_class: Optional[str] = None) -> Dict[str, Any]:
    # Normalize plurals so "NCDs" → "NCD", "AIFs" → "AIF" (strip trailing single 's')
    normalized: Optional[str] = None
    if asset_class:
        s = asset_class.strip().upper()
        if len(s) > 1 and s.endswith("S") and not s.endswith("SS"):
            s = s[:-1]
        normalized = s
    title = "Connect with a Mackertich ONE advisor"
    subtitle = (
        f"We'll personalise {normalized} options for your goals and a senior Mackertich ONE advisor will respond within one business day."
        if normalized
        else "Share a few details and a Mackertich ONE senior advisor will reach out within one business day."
    )
    return {
        "form_type": "lead_capture",
        "brand": "Mackertich ONE",
        "title": title,
        "subtitle": subtitle,
        "fields": [
            {"name": "name", "label": "Full Name", "type": "text", "required": True, "placeholder": "Your name"},
            {"name": "email", "label": "Email", "type": "email", "required": True, "placeholder": "you@example.com"},
            {
                "name": "phone",
                "label": "Phone",
                "type": "tel",
                "required": True,
                "placeholder": "+91 98765 43210",
                "pattern": r"^[+0-9 \-]{10,18}$",
            },
            {"name": "city", "label": "City", "type": "text", "required": False, "placeholder": "Mumbai"},
            {
                "name": "investment_range",
                "label": "Investment Range",
                "type": "select",
                "required": True,
                "options": ["Under ₹10L", "₹10L–₹50L", "₹50L–₹2Cr", "₹2Cr+"],
            },
        ],
        "submit_label": "Request Callback",
        "context": {"asset_class": normalized},
    }


def callback_form() -> Dict[str, Any]:
    return {
        "form_type": "callback",
        "brand": "Mackertich ONE",
        "title": "Request a Mackertich ONE callback",
        "subtitle": "A senior Mackertich ONE advisor will call you at your preferred time.",
        "fields": [
            {"name": "name", "label": "Full Name", "type": "text", "required": True, "placeholder": "Your name"},
            {
                "name": "phone",
                "label": "Phone",
                "type": "tel",
                "required": True,
                "placeholder": "+91 98765 43210",
                "pattern": r"^[+0-9 \-]{10,18}$",
            },
            {
                "name": "preferred_time",
                "label": "Preferred Time",
                "type": "select",
                "required": True,
                "options": [
                    "Today, 10am–1pm",
                    "Today, 2pm–6pm",
                    "Tomorrow morning",
                    "Tomorrow afternoon",
                    "Next available slot",
                ],
            },
            {
                "name": "topic",
                "label": "Topic (optional)",
                "type": "text",
                "required": False,
                "placeholder": "e.g. NCD subscription, portfolio review",
            },
        ],
        "submit_label": "Request Callback",
        "context": {},
    }
