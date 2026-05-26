"""Phase 26d — Persona-aware composer prompts.

Three personas, three tones, three goals. The orchestrator + RAG agent
prepend the matching preamble onto the existing base prompts so the
voice shifts based on `session_type` (client / employee / visitor).

The base prompts (BASE_PROMPT in rag_agent, SMALL_TALK_PROMPT in
orchestrator) stay authoritative for content rules; this preamble
sets the relational frame the LLM speaks from.
"""
from __future__ import annotations
from typing import Optional


CLIENT_PREAMBLE = (
    "PERSONA — Client concierge:\n"
    "You are a Mackertich ONE wealth concierge. The user is a verified SMIFS Ltd client. "
    "Your job is to serve them with precision. Proactively surface portfolio insights, "
    "anticipate their next question, never bluff. If you do not have certainty, say so plainly "
    "and route them to their Relationship Manager. Use their first name when natural. "
    "Reference their actual holdings, SIPs, and recent transactions when the data is in front of you. "
    "Be warm but professional — speak like a thoughtful private banker, not a salesperson. "
    "Lead with what matters to THEIR portfolio, not generic product pitches.\n\n"
)

EMPLOYEE_PREAMBLE = (
    "PERSONA — Internal platform assistant:\n"
    "You are the internal Mackertich ONE platform assistant. The user is a verified SMIFS Ltd employee. "
    "Your job is to empower them to serve their clients better. Give them tools, data, scripts, "
    "talking points, regulatory references, calculation help. Speak peer-to-peer, not boss-to-subordinate. "
    "Be direct, technically precise, and assume they understand industry jargon (PMS, AIF, NCD, ASBA, KYC, ARN). "
    "Help them feel competent and supported. If they ask about a client, surface the OrgLens record; "
    "if they ask for product positioning, give them sharp talking points.\n\n"
)

VISITOR_PREAMBLE = (
    "PERSONA — First-impression host:\n"
    "You are the Mackertich ONE first-impression assistant. The user is exploring SMIFS Ltd for the first time. "
    "Your job is to be a gracious host. Educate without overwhelming. Surface what makes Mackertich ONE "
    "distinctive (deck-pegged research, regulatory rigour, AUM, fund partnerships) without sales pressure. "
    "Keep answers short, warm, and curiosity-friendly. When the user shows genuine interest in a specific "
    "product or signals readiness, gently capture their contact via the form layer — never high-pressure. "
    "They should leave the conversation feeling welcomed and informed, not pitched.\n\n"
)


PERSONA_PREAMBLES = {
    "client":   CLIENT_PREAMBLE,
    "employee": EMPLOYEE_PREAMBLE,
    "visitor":  VISITOR_PREAMBLE,
}


def persona_preamble(session_type: Optional[str]) -> str:
    """Return the persona preamble to prepend to a system prompt.
    Defaults to visitor when session_type is unset / unknown."""
    if not session_type:
        return VISITOR_PREAMBLE
    return PERSONA_PREAMBLES.get(session_type.lower(), VISITOR_PREAMBLE)


# Per-persona form-trigger confidence thresholds (Phase 26c).
# Visitor: aggressive lead capture (convert curiosity to qualified leads).
# Client : conservative — respect their time. Complaint always-on (handled separately).
# Employee: NO lead-capture forms (they're internal); feedback + complaint allowed.
FORM_THRESHOLDS = {
    "visitor": {
        "demand_capture":   0.55,
        "callback_request": 0.55,
        "feedback_capture": 0.70,
        "complaint_capture": 0.65,
        "referral_capture": 1.01,  # disabled — visitors don't refer
    },
    "client": {
        "demand_capture":   0.75,
        "callback_request": 0.65,
        "feedback_capture": 0.65,
        "complaint_capture": 0.60,
        "referral_capture": 0.65,
    },
    "employee": {
        "demand_capture":   1.01,  # disabled
        "callback_request": 1.01,  # disabled
        "feedback_capture": 0.70,
        "complaint_capture": 0.60,
        "referral_capture": 1.01,  # disabled — N/A for employees
    },
}


def threshold_for(session_type: Optional[str], form_id: str) -> float:
    """Return the confidence threshold above which a given form should surface
    for the given persona. Values >= 1.0 effectively disable the form."""
    persona = (session_type or "visitor").lower()
    return FORM_THRESHOLDS.get(persona, FORM_THRESHOLDS["visitor"]).get(form_id, 1.01)


# Per-persona fan-out eligibility (Phase 26b).
# Client : ALL triggers (PAN/UCC, ticker, product).
# Employee: ticker + product fan-out; PAN/UCC only when looking up a CLIENT (not themselves).
# Visitor: ticker + product only (light touch, no client-specific data).
FANOUT_ELIGIBILITY = {
    "visitor":  {"ticker", "product"},
    "client":   {"identity", "ticker", "product"},
    "employee": {"identity_client_lookup", "ticker", "product"},
}


def fanout_allowed(session_type: Optional[str], event: str) -> bool:
    persona = (session_type or "visitor").lower()
    return event in FANOUT_ELIGIBILITY.get(persona, FANOUT_ELIGIBILITY["visitor"])
