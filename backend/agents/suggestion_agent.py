"""Phase 29b — Suggestion Agent.

Generates EXACTLY 3 follow-up chips after substantive assistant replies, so
the user stays "pinned to context" without losing open-ended freedom.

Design contract:
  - Fast: 600 ms LLM budget, 800 ms total wall-clock (else skip the block).
  - Persona-aware: visitor → discovery; client → portfolio/service;
                   employee → ops/research.
  - Specific to the just-discussed topic, NOT generic ("ask another question").
  - Fallback catalog by intent so a Hub AI outage still gives chips.
  - Skip rules: anti-bluff rail, dynamic_form, submission success, farewells,
    proactive opener.

Public API:
  - SKIP_INTENTS / SKIP_BLOCK_TYPES: classification helpers
  - should_skip(intent, blocks, user_message): bool
  - generate(...): -> list[{"id","label"}] of length 3, or [] on skip/fail
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from . import llm as _llm

logger = logging.getLogger(__name__)

# ---------- Feature flag ----------
ENABLED = (os.environ.get("SUGGESTION_AGENT_ENABLED", "true").lower() == "true")

# ---------- Budget ----------
LLM_BUDGET_S = float(os.environ.get("SUGGESTION_LLM_BUDGET_S", "0.6"))
TOTAL_BUDGET_S = float(os.environ.get("SUGGESTION_TOTAL_BUDGET_S", "0.8"))
MAX_LABEL_LEN = 60

# ---------- Skip rules ----------
# Intents whose block already includes its own CTAs/affordances.
SKIP_INTENTS = {
    "LOW_CONFIDENCE_ESCALATION",
    "AUTH_PAN_REQUEST", "AUTH_PAN_RETRY", "AUTH_CHALLENGE",
    "AUTH_LOCKED", "AUTH_NOT_FOUND",
    "OOS_REFUSAL", "TOOLS_REFUSAL",
    "EMPTY_INPUT", "RESUME_OFFER",
}

# Block types that own their own UX flow.
SKIP_BLOCK_TYPES = {
    "dynamic_form", "form", "form_submitted",
    "low_confidence_escalation", "resume_offer",
    "role_choice", "suggested_actions",
}

# Farewell pattern — case-insensitive substring match.
_FAREWELL_RE = re.compile(
    r"\b("
    r"bye|goodbye|thanks?\s*(a\s*lot|so\s*much)?\s*bye|"
    r"that'?s\s+all|that'?s\s+it|"
    r"stop|end\s+conversation|end\s+chat|i'?m\s+done|nothing\s+else|"
    r"talk\s+later|see\s+you|catch\s+you\s+later"
    r")\b",
    re.IGNORECASE,
)


def should_skip(intent: Optional[str],
                blocks: Optional[List[Dict[str, Any]]],
                user_message: Optional[str]) -> bool:
    """Decide whether suggestions are appropriate for this turn."""
    if not ENABLED:
        return True
    if intent and intent in SKIP_INTENTS:
        return True
    for b in (blocks or []):
        if isinstance(b, dict) and b.get("type") in SKIP_BLOCK_TYPES:
            return True
    if user_message and _FAREWELL_RE.search(user_message):
        return True
    return False


# ---------- Fallback catalog (used when LLM fails or budget blown) ----------
_GENERIC_FALLBACK = [
    "Tell me more about this",
    "How does it compare to FDs?",
    "What are the risks?",
]

_FALLBACK_BY_KEYWORD: List[tuple] = [
    # Each tuple: (keywords, visitor_chips, client_chips, employee_chips)
    (
        ("ncd", "debenture", "non-convertible"),
        ["Show eligibility & lock-in",
         "Compare NCD vs FD returns",
         "What's the minimum investment?"],
        ["Show my NCD holdings",
         "Upcoming NCD interest dates",
         "Talk to my relationship manager"],
        ["Latest NCD circulars from SEBI",
         "Generate NCD sales pitch",
         "Pull NCD allocation by client segment"],
    ),
    (
        ("sip", "systematic investment plan", "mutual fund", "mf "),
        ["How do SIPs work?",
         "Show SIP calculator example",
         "What's the minimum SIP?"],
        ["Show my active SIPs",
         "Pause or modify a SIP",
         "Talk to my relationship manager"],
        ["Top performing SIPs this quarter",
         "Generate SIP comparison deck",
         "Pull client SIP renewals due"],
    ),
    (
        ("kyc", "pan", "aadhaar", "onboard"),
        ["What KYC documents do I need?",
         "How long does account opening take?",
         "Open an account now"],
        ["Update my KYC details",
         "Check my KYC status",
         "Talk to my relationship manager"],
        ["KYC pending clients list",
         "Latest KYC norm updates",
         "Generate KYC checklist"],
    ),
    (
        ("portfolio", "holding", "valuation", "demat"),
        ["What goes into a portfolio?",
         "Compare equity vs debt allocation",
         "How is risk measured?"],
        ["Show my portfolio summary",
         "What are my biggest holdings?",
         "Schedule a portfolio review"],
        ["Pull client portfolio breakdown",
         "Generate review presentation",
         "Top-performing client portfolios"],
    ),
    (
        ("complaint", "issue", "grievance", "escalate"),
        ["Talk to a human advisor",
         "How long for resolution?",
         "Check status of my complaint"],
        ["Escalate to senior management",
         "Talk to my relationship manager",
         "Track my complaint"],
        ["Open complaints queue",
         "SLA breach report",
         "Compose escalation note"],
    ),
    (
        ("reliance", "tcs", "infosys", "stock", "ticker", "share price"),
        ["What's the analyst view?",
         "Show 1-year price chart",
         "Compare with sector peers"],
        ["Add to my watchlist",
         "Show my exposure",
         "Talk to my advisor"],
        ["Latest research note",
         "Generate client talking points",
         "Run sector comparison"],
    ),
    (
        ("smifs", "about us", "company", "branch"),
        ["Where are you located?",
         "What services do you offer?",
         "Schedule a free consultation"],
        ["Talk to my relationship manager",
         "Update my profile",
         "Recent product launches"],
        ["Org structure overview",
         "Branch performance dashboard",
         "Latest internal circulars"],
    ),
]


def _fallback_chips(user_message: str, persona: str) -> List[str]:
    msg = (user_message or "").lower()
    for keywords, vis, cli, emp in _FALLBACK_BY_KEYWORD:
        if any(k in msg for k in keywords):
            if persona == "client":
                return cli
            if persona == "employee":
                return emp
            return vis
    return list(_GENERIC_FALLBACK)


# ---------- Persona prompts ----------
_PERSONA_DIRECTIVE = {
    "visitor": (
        "USER PERSONA: prospect/visitor (unauthenticated). "
        "Suggestions should drive discovery, comparison, and qualification "
        "(e.g. eligibility, minimums, FD comparisons, opening an account)."
    ),
    "client": (
        "USER PERSONA: authenticated client. "
        "Suggestions should tie back to portfolio/holdings or service actions "
        "(e.g. 'Show my holdings', 'Talk to my RM', 'Update my profile')."
    ),
    "employee": (
        "USER PERSONA: SMIFS employee. "
        "Suggestions should be ops/research-tied "
        "(e.g. 'Pull client X portfolio', 'Latest SEBI circular', "
        "'Generate sales talking points')."
    ),
}

_SYSTEM_PROMPT_BASE = (
    "You are a wealth-management chat assistant. Given the user's last "
    "message and the assistant's reply, propose EXACTLY 3 short follow-up "
    "questions the user is likely to ask next.\n\n"
    "Rules:\n"
    "- Keep them SPECIFIC to the just-discussed topic. NOT generic.\n"
    "- Each label ≤ 60 characters.\n"
    "- No numbering, no bullets, no quotes around labels.\n"
    "- Question marks optional.\n"
    "- Output STRICTLY a JSON array of 3 strings, nothing else.\n"
    "Example output: [\"Show eligibility\", \"Compare with FDs\", \"Open an account\"]"
)


def _normalise(chips: List[str]) -> List[str]:
    """Dedupe, strip, clamp to MAX_LABEL_LEN, drop empty, keep first 3."""
    seen: set = set()
    out: List[str] = []
    for c in (chips or []):
        if not isinstance(c, str):
            continue
        s = c.strip().strip("\"'`")
        if not s:
            continue
        if len(s) > MAX_LABEL_LEN:
            s = s[: MAX_LABEL_LEN - 1].rstrip() + "…"
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= 3:
            break
    return out


def _parse_llm_output(raw: str) -> List[str]:
    """LLM occasionally wraps JSON in markdown fences; try several recoveries."""
    if not raw:
        return []
    txt = raw.strip()
    # Strip ```json fences if present.
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
    try:
        parsed = json.loads(txt)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        if isinstance(parsed, dict):
            for key in ("options", "chips", "suggestions"):
                if isinstance(parsed.get(key), list):
                    return [str(x) for x in parsed[key]]
    except Exception:
        pass
    # Last resort — line-split.
    lines = [ln.strip("-•*0123456789.) ").strip() for ln in txt.splitlines() if ln.strip()]
    return [ln for ln in lines if ln][:3]


async def generate(
    user_message: str,
    assistant_reply: str,
    persona: str = "visitor",
    intent: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Return EXACTLY 3 chips, or [] if generation should be skipped.

    Caller must check `should_skip(...)` first; this function does NOT
    re-apply skip rules.
    """
    persona = (persona or "visitor").lower()
    if persona not in _PERSONA_DIRECTIVE:
        persona = "visitor"

    sys_prompt = _SYSTEM_PROMPT_BASE + "\n\n" + _PERSONA_DIRECTIVE[persona]
    if intent:
        sys_prompt += f"\nROUTER INTENT: {intent}"

    # Trim the assistant reply so we don't pay for huge token contexts on
    # what should be a 600 ms call.
    reply_excerpt = (assistant_reply or "")[:900]

    user_prompt = (
        f"User message: {user_message[:400]}\n\n"
        f"Assistant reply (truncated): {reply_excerpt}\n\n"
        f"Now output the JSON array."
    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]

    chips: List[str] = []
    try:
        # Bound the LLM call within LLM_BUDGET_S; everything outside that
        # falls back to the keyword catalog. We rely on chat_with_fallback's
        # model chain for high availability but cap with asyncio.wait_for.
        res = await asyncio.wait_for(
            _llm.chat_with_fallback(
                messages, temperature=0.4, max_tokens=180,
                session_id=session_id, intent="suggestion_synthesis",
            ),
            timeout=LLM_BUDGET_S,
        )
        raw = (res.get("reply_text")
               or ((res.get("data") or {}).get("choices", [{}])[0].get("message") or {}).get("content")
               or "")
        chips = _normalise(_parse_llm_output(raw))
    except asyncio.TimeoutError:
        logger.info("suggestion_agent: LLM budget %.2fs exceeded — using fallback", LLM_BUDGET_S)
    except Exception:
        logger.exception("suggestion_agent: LLM call failed — using fallback")

    if len(chips) < 3:
        # Top up from the keyword catalog if LLM gave us 0-2 chips.
        extra = _normalise(_fallback_chips(user_message, persona))
        for c in extra:
            if c.lower() not in {x.lower() for x in chips}:
                chips.append(c)
            if len(chips) >= 3:
                break

    chips = chips[:3]
    if len(chips) < 3:
        # Final hard fallback — pad with generic safe chips.
        for c in _GENERIC_FALLBACK:
            if c.lower() not in {x.lower() for x in chips}:
                chips.append(c)
            if len(chips) >= 3:
                break

    return [{"id": str(i + 1), "label": c} for i, c in enumerate(chips[:3])]


def block_from_chips(chips: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """Format chips into the SSE/blocks dispatcher schema."""
    if not chips or len(chips) < 3:
        return None
    return {"type": "suggested_actions", "options": chips}
