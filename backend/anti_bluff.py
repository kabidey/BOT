"""Phase 24b — Anti-Bluff / Anti-Hallucination Rail.

Three guards applied during every RAG/tool turn:

1. `confidence_score()` — derive {top_score, mean_top3, citation_count,
   confidence: high|medium|low|none} from reranked chunks. Thresholds are
   env-tunable (`RAG_CONFIDENCE_HIGH`, `RAG_CONFIDENCE_MED`).

2. `validate_compose()` — programmatic post-compose regex validator.
   If the LLM composed factual claims (regulation numbers, named circulars,
   percentages, dates) WITHOUT any citations, returns a rewrite payload
   that replaces the message with the escalation rail.

3. `build_escalation_rail()` — produces the structured response when
   confidence is low OR the post-compose validator trips. Includes a
   handoff_request block and (silently) writes a knowledge_gap_log entry.

ALL guards are no-ops when DB is None (test harnesses).
"""
from __future__ import annotations
import logging
import os
import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ----- Tunable thresholds (env-overridable) -----
# Calibrated against the live SMIFS doc_chunks corpus (cosine on
# text-embedding-3-large). KYC-type questions (well-covered) land at ~0.65–0.75.
# Off-corpus questions (e.g. "dress code at Mumbai office") land in the
# false-match 0.50–0.55 band where cosine reacts to overlapping brand /
# location tokens without semantic relevance. Therefore HIGH=0.60 / MED=0.55
# correctly puts those into the "low" bucket and triggers the rail.
# For environments using the reranker (claude-haiku), set
# `RAG_CONFIDENCE_HIGH=0.55` / `RAG_CONFIDENCE_MED=0.35` to match the
# spec's reranker-score distribution.
HIGH_THR: float = float(os.environ.get("RAG_CONFIDENCE_HIGH", "0.60"))
MED_THR: float = float(os.environ.get("RAG_CONFIDENCE_MED", "0.55"))

# ----- Hard rule appendix injected into compose system prompt -----
HARD_RULES_BLOCK = (
    "\n\nHARD RULES (Phase 24b — Anti-Bluff Rail):\n"
    "1. Every factual claim about SMIFS, products, regulations, market data, or client "
    "information MUST be grounded in a tool result or retrieved chunk in your provided "
    "context. If a fact is not in the context, do NOT state it.\n"
    "2. If you do not have a high-confidence source for a claim, do NOT make the claim. "
    "Instead, say \"I don't have a confident answer on that, but our research desk can help\" "
    "and request a handoff.\n"
    "3. NEVER invent a circular number, regulation name, section number, product feature, "
    "fee schedule, tenure, or financial figure. NEVER expand an acronym you are not certain "
    "about (e.g., do NOT guess \"PIT\" → \"Private Placement\" — PIT means \"Prohibition of "
    "Insider Trading\". If unsure, ask the user to clarify rather than guess).\n"
    "4. When tool_calls return citations, ALWAYS cite them implicitly by quoting or "
    "paraphrasing the source content. Do NOT inline citation indices like [1][2] — the UI "
    "surfaces citations separately as chips.\n"
)

# Localized soft-handoff CTA snippet (English; locale layer can translate
# downstream via existing Phase 18 locale_instruction).
SOFT_HANDOFF_CTA_TEMPLATES = [
    "If you want a deeper, sourced view, our research desk can speak with you directly — "
    "happy to set that up.",
    "I can offer a starting view here, but for a regulatory-grade answer it's worth "
    "speaking to our advisory desk — want me to set up a callback?",
]

# Localized escalation copy variants for the low-confidence rail.
ESCALATION_COPY = [
    "I want to give you a precise, sourced answer on this — and I don't have one with "
    "high confidence right now. Let me connect you to our advisory team who can give you "
    "the regulatory-grade response you deserve.",
    "This is the kind of question I'd rather have a specialist answer — let me pull in "
    "our research desk so you get a properly sourced response.",
    "Consulting our research desk — I'd rather route this to a human advisor than guess. "
    "Want me to arrange a callback?",
]


