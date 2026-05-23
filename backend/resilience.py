"""Phase 13 — Resilient Bot foundation.

Three responsibilities, all defensive:

1. **Adversarial-input detection** — prompt-injection, off-topic
   recommendations, profanity, abuse, off-topic curveballs.
2. **Input normalisation & self-healing** — empty / whitespace / single-char /
   too-long / emoji-only / repeated-turn detection; typo-healing for UCC
   (`O`→`0`), PAN spacing/hyphens, email common-typo domains, phone
   normalisation to 10-digit.
3. **Always-reply guarantee** — `graceful_envelope()` builds a role-aware
   fallback payload (text block + optional escalation_card / form) that
   `server.py` returns whenever any deeper layer raises. Pairs with two
   audit-only collections:

      * `errors`           — every caught 5xx-class exception (with error_id)
      * `security_events`  — every injection / abuse / data-extraction probe

Nothing in this module talks to the LLM. It's deterministic + pure-Python
so it stays available even when Hub AI / OrgLens / Mongo are all down.
"""
from __future__ import annotations

import logging
import re
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ===================================================================
# Adversarial detection
# ===================================================================
# Prompt-injection / jailbreak / system-prompt-extraction patterns.
# Conservative regex set — false-positives are ok (we just decline + redirect)
# but false-negatives are not.
_INJECTION_PATTERNS = [
    r"\bignore\s+(?:all\s+)?(?:previous|prior|above|earlier|the)\s+(?:instructions|prompts?|rules|directions?|messages?)\b",
    r"\bdisregard\s+(?:all\s+)?(?:previous|prior|above|earlier|the)\s+(?:instructions|prompts?|rules?)\b",
    r"\bforget\s+(?:everything|all|your\s+(?:instructions|rules|guidelines|prompt))\b",
    r"\bdeveloper\s+mode\b",
    r"\bjailbreak(?:ing|ed)?\b",
    r"\bunrestricted\s+(?:mode|assistant|ai)\b",
    r"\bdo\s+anything\s+now\b",
    r"\b(?:i\s+am\s+now\s+|you\s+are\s+now\s+)?DAN\b",
    r"\breveal\s+your\s+(?:system\s+prompt|instructions|prompt|guidelines|rules|tools?)\b",
    r"\b(?:show|tell|give|print|output|display)\s+(?:me\s+)?your\s+"
    r"(?:system\s+prompt|instructions|tools?|tool\s+definitions?|prompt|api\s+key|api\s+keys?|secret\s*key)\b",
    r"\bwhat\s+(?:is|are)\s+your\s+(?:system\s+prompt|instructions|tools?|api\s+keys?|secret)\b",
    r"\b(?:print|dump|output|leak)\s+the\s+system\s+prompt\b",
    r"\bpretend\s+(?:to\s+be|you\s+are|you'?re)\s+(?:a\s+)?(?:different|another|new|unrestricted)\b",
    r"\bact\s+as\s+(?:if\s+)?(?:a\s+)?(?:different|another|new|unrestricted)\b",
    r"\byou\s+are\s+now\s+(?:in\s+)?(?:developer|admin|unrestricted|free|debug)\s+(?:mode|assistant)\b",
    r"\bbypass\s+(?:verification|security|authentication|the\s+rules|safety)\b",
    r"\bi\s+am\s+(?:the\s+)?(?:admin|root|developer|superuser|engineer)\b",
    r"\bi\s+am\s+authoris(?:e|ed)\s+to\s+(?:see|access|view|know)\b",
    r"\bexecute\s+this\s+(?:code|script|command|payload)\b",
    r"\brun\s+this\s+(?:code|script|command|shell)\b",
    r"\b(?:sudo|exec\s*\()\b",
    r"<\s*script\b",   # crude XSS probe — surface the same security-event log
    r"\bsystem\s+prompt\b.*\b(show|reveal|print|leak)\b",
    r"\bshow\s+me\s+(?:the\s+)?conversation\s+(?:of|for)\s+(?:ucc|client|user)\s+\w+",
    r"\b(?:portfolio|trades?|holdings?|ledger)\s+of\s+(?:ucc\s+)?\d{4,}",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

# Off-topic stock / fund recommendation patterns.
_RECOMMENDATION_RE = re.compile(
    r"\b(?:should\s+i\s+(?:buy|sell|invest\s+in|purchase|exit|book\s+profit)|"
    r"is\s+\w[\w\s&\-\.]{0,40}\s+(?:a\s+)?(?:good\s+)?(?:buy|sell|investment|stock|pick)|"
    r"(?:recommend|suggest|tell\s+me)\s+(?:me\s+)?(?:a\s+|some\s+)?(?:stock|share|fund|sip|aif|pms)|"
    r"which\s+(?:stock|share|fund|aif|pms|mutual\s+fund)\s+(?:should|to)\s+(?:i\s+)?(?:buy|invest))\b",
    re.IGNORECASE,
)

# Off-topic curveballs (jokes, translation, weather, philosophy, code).
_OFF_TOPIC_RE = re.compile(
    r"\b(?:tell\s+me\s+a\s+joke|sing\s+(?:me\s+)?a\s+song|translate\s+(?:this|that|the|to)|"
    r"what(?:'s|\s+is)\s+(?:the\s+)?(?:weather|meaning\s+of\s+life|capital\s+of)|"
    r"write\s+(?:me\s+)?(?:a\s+)?(?:poem|essay|story|code|python|javascript|sql)|"
    r"(?:roast|insult|mock|make\s+fun\s+of)\s+\w+)\b",
    re.IGNORECASE,
)

# Common English profanity (kept short — we don't moralise, we just steer back
# to professional tone). Extend cautiously to avoid false positives.
_PROFANITY = {
    "fuck", "fucking", "fucker", "shit", "bullshit", "bitch", "asshole",
    "ass-hole", "cunt", "bastard", "dick", "dickhead", "piss", "fuckoff",
    "wtf", "stfu", "motherfucker",
}
_PROFANITY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in _PROFANITY) + r")\b",
    re.IGNORECASE,
)


