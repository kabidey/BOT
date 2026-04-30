"""Phase 9 — Anti-hallucination guardrails.

We cannot prevent the LLM from hallucinating — but we can detect likely
hallucinated product claims and log them for review. Two strategies:

1. **Categorical gating** (applied at retrieval time in rag_agent): for
   product/offering topics, exclude uploaded + archive chunks from retrieval
   so seed + smifs_knowledge are the only sources.

2. **Post-generation flagging** (applied after the LLM reply): regex-match
   for confident numeric / prescriptive claims (₹ minimums, % returns, lock-in
   months, "guaranteed", "SEBI-registered as X"). If the reply contains these
   AND no smifs_knowledge chunk was retrieved for the topic, flag the turn.
   We write the event to `hallucination_events` so admins can review.

Note: we do NOT silently edit the model's reply; we annotate it with a visible
caveat block so the user sees the warning, and we log it for audit. The spec's
"strip the claim" approach risks butchering grammar and making replies
nonsensical — annotation + regen-once is safer.
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---- Topic detectors ----
PRODUCT_KEYWORDS = [
    "aif", "pms", "ncd", "ipo", "mutual fund", "sip", "elss", "mackertich",
    "smifs", "category i", "category ii", "category iii", "cat i", "cat ii", "cat iii",
    "portfolio management", "alternative investment", "demat", "icd",
    "sovereign gold bond", "sgb", "tax-saving", "equity fund", "debt fund",
    "hybrid fund", "compliance", "kyc", "fatca", "expense ratio",
]


def is_product_topic(message: str) -> bool:
    m = (message or "").lower()
    return any(k in m for k in PRODUCT_KEYWORDS)


# ---- Confident-claim patterns ----
# Each pattern targets statements that SHOULD be grounded in the KB if made.
CLAIM_PATTERNS = [
    # "₹1 crore minimum" / "Rs. 25 lakh"
    (re.compile(r"(?:₹|rs\.?|inr)\s*[\d,.]+\s*(?:crore|lakh|lakhs|cr|l)\b", re.I), "currency_threshold"),
    # "12% returns", "8.5 per cent"
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|per\s?cent|pct)\b", re.I), "percentage"),
    # "locked for 3 years", "3-year lock-in"
    (re.compile(r"\b(?:lock[- ]?in|locked)\b.*?\b\d+\s*(?:year|month|yr|m)s?\b", re.I), "lock_in"),
    (re.compile(r"\b\d+\s*(?:year|month)\s*lock[- ]?in\b", re.I), "lock_in"),
    # Prescriptive / regulatory claims
    (re.compile(r"\b(?:sebi[- ]registered|sebi[- ]approved|sebi[- ]mandated)\b", re.I), "sebi_claim"),
    (re.compile(r"\bguaranteed\b", re.I), "guarantee"),
    (re.compile(r"\btax[- ]free\b", re.I), "tax_free"),
    (re.compile(r"\brisk[- ]free\b", re.I), "risk_free"),
]


def detect_claims(text: str) -> List[Dict[str, str]]:
    if not text:
        return []
    hits: List[Dict[str, str]] = []
    for rx, label in CLAIM_PATTERNS:
        for m in rx.finditer(text):
            hits.append({"kind": label, "match": m.group(0)})
    return hits


def citation_supports_claims(claims: List[Dict[str, str]], reply_text: str,
                             citations: List[Dict[str, Any]]) -> bool:
    """Weak but useful heuristic: every flagged claim substring must appear
    in at least one retrieved citation's text. If yes → the LLM sourced it.
    If no → it's almost certainly hallucinated."""
    if not claims:
        return True
    if not citations:
        return False
    citation_blob = " ".join((c.get("text") or "") for c in citations).lower()
    unsupported = []
    for c in claims:
        needle = c["match"].lower().strip()
        # For currency / percent we match the numeric core — tighter match
        if c["kind"] in ("currency_threshold", "percentage"):
            digits = re.findall(r"\d+(?:\.\d+)?", needle)
            if digits and not all(d in citation_blob for d in digits):
                unsupported.append(c)
        else:
            if needle not in citation_blob:
                unsupported.append(c)
    return len(unsupported) == 0


# ---- Grounding thresholds ----
SMIFS_STRONG_SCORE = 0.35     # minimum weighted score on smifs_knowledge for "KB covers it"
SEED_FALLBACK_SCORE = 0.55    # seed docs can answer alone ONLY if this strong


def analyse_retrieval(hits: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a summary of what the retriever actually found, bucketed by source."""
    smifs = [h for h in hits if h.get("source") == "smifs_knowledge"]
    seed = [h for h in hits if h.get("source") == "seed"]
    other = [h for h in hits if h.get("source") not in ("smifs_knowledge", "seed")]
    return {
        "smifs_top": smifs[0]["score"] if smifs else 0.0,
        "smifs_count": len(smifs),
        "seed_top": seed[0]["score"] if seed else 0.0,
        "seed_count": len(seed),
        "other_count": len(other),
        "has_smifs_strong": bool(smifs) and smifs[0]["score"] >= SMIFS_STRONG_SCORE,
        "has_seed_strong": bool(seed) and seed[0]["score"] >= SEED_FALLBACK_SCORE,
    }


def should_refuse_product_query(message: str, analysis: Dict[str, Any]) -> bool:
    """For product topics, refuse if neither SMIFS KB nor strong seed coverage."""
    if not is_product_topic(message):
        return False
    return not (analysis.get("has_smifs_strong") or analysis.get("has_seed_strong"))


REFUSAL_REPLY = (
    "I don't have verified information on this in Mackertich ONE's knowledge base. "
    "Let me connect you to a senior advisor — they'll have the current, accurate details."
)


async def log_event(db, *, session_id: Optional[str], message: str,
                    reply_text: str, analysis: Dict[str, Any],
                    claims: List[Dict[str, str]], action: str) -> None:
    """Persist the turn to `hallucination_events` for admin review. Best-effort."""
    try:
        await db.hallucination_events.insert_one({
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "user_message": (message or "")[:1200],
            "reply_text": (reply_text or "")[:4000],
            "analysis": analysis,
            "claims": claims,
            "action": action,  # "refused" | "flagged" | "unchecked_claim" | "regen"
        })
    except Exception as e:  # never break the reply path
        logger.warning("hallucination_events insert failed: %s", e)


async def recent_count(db, days: int = 7) -> int:
    """Admin KPI — count of low-confidence events in the last N days."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return await db.hallucination_events.count_documents({"created_at": {"$gte": cutoff}})
