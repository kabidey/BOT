"""RAG specialist agent — wraps Phase 1 retrieval + grounded generation."""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

import rag

from .llm import chat_with_fallback, extract_reply

logger = logging.getLogger(__name__)

RAG_TOP_K = 8
RAG_MIN_SCORE = 0.15
RAG_HISTORY_TURNS = 10

BASE_PROMPT = (
    "You are the Lead Wealth-Engagement Agent for SMIFS Management Services Limited. "
    "Sophisticated, precise, empathetic, professional tone — the voice of a senior wealth manager. "
    "Replies should be concise and considered."
)

GROUNDED_INSTR = (
    "\n\nWhen KNOWLEDGE BASE passages are provided below, you MUST extract specific facts "
    "(figures, regulations, fees, taxation, processes, eligibility, tenure, lock-ins, ticket sizes) "
    "directly from those passages and answer the user's question concretely. "
    "Synthesise across multiple passages when the answer spans them. "
    "Do NOT respond with generic punts like 'please consult an advisor' or 'I do not have information' "
    "when the passages clearly contain the answer — that is a failure mode. "
    "Do NOT invent SMIFS-specific facts beyond what the passages state. "
    "Do NOT enumerate citation IDs (e.g. [1], [2]) in your reply — citations are surfaced separately in the UI. "
    "ONLY if the passages genuinely do not contain the requested information, briefly acknowledge the gap "
    "and offer to connect the client with a human advisor.\n\n"
    "EXAMPLE — passages contain the answer:\n"
    "User: What is the minimum investment for an AIF?\n"
    "Passages mention: 'SEBI mandates a minimum of ₹1 crore per investor across all AIF categories.'\n"
    "Good reply: 'For Alternative Investment Funds, SEBI mandates a minimum commitment of ₹1 crore per "
    "investor, applicable across all three AIF categories. At SMIFS we typically evaluate AIF allocations "
    "only for clients whose investable surplus comfortably accommodates this threshold.'\n"
    "Bad reply: 'AIF investment minimums vary; please connect with an advisor.' (← refuses despite "
    "having the answer — never do this.)"
)

UNGROUNDED_INSTR = (
    "\n\nThe internal SMIFS knowledge base does not contain a confident match for this query. "
    "Acknowledge the limit briefly and offer to connect the client with a human advisor. "
    "You may speak in general financial-literacy terms, but do not attribute specifics to SMIFS."
)


async def answer(message: str, history: List[Dict[str, Any]],
                 client_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Returns {reply_text, citations, grounded, model}."""
    hits = await rag.search(message, top_k=RAG_TOP_K)
    grounded = bool(hits) and any(h["score"] >= RAG_MIN_SCORE for h in hits)

    if grounded:
        kb_block = "\n\n".join(
            f"[{i+1}] ({h['doc_title']} · §{h['section']})\n{h['text']}"
            for i, h in enumerate(hits) if h["score"] >= RAG_MIN_SCORE
        )
        system_content = BASE_PROMPT + GROUNDED_INSTR + "\n\n--- KNOWLEDGE BASE ---\n" + kb_block + "\n--- END KNOWLEDGE BASE ---"
    else:
        system_content = BASE_PROMPT + UNGROUNDED_INSTR

    if client_context:
        # Inline import to avoid a circular dep at import time.
        from .auth_agent import client_context_block
        system_content = system_content + client_context_block(client_context)

    trimmed = history[-(RAG_HISTORY_TURNS * 2):]
    history_msgs = [{"role": m["role"], "content": m["content"]} for m in trimmed]
    messages = [{"role": "system", "content": system_content}] + history_msgs + [
        {"role": "user", "content": message},
    ]

    result = await chat_with_fallback(messages)
    reply_text = extract_reply(result["data"])
    model_used = result["data"].get("model") or result["model"]

    citations: List[Dict[str, Any]] = []
    if grounded:
        seen_docs: set = set()
        for h in hits:
            if h["score"] < RAG_MIN_SCORE:
                continue
            # Prefer one citation per distinct doc; fall back to extra chunks if we still have room
            if h["doc_id"] in seen_docs:
                continue
            seen_docs.add(h["doc_id"])
            citations.append({
                "doc_id": h["doc_id"],
                "doc_title": h["doc_title"],
                "section": h["section"],
                "score": round(h["score"], 4),
                "text": h["text"],
            })
            if len(citations) >= 5:
                break
    return {
        "reply_text": reply_text,
        "citations": citations,
        "grounded": grounded,
        "model": model_used,
    }