def detect_injection(message: str) -> Optional[str]:
    """Return a short signal string (e.g. 'reveal_system_prompt') when the
    message looks like a prompt-injection / jailbreak attempt. None otherwise.
    """
    if not message:
        return None
    m = _INJECTION_RE.search(message)
    if not m:
        return None
    snippet = m.group(0).lower()
    if "system prompt" in snippet or "reveal" in snippet or "show" in snippet or "print" in snippet:
        return "reveal_internals"
    if "ignore" in snippet or "disregard" in snippet or "forget" in snippet:
        return "override_instructions"
    if "developer mode" in snippet or "jailbreak" in snippet or "dan" in snippet or "unrestricted" in snippet:
        return "jailbreak"
    if "bypass" in snippet or "authorised" in snippet or "authorized" in snippet or "i am" in snippet:
        return "auth_bypass"
    if "execute" in snippet or "run this" in snippet or "sudo" in snippet or "exec" in snippet or "script" in snippet:
        return "code_exec"
    if "conversation of" in snippet or "portfolio of" in snippet or "trades of" in snippet:
        return "cross_account_probe"
    return "injection_other"


def detect_recommendation(message: str) -> bool:
    if not message:
        return False
    return bool(_RECOMMENDATION_RE.search(message))


def detect_off_topic(message: str) -> bool:
    if not message:
        return False
    return bool(_OFF_TOPIC_RE.search(message))


def detect_profanity(message: str) -> bool:
    if not message:
        return False
    return bool(_PROFANITY_RE.search(message))


# ===================================================================
# Input normalisation & edge-case detection
# ===================================================================
MAX_MESSAGE_CHARS = 5000

_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
    r"\u2600-\u27BF\u2300-\u23FF]",
    flags=re.UNICODE,
)


def is_emoji_only(message: str) -> bool:
    if not message:
        return False
    stripped = _EMOJI_RE.sub("", message).strip()
    return stripped == "" and bool(_EMOJI_RE.search(message))


