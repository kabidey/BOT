"""RAG specialist agent — wraps Phase 1 retrieval + grounded generation.

Phase 6 (Apr 2026): SMIFS knowledge passages are now sent to Hub AI via the
native `context_chunks` field (verified supported — see HUB_AI_CAPABILITIES.md).
This is cleaner than stuffing them into a "KNOWLEDGE BASE" system-prompt block:
prompt_tokens are framed correctly by Hub, the model receives them in a
structured slot, and citations remain driven by our own retrieval scores.
"""
from __future__ import annotations
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import rag

from .llm import chat_with_fallback, stream_chat_with_fallback, extract_reply

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
    "\n\nWhen SMIFS knowledge passages are attached to this turn (as `context_chunks`), you MUST extract "
    "specific facts (figures, regulations, fees, taxation, processes, eligibility, tenure, lock-ins, ticket sizes) "
    "directly from those passages and answer the user's question concretely. "
    "Synthesise across multiple passages when the answer spans them. "
    "Do NOT respond with generic punts like 'please consult an advisor' or 'I do not have information' "
    "when the passages clearly contain the answer — that is a failure mode. "
    "Do NOT invent SMIFS-specific facts beyond what the passages state. "
    "Do NOT enumerate citation IDs (e.g. [1], [2]) in your reply — citations are surfaced separately in the UI. "
    "ONLY if the passages genuinely do not contain the requested information, briefly acknowledge the gap "
    "and offer to connect the client with a human advisor."
)

UNGROUNDED_INSTR = (
    "\n\nThe internal SMIFS knowledge base does not contain a confident match for this query. "
    "Acknowledge the limit briefly and offer to connect the client with a human advisor. "
    "You may speak in general financial-literacy terms, but do not attribute specifics to SMIFS."
)


def _hits_to_chunks(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert RAG hits (passing the score threshold) to Hub AI `context_chunks` payload."""
    return [
        {
            "id": f"{h['doc_id']}::{h['section']}",
            "text": h["text"],
            "title": h["doc_title"],
            "section": h["section"],
            "source": h["doc_id"],
        }
        for h in hits if h["score"] >= RAG_MIN_SCORE
    ]


def _build_messages(message: str, history: List[Dict[str, Any]],
                    grounded: bool, client_context: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    system_content = BASE_PROMPT + (GROUNDED_INSTR if grounded else UNGROUNDED_INSTR)
    if client_context:
        from .auth_agent import client_context_block
        system_content = system_content + client_context_block(client_context)
    trimmed = history[-(RAG_HISTORY_TURNS * 2):]
    history_msgs = [{"role": m["role"], "content": m["content"]} for m in trimmed]
    return [{"role": "system", "content": system_content}] + history_msgs + [
        {"role": "user", "content": message},
    ]


def _build_citations(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    citations: List[Dict[str, Any]] = []
    seen_docs: set = set()
    for h in hits:
        if h["score"] < RAG_MIN_SCORE:
            continue
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
    return citations


async def _retrieve(message: str) -> Tuple[List[Dict[str, Any]], bool]:
    hits = await rag.search(message, top_k=RAG_TOP_K)
    grounded = bool(hits) and any(h["score"] >= RAG_MIN_SCORE for h in hits)
    return hits, grounded


async def answer(message: str, history: List[Dict[str, Any]],
                 client_context: Optional[Dict[str, Any]] = None,
                 session_id: Optional[str] = None) -> Dict[str, Any]:
    """Non-streaming entry point. Returns {reply_text, citations, grounded, model}."""
    hits, grounded = await _retrieve(message)
    messages = _build_messages(message, history, grounded, client_context)
    chunks = _hits_to_chunks(hits) if grounded else None

    result = await chat_with_fallback(
        messages, context_chunks=chunks, session_id=session_id, intent="KNOWLEDGE",
    )
    reply_text = extract_reply(result["data"])
    model_used = result["data"].get("model") or result["model"]
    return {
        "reply_text": reply_text,
        "citations": _build_citations(hits) if grounded else [],
        "grounded": grounded,
        "model": model_used,
    }


async def stream_answer(message: str, history: List[Dict[str, Any]],
                        client_context: Optional[Dict[str, Any]] = None,
                        session_id: Optional[str] = None) -> AsyncGenerator[Tuple[str, Any], None]:
    """Streaming entry point.
    Yields (event_type, payload):
      - ('citations', [...])  — emitted ONCE up-front so the UI can render citation chips early
      - ('token', str)        — incremental content tokens
      - ('done', {'reply_text','citations','grounded','model'})
    """
    hits, grounded = await _retrieve(message)
    citations = _build_citations(hits) if grounded else []
    yield ("citations", citations)

    messages = _build_messages(message, history, grounded, client_context)
    chunks = _hits_to_chunks(hits) if grounded else None

    full_text = ""
    model_used: Optional[str] = None
    try:
        async for ev, data in stream_chat_with_fallback(
            messages, context_chunks=chunks, session_id=session_id, intent="KNOWLEDGE",
        ):
            if ev == "token":
                full_text += data
                yield ("token", data)
            elif ev == "done":
                full_text = data.get("reply_text", full_text)
                model_used = data.get("model")
    except Exception as e:
        logger.warning("RAG stream failed (%s); falling back to non-streaming.", e)
        result = await chat_with_fallback(
            messages, context_chunks=chunks, session_id=session_id, intent="KNOWLEDGE",
        )
        full_text = extract_reply(result["data"])
        model_used = result["data"].get("model") or result["model"]
        # Emit the whole reply as a single token so the UI still gets text.
        yield ("token", full_text)

    yield ("done", {
        "reply_text": full_text,
        "citations": citations,
        "grounded": grounded,
        "model": model_used,
    })