def confidence_score(citations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute a confidence verdict from a list of (re-ranked) citation
    chunks. Each citation should carry a numeric `score` (RRF / cross-encoder
    / cosine — any monotonic score works as long as upstream is consistent).

    Returns: {top_score, mean_top3, citation_count, confidence}
      confidence ∈ {"high", "medium", "low", "none"}
    """
    if not citations:
        return {"top_score": 0.0, "mean_top3": 0.0, "citation_count": 0, "confidence": "none"}
    scores = [float(c.get("score") or c.get("score_rrf") or 0.0) for c in citations]
    scores_sorted = sorted(scores, reverse=True)
    top_score = scores_sorted[0]
    mean_top3 = sum(scores_sorted[:3]) / max(1, min(3, len(scores_sorted)))
    if top_score >= HIGH_THR:
        verdict = "high"
    elif top_score >= MED_THR:
        verdict = "medium"
    else:
        verdict = "low"
    return {
        "top_score": round(top_score, 4),
        "mean_top3": round(mean_top3, 4),
        "citation_count": len(citations),
        "confidence": verdict,
    }


# ----- Programmatic post-compose validator -----
# Pattern matches "factual claim" patterns: regulator names + identifier, regulation
# names + section/clause numbers, percentages over 1%, "circular/notification/section <num>".
_FACTUAL_PATTERNS = [
    re.compile(r"\b(SEBI|RBI|MCA|IRDAI|AMFI|PFRDA|NSE|BSE|CDSL|NSDL)\s+"
               r"(?:circular|regulation|notification|section|clause|order|act|guideline|rule)\s*[A-Z0-9/\-]+", re.I),
    re.compile(r"\b(?:circular|notification|regulation|section|clause|act|rule)\s+(?:no\.?\s*)?[A-Z0-9/\-]{2,}", re.I),
    re.compile(r"\b\d{1,3}(?:\.\d+)?\s*%"),  # percentages
    re.compile(r"\bRs\.?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*(?:lakh|crore|cr|L|million|bn))?", re.I),  # INR figures
    re.compile(r"\b(?:19|20)\d{2}\b"),  # year references (e.g., "PIT Regulations, 2015")
]


def _count_factual_claims(text: str) -> int:
    if not text:
        return 0
    n = 0
    for pat in _FACTUAL_PATTERNS:
        n += len(pat.findall(text))
    return n


def validate_compose(text: str, citations: List[Dict[str, Any]],
                     *, min_factual_for_rewrite: int = 2) -> Dict[str, Any]:
    """Programmatic guard. If the composed text contains ≥ N factual-claim
    patterns AND the citations list is empty, returns:
        {"action": "rewrite", "reason": "ungrounded_factual_claims",
         "factual_claim_count": <int>}
    Otherwise:
        {"action": "ok", "factual_claim_count": <int>}
    """
    claim_count = _count_factual_claims(text or "")
    if claim_count >= min_factual_for_rewrite and not citations:
        return {
            "action": "rewrite",
            "reason": "ungrounded_factual_claims",
            "factual_claim_count": claim_count,
        }
    return {"action": "ok", "factual_claim_count": claim_count}


def _pick_copy(message: Optional[str] = None) -> str:
    seed = (message or "") + str(int(datetime.now(timezone.utc).timestamp()) // 30)
    rnd = random.Random(seed)
    return rnd.choice(ESCALATION_COPY)


def _pick_soft_cta(message: Optional[str] = None) -> str:
    seed = (message or "") + str(int(datetime.now(timezone.utc).timestamp()) // 30)
    rnd = random.Random(seed)
    return rnd.choice(SOFT_HANDOFF_CTA_TEMPLATES)


def build_soft_handoff_cta(message: Optional[str] = None) -> str:
    """Returns a single-sentence soft handoff CTA to append to medium-confidence
    answers. Caller is responsible for appending to the LLM reply text."""
    return _pick_soft_cta(message)


def build_escalation_rail(message: str,
                          confidence: Dict[str, Any],
                          reason: str = "low_confidence") -> Dict[str, Any]:
    """Construct the structured low-confidence escalation envelope. Returns a
    dict directly compatible with the orchestrator's `out` envelope shape:
        {reply_text, blocks, citations: [], grounded: False, intent_hint}
    """
    copy = _pick_copy(message)
    intent_line = (message or "").strip().splitlines()[0][:140] or "advisory question"
    rail_block = {
        "type": "low_confidence_escalation",
        "intent": intent_line,
        "user_facing_text": copy,
        "reason": reason,
        "confidence": confidence,
    }
    handoff_block = {
        "type": "handoff_request",
        "channels": ["callback", "email", "whatsapp"],
    }
    return {
        "reply_text": copy,
        "blocks": [rail_block, handoff_block],
        "citations": [],
        "grounded": False,
        "model": None,
        "intent_hint": "LOW_CONFIDENCE_ESCALATION",
    }


async def log_bluff_event(db, *, session_id: Optional[str], message: str,
                          confidence: Dict[str, Any], outcome: str,
                          reason: Optional[str] = None) -> None:
    """Persist one anti-bluff decision to the `bluff_events` collection.

    outcome ∈ {"answered_grounded", "answered_with_caveat", "escalated"}
    """
    if db is None:
        return
    try:
        await db.bluff_events.insert_one({
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "user_message": (message or "")[:500],
            "top_score": confidence.get("top_score"),
            "mean_top3": confidence.get("mean_top3"),
            "citation_count": confidence.get("citation_count"),
            "confidence": confidence.get("confidence"),
            "outcome": outcome,
            "reason": reason,
        })
    except Exception:
        logger.exception("Failed to log bluff_event")


async def log_knowledge_gap(db, *, session_id: Optional[str], topic: str,
                            confidence_at_decline: float) -> None:
    """Silently push a row to `knowledge_gaps_log` (visible only to the admin
    content team via `Knowledge Gaps` tab). Never shown to the user."""
    if db is None:
        return
    try:
        await db.knowledge_gaps_log.insert_one({
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "topic": (topic or "")[:500],
            "confidence_at_decline": confidence_at_decline,
        })
    except Exception:
        logger.exception("Failed to log knowledge_gap")


async def bluff_summary(db, *, days: int = 7) -> Dict[str, Any]:
    """Aggregate the last N days of bluff_events for the admin tile."""
    if db is None:
        return {"window_days": days, "buckets": {}, "total": 0}
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cursor = db.bluff_events.find({"ts": {"$gte": since}}, {"_id": 0, "outcome": 1, "confidence": 1})
    buckets = {"answered_grounded": 0, "answered_with_caveat": 0, "escalated": 0}
    by_conf = {"high": 0, "medium": 0, "low": 0, "none": 0}
    total = 0
    async for ev in cursor:
        total += 1
        out = ev.get("outcome") or "answered_grounded"
        if out in buckets:
            buckets[out] += 1
        c = ev.get("confidence") or "none"
        if c in by_conf:
            by_conf[c] += 1
    return {
        "window_days": days,
        "total": total,
        "buckets": buckets,
        "by_confidence": by_conf,
        "thresholds": {"high": HIGH_THR, "medium": MED_THR},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
