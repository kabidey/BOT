"""RAG specialist agent — wraps Phase 1 retrieval + grounded generation."""
from __future__ import annotations
import logging
from typing import Any, Dict, List

import rag

from .llm import chat_with_fallback, extract_reply

logger = logging.getLogger(__name__)

RAG_TOP_K = 5
RAG_MIN_SCORE = 0.25
RAG_HISTORY_TURNS = 10

BASE_PROMPT = (
    "You are the Lead Wealth-Engagement Agent for SMIFS Management Services Limited. "
    "Sophisticated, precise, empathetic, professional tone — the voice of a senior wealth manager. "
    "Replies should be concise and considered."
)

GROUNDED_INSTR = (
    "\n\nUse ONLY the KNOWLEDGE BASE passages below for SMIFS-specific facts (figures, regulations, fees, taxation, processes). "
    "Do NOT invent SMIFS-specific facts. Do NOT enumerate citation IDs in your reply — citations are surfaced separately. "
    "If the passages do not contain the answer, say so plainly and offer to connect the client with a human advisor."
)

UNGROUNDED_INSTR = (
    "\n\nThe internal SMIFS knowledge base does not contain a confident match for this query. "
    "Acknowledge the limit briefly and offer to connect the client with a human advisor. "
    "You may speak in general financial-literacy terms, but do not attribute specifics to SMIFS."
)


async def answer(message: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
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
        for h in hits[:3]:
            if h["score"] >= RAG_MIN_SCORE:
                citations.append({
                    "doc_id": h["doc_id"],
                    "doc_title": h["doc_title"],
                    "section": h["section"],
                    "score": round(h["score"], 4),
                    "text": h["text"],
                })
    return {
        "reply_text": reply_text,
        "citations": citations,
        "grounded": grounded,
        "model": model_used,
    }
