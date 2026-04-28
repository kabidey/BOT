"""Router agent — classifies user intent into one of 7 buckets."""
from __future__ import annotations
import json
import logging
import re
from typing import Any, Dict, List

from .llm import call_with_fallback, extract_reply

logger = logging.getLogger(__name__)

INTENTS = {
    "KNOWLEDGE",        # general product / explainer / compliance — RAG
    "MARKET_DATA",      # live prices, NAV, fund performance — API agent
    "CLIENT_LOOKUP",    # user provides client code / wants to identify themselves — API agent
    "LEAD_CAPTURE",     # prospect interested in a product — Form agent
    "CALLBACK_REQUEST", # explicit "call me back" — Form agent (callback)
    "ESCALATION",       # frustration, complex/risky advice, out-of-scope — escalation card
    "SMALL_TALK",       # greetings, thanks
}

ROUTER_SYSTEM = """You are the intent classifier for SMIFS Wealth Advisor.
Classify the LATEST user message into EXACTLY ONE of these intents:

- KNOWLEDGE: general product, regulation, taxation, or process explainer questions (e.g. "What is an AIF?", "How are NCDs taxed?", "What is KYC?").
- MARKET_DATA: user wants a live price, NAV, or recent performance of a specific stock or fund (e.g. "What's RELIANCE trading at?", "ICICI Bluechip NAV today").
- CLIENT_LOOKUP: user identifies themselves as an existing SMIFS client, shares a client code (pattern SMIFS\\d+), phone number, or asks about THEIR portfolio/holdings.
- LEAD_CAPTURE: user expresses concrete interest in investing in a SMIFS product (e.g. "I want to invest in NCDs", "interested in the AIF").
- CALLBACK_REQUEST: user explicitly asks to be called back, to speak to an advisor, or to schedule a meeting.
- ESCALATION: user is frustrated, asks for highly personalised advice that requires a human, or topic is clearly out-of-scope for a wealth advisor.
- SMALL_TALK: greetings, thanks, social chit-chat with no advisory content.

If a message has both knowledge AND lead-capture (e.g. "Tell me about NCDs and I want to invest"), prefer LEAD_CAPTURE.
If a message asks about the user's own portfolio without providing a code, prefer CLIENT_LOOKUP.

Respond with ONLY a single JSON object — no prose, no markdown fences:
{"intent": "<INTENT>", "confidence": <0.0-1.0>, "rationale": "<one short sentence>", "subject": "<extracted subject if any: e.g. 'NCD', 'RELIANCE', 'SMIFS001', else null>"}
"""


def _safe_json_parse(raw: str) -> Dict[str, Any]:
    """Extract a JSON object from possibly-noisy LLM output."""
    raw = raw.strip()
    # Strip code fences if any
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    # Find first {...} block
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError(f"No JSON object in router output: {raw[:200]}")
    return json.loads(m.group(0))


async def classify(message: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
    """Returns {intent, confidence, rationale, subject, model}."""
    # Use last 4 turns of history for context
    trimmed = history[-8:]  # 4 turns × 2 messages
    convo_lines = "\n".join(f"{m['role'].upper()}: {m['content'][:400]}" for m in trimmed)
    user_block = (
        (f"PRIOR CONVERSATION:\n{convo_lines}\n\n" if convo_lines else "")
        + f"LATEST USER MESSAGE:\n{message}\n\n"
        + "Output the JSON object now."
    )

    messages = [
        {"role": "system", "content": ROUTER_SYSTEM},
        {"role": "user", "content": user_block},
    ]

    try:
        result = await call_with_fallback(
            messages,
            task="router",
            temperature=0.0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        raw = extract_reply(result["data"])
        parsed = _safe_json_parse(raw)
    except Exception as e:
        logger.warning("Router classification failed (%s); defaulting to KNOWLEDGE.", e)
        return {
            "intent": "KNOWLEDGE",
            "confidence": 0.4,
            "rationale": f"Fallback after parse error: {e}",
            "subject": None,
            "model": None,
        }

    intent = str(parsed.get("intent", "")).upper().strip()
    if intent not in INTENTS:
        # Try to map common variants
        if "FORM" in intent or "INTEREST" in intent or "CAPTURE" in intent:
            intent = "LEAD_CAPTURE"
        elif "CALL" in intent or "CALLBACK" in intent:
            intent = "CALLBACK_REQUEST"
        elif "MARKET" in intent or "PRICE" in intent or "NAV" in intent:
            intent = "MARKET_DATA"
        elif "PORTFOLIO" in intent or "CLIENT" in intent:
            intent = "CLIENT_LOOKUP"
        elif "ESCALAT" in intent:
            intent = "ESCALATION"
        elif "SMALL" in intent or "GREETING" in intent:
            intent = "SMALL_TALK"
        else:
            intent = "KNOWLEDGE"

    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    return {
        "intent": intent,
        "confidence": max(0.0, min(1.0, confidence)),
        "rationale": str(parsed.get("rationale", ""))[:300],
        "subject": parsed.get("subject"),
        "model": result.get("model"),
    }