def normalise_input(message: Optional[str]) -> Tuple[str, Optional[str]]:
    """Return (cleaned_message, edge_kind).

    edge_kind ∈ { None, 'empty', 'whitespace', 'single_char', 'too_long',
                  'emoji_only' }.

    Cleaned message never exceeds MAX_MESSAGE_CHARS. Whitespace at the edges
    is stripped. The caller is responsible for the 'repeated' check because
    it needs history.
    """
    if message is None:
        return "", "empty"
    raw = message
    if raw == "":
        return "", "empty"
    if raw.strip() == "":
        return "", "whitespace"
    cleaned = raw.strip()
    truncated = False
    if len(cleaned) > MAX_MESSAGE_CHARS:
        cleaned = cleaned[:MAX_MESSAGE_CHARS]
        truncated = True
    if is_emoji_only(cleaned):
        return cleaned, "emoji_only"
    # Single non-alnum char like "?" "." "k" "!"
    if len(cleaned) == 1 and not cleaned.isalnum():
        return cleaned, "single_char"
    if len(cleaned) <= 2 and cleaned.isalpha() and cleaned.lower() not in {"hi", "ok", "no", "yo"}:
        return cleaned, "single_char"
    if truncated:
        return cleaned, "too_long"
    return cleaned, None


def is_repeated(message: str, last_user_message: Optional[str]) -> bool:
    if not message or not last_user_message:
        return False
    return message.strip().lower() == last_user_message.strip().lower()


# ===================================================================
# Self-healing input parsers
# ===================================================================
def repair_ucc_candidate(message: str) -> str:
    """Apply *minimal* O/0 / l/1 / I/1 healing inside the FIRST UCC-shaped run.

    We must NOT mangle the whole message (that would break later regex matches
    for emails, names, etc.). We replace ambiguous characters only inside the
    matched run.
    """
    if not message:
        return message
    # Match a UCC-shaped run (1-2 letters + 4-9 alnum mixing 0/O 1/l/I 2/Z 5/S)
    rx = re.compile(r"\b([A-Za-z]{0,2}[A-Za-z0-9OoIlS]{4,9})\b")
    out = message
    seen: List[str] = []
    for m in rx.finditer(message):
        tok = m.group(1)
        if tok.lower() in seen:
            continue
        seen.append(tok.lower())
        repaired = _heal_alnum_token(tok)
        if repaired != tok:
            out = out[:m.start(1)] + repaired + out[m.end(1):]
            # First repair wins — we don't want to chain-apply
            break
    return out


def _heal_alnum_token(tok: str) -> str:
    """Within a UCC-shaped token (letters then digits OR plain digits):
       - in the digit zone, swap O/o → 0, l/I → 1, S → 5 *only* if the
         result becomes a 4-9 digit run preceded by 0-2 letters.
    """
    if not tok or len(tok) < 5:
        return tok
    # Locate the boundary between leading letters and the tail.
    i = 0
    while i < len(tok) and tok[i].isalpha() and not _is_digit_lookalike(tok[i]):
        i += 1
    prefix, tail = tok[:i], tok[i:]
    if len(prefix) > 2:
        return tok
    healed_tail = (tail
                   .replace("O", "0").replace("o", "0")
                   .replace("I", "1").replace("l", "1")
                   .replace("S", "5"))
    if healed_tail.isdigit() and 4 <= len(healed_tail) <= 8:
        return prefix.upper() + healed_tail
    return tok


def _is_digit_lookalike(c: str) -> bool:
    return c in {"O", "o", "I", "l", "S"}


_EMAIL_TYPO_DOMAINS = {
    "gnail.com": "gmail.com",
    "gmial.com": "gmail.com",
    "gmal.com": "gmail.com",
    "gmaill.com": "gmail.com",
    "yaho.com": "yahoo.com",
    "yhaoo.com": "yahoo.com",
    "yhoo.com": "yahoo.com",
    "hotnail.com": "hotmail.com",
    "hotmial.com": "hotmail.com",
    "outloo.com": "outlook.com",
    "smifs.in": "smifs.com",
    "pesmifs.in": "pesmifs.com",
}


def repair_email(text: str) -> str:
    """Replace a single common email-typo domain inside the text. Returns
    original text when no repair applies."""
    if not text or "@" not in text:
        return text
    rx = re.compile(r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", re.IGNORECASE)
    def _sub(m: re.Match) -> str:
        domain = m.group(1).lower()
        if domain in _EMAIL_TYPO_DOMAINS:
            return "@" + _EMAIL_TYPO_DOMAINS[domain]
        return m.group(0)
    return rx.sub(_sub, text)


def repair_pan_in_text(text: str) -> str:
    """Strip spaces/hyphens inside a PAN-shaped run so `identity.extract_pan`
    can pick it up. e.g. 'ABCDE 1234 F' or 'ABCDE-1234-F' → 'ABCDE1234F'.
    """
    if not text:
        return text
    rx = re.compile(r"\b([A-Za-z]{5})[\s\-]?(\d{4})[\s\-]?([A-Za-z])\b")
    return rx.sub(lambda m: (m.group(1) + m.group(2) + m.group(3)).upper(), text)


def normalise_phone_e164ish(s: str) -> Optional[str]:
    """Best-effort normalise an Indian mobile to 10 digits (no country code).
    Returns None if the input isn't phone-shaped.
    """
    if not s:
        return None
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) >= 10:
        return digits[-10:]
    return None


def self_heal_message(message: str) -> Tuple[str, List[str]]:
    """Apply *all* healers to the message. Returns (healed_message, applied).

    `applied` is a list of strings so we can log which heuristics fired
    (UCC repair, email repair, PAN normalisation). The message is changed
    only when a heuristic positively matches — we never mutate arbitrary
    user prose.
    """
    if not message:
        return message, []
    applied: List[str] = []
    after_ucc = repair_ucc_candidate(message)
    if after_ucc != message:
        applied.append("ucc_lookalike")
    after_pan = repair_pan_in_text(after_ucc)
    if after_pan != after_ucc:
        applied.append("pan_spacing")
    after_email = repair_email(after_pan)
    if after_email != after_pan:
        applied.append("email_typo_domain")
    return after_email, applied


# ===================================================================
# Graceful-envelope builders
# ===================================================================
def _rm_contact_line(identity_obj: Optional[Dict[str, Any]]) -> str:
    rm = (identity_obj or {}).get("rm_name") or "your relationship manager"
    rm_email = (identity_obj or {}).get("rm_email") or ""
    rm_mobile = (identity_obj or {}).get("rm_mobile") or ""
    bits = [b for b in (rm_email, rm_mobile) if b]
    return f"{rm} ({', '.join(bits)})" if bits else rm


def _hrbp_line(identity_obj: Optional[Dict[str, Any]]) -> str:
    return (identity_obj or {}).get("hrbp_name") or "your HRBP"


def role_state(session_type: Optional[str], auth_state: Optional[str],
               identity_obj: Optional[Dict[str, Any]]) -> str:
    """Return one of: 'client', 'employee', 'visitor'."""
    if (session_type == "client" and auth_state == "verified"
            and identity_obj and identity_obj.get("type") == "client"):
        return "client"
    if (session_type == "employee" and auth_state == "verified"
            and identity_obj and identity_obj.get("type") == "employee"):
        return "employee"
    return "visitor"


def graceful_envelope(*, session_id: Optional[str], error_id: str,
                      session_type: Optional[str] = None,
                      auth_state: Optional[str] = None,
                      identity_obj: Optional[Dict[str, Any]] = None,
                      reason: str = "internal_error") -> Dict[str, Any]:
    """Build the always-reply envelope for an internal failure.

    Caller is responsible for persisting this to `conversations` so the FE
    history shows the reply on the next /sessions fetch.
    """
    rs = role_state(session_type, auth_state, identity_obj)
    if rs == "client":
        contact = _rm_contact_line(identity_obj)
        text = (
            "I had trouble pulling that just now. Please connect with your "
            f"Wealth Manager — {contact} — or try asking again in a moment."
        )
        blocks: List[Dict[str, Any]] = [
            {"type": "text", "text": text},
            {"type": "escalation_card",
             "data": {"reason": reason, "rm_name": (identity_obj or {}).get("rm_name"),
                      "rm_email": (identity_obj or {}).get("rm_email_display"),
                      "rm_mobile": (identity_obj or {}).get("rm_mobile_display")}},
        ]
    elif rs == "employee":
        hrbp = _hrbp_line(identity_obj)
        text = (
            "I had trouble with that request just now. You can try again in a "
            f"moment, or reach out to {hrbp} for assistance."
        )
        blocks = [{"type": "text", "text": text}]
    else:
        text = (
            "I had trouble with that just now. Please try again in a moment, "
            "or submit a callback request below and a Mackertich ONE advisor "
            "will reach out."
        )
        blocks = [
            {"type": "text", "text": text},
            {"type": "escalation_card", "data": {"reason": reason}},
        ]
    return {
        "session_id": session_id or "",
        "intent": "INTERNAL_ERROR",
        "blocks": blocks,
        "citations": [],
        "trace": [{"step": "fault", "error_id": error_id, "logged": True, "reason": reason}],
        "model": None,
    }


# ===================================================================
# Canned reply builders — adversarial + edge-case short-circuits
# ===================================================================
def injection_reply() -> Dict[str, Any]:
    return {
        "blocks": [{"type": "text", "text": (
            "I can only help with Mackertich ONE wealth-management questions "
            "and your account information. How may I assist you today?"
        )}],
        "citations": [],
        "model": None,
        "intent_hint": "OUT_OF_SCOPE",
    }


def recommendation_reply(identity_obj: Optional[Dict[str, Any]],
                         session_type: Optional[str],
                         auth_state: Optional[str]) -> Dict[str, Any]:
    rs = role_state(session_type, auth_state, identity_obj)
    if rs == "client":
        contact = _rm_contact_line(identity_obj)
        text = (
            "I can't give specific buy / sell / hold recommendations. For "
            f"personalised advice please consult your Wealth Manager — {contact}."
        )
        return {
            "blocks": [
                {"type": "text", "text": text},
                {"type": "escalation_card",
                 "data": {"reason": "advisor_required",
                          "rm_name": (identity_obj or {}).get("rm_name"),
                          "rm_email": (identity_obj or {}).get("rm_email_display"),
                          "rm_mobile": (identity_obj or {}).get("rm_mobile_display")}},
            ],
            "citations": [], "model": None, "intent_hint": "ESCALATION",
        }
    return {
        "blocks": [
            {"type": "text", "text": (
                "I can't give specific buy / sell / hold recommendations. If "
                "you'd like personalised advice, please request a callback "
                "and a Mackertich ONE advisor will reach out."
            )},
            {"type": "escalation_card", "data": {"reason": "advisor_required"}},
        ],
        "citations": [], "model": None, "intent_hint": "ESCALATION",
    }


def off_topic_reply() -> Dict[str, Any]:
    return {
        "blocks": [{"type": "text", "text": (
            "I'm focused on Mackertich ONE wealth-management — products, your "
            "account, market data, and connecting you with an advisor. How may "
            "I help on that front?"
        )}],
        "citations": [], "model": None, "intent_hint": "OUT_OF_SCOPE",
    }


def profanity_reply() -> Dict[str, Any]:
    return {
        "blocks": [{"type": "text", "text": (
            "I'm here to help with your Mackertich ONE queries. Could we keep "
            "this professional? What would you like to know?"
        )}],
        "citations": [], "model": None, "intent_hint": "OUT_OF_SCOPE",
    }


def empty_input_reply() -> Dict[str, Any]:
    return {
        "blocks": [{"type": "text", "text": (
            "I didn't catch that — could you share what you'd like help with?"
        )}],
        "citations": [], "model": None, "intent_hint": "SMALL_TALK",
    }


def single_char_reply() -> Dict[str, Any]:
    return {
        "blocks": [{"type": "text", "text": (
            "Could you share a bit more? I can help with Mackertich ONE products, "
            "your account, market data, or arranging a callback."
        )}],
        "citations": [], "model": None, "intent_hint": "SMALL_TALK",
    }


def emoji_only_reply() -> Dict[str, Any]:
    return {
        "blocks": [{"type": "text", "text": (
            "How may I help you today? You can ask about Mackertich ONE products, "
            "your account, market data, or request a callback."
        )}],
        "citations": [], "model": None, "intent_hint": "SMALL_TALK",
    }


def too_long_notice() -> str:
    return (
        "\n\n(Note: I trimmed your message to the first 5,000 characters so I "
        "could respond — please re-send the rest in a follow-up if needed.)"
    )


def repeated_reply() -> Dict[str, Any]:
    return {
        "blocks": [{"type": "text", "text": (
            "It looks like the same question — would you like me to try a "
            "different angle, or shall I escalate it to a Mackertich ONE "
            "advisor instead?"
        )},
            {"type": "escalation_card", "data": {"reason": "repeated_query"}}],
        "citations": [], "model": None, "intent_hint": "ESCALATION",
    }


# ===================================================================
# Audit-log helpers
# ===================================================================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask_message_for_log(message: str, max_chars: int = 600) -> str:
    """Trim and PAN-scrub a user message before persisting to audit collections."""
    import identity as id_mod
    if not message:
        return ""
    return id_mod.redact_pii_in_text(message)[:max_chars]


async def log_error(db, *, error_id: str, exc: BaseException,
                    session_id: Optional[str],
                    endpoint: str,
                    role_state_value: str,
                    user_message: Optional[str] = None) -> None:
    """Persist a single error to the `errors` collection. Best-effort —
    swallowed if Mongo itself is the thing that failed."""
    try:
        await db.errors.insert_one({
            "error_id": error_id,
            "created_at": _now_iso(),
            "endpoint": endpoint,
            "session_id": session_id,
            "role_state": role_state_value,
            "exc_type": type(exc).__name__,
            "exc_message": str(exc)[:600],
            "traceback": traceback.format_exc()[:6000],
            "user_message_excerpt": _mask_message_for_log(user_message or "", 600),
        })
    except Exception:
        logger.exception("errors collection insert failed (non-fatal)")


async def log_security_event(db, *, kind: str, session_id: Optional[str],
                             role_state_value: str,
                             user_message: str,
                             action: str) -> None:
    """Persist a single security event (injection / abuse / extraction probe)
    to the `security_events` collection. PII-scrubbed."""
    try:
        await db.security_events.insert_one({
            "created_at": _now_iso(),
            "kind": kind,
            "session_id": session_id,
            "role_state": role_state_value,
            "user_message_excerpt": _mask_message_for_log(user_message, 600),
            "action": action,
        })
    except Exception:
        logger.exception("security_events insert failed (non-fatal)")


def new_error_id() -> str:
    return uuid.uuid4().hex[:8]


# ===================================================================
# Public entry-point for the orchestrator
# ===================================================================
def short_circuit(message: str, history: List[Dict[str, Any]],
                  *, identity_obj: Optional[Dict[str, Any]],
                  session_type: Optional[str],
                  auth_state: Optional[str]) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Run every adversarial / edge-case check.

    Returns None when the orchestrator should proceed normally. Returns
    `(out, audit_ctx)` when we want to short-circuit:

        out        — the `{blocks, citations, model, intent_hint}` dict the
                     orchestrator already speaks
        audit_ctx  — { 'kind': str, 'action': str, 'security_event': bool }

    Order is important — injection checks beat profanity, profanity beats
    recommendation, etc.
    """
    if message is None:
        return empty_input_reply(), {"kind": "empty", "action": "nudged", "security_event": False}

    # 1) Injection — highest priority. Don't reveal anything from KB/identity.
    sig = detect_injection(message)
    if sig:
        return injection_reply(), {"kind": sig, "action": "deflected", "security_event": True}

    # 2) Profanity / abuse — log + steer professional.
    if detect_profanity(message):
        return profanity_reply(), {"kind": "profanity", "action": "moderated", "security_event": True}

    # 3) Stock / fund buy-sell recommendation — strict refusal.
    if detect_recommendation(message):
        return (
            recommendation_reply(identity_obj, session_type, auth_state),
            {"kind": "recommendation_request", "action": "deflected", "security_event": False},
        )

    # 4) Generic off-topic (joke / translate / weather / code / poem / roast).
    if detect_off_topic(message):
        return off_topic_reply(), {"kind": "off_topic_curveball", "action": "deflected",
                                   "security_event": False}

    return None
