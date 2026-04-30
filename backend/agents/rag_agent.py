"""RAG specialist agent — wraps Phase 1 retrieval + grounded generation.

Phase 9 (Apr 2026): SMIFS Knowledge API is the PRIMARY corpus. Retrieval is
source-weighted (smifs_knowledge > seed > upload > session_archive). For
product/offering questions we apply a categorical gate (reject upload +
archive) and enforce a strict grounding threshold — below it the bot refuses
+ escalates rather than hallucinate.
"""
from __future__ import annotations
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import rag
import guardrails

from .llm import chat_with_fallback, stream_chat_with_fallback, extract_reply

logger = logging.getLogger(__name__)

RAG_TOP_K = 8
RAG_MIN_SCORE = 0.15
RAG_HISTORY_TURNS = 10

BASE_PROMPT = (
    "You are the Mackertich ONE Advisor — the wealth-engagement agent for Mackertich ONE, "
    "the wealth-management vertical of SMIFS Ltd. "
    "Sophisticated, precise, empathetic, professional tone — the voice of a senior private-bank wealth manager. "
    "Replies should be concise and considered."
)

KNOWLEDGE_PRIORITY_RULES = (
    "\n\nKNOWLEDGE PRIORITY RULES:\n"
    "1. SMIFS Knowledge (any passage whose `source` field is `smifs_knowledge`) is the AUTHORITATIVE "
    "source for all Mackertich ONE / SMIFS product, offering, and policy information. ALWAYS prefer it.\n"
    "2. When a SMIFS Knowledge passage is in the provided context, quote or paraphrase it precisely. "
    "Do not contradict it.\n"
    "3. If the provided context does not cover the user's question, say so explicitly and offer to "
    "connect them with an advisor. Do NOT invent product details, minimums, fees, returns, tenures, "
    "lock-ins, taxation, or compliance statements.\n"
    "4. Seed documentation (source=seed) is generic financial literacy — use only to supplement "
    "SMIFS Knowledge or for purely educational topics not covered officially.\n"
    "5. Do NOT enumerate citation IDs (e.g. [1], [2]) inline — citations are surfaced separately in the UI.\n"
)

GROUNDED_INSTR = KNOWLEDGE_PRIORITY_RULES + (
    "\n\nWhen SMIFS knowledge passages are attached to this turn (as `context_chunks`), extract "
    "specific facts (figures, regulations, fees, taxation, processes, eligibility, tenure, lock-ins, ticket sizes) "
    "directly from those passages and answer the user's question concretely. "
    "Synthesise across multiple passages when the answer spans them. "
    "Do NOT respond with generic punts like 'please consult an advisor' when the passages clearly contain the answer. "
    "ONLY if the passages genuinely do not contain the requested information, briefly acknowledge the gap "
    "and offer to connect the client with a human advisor."
)

UNGROUNDED_INSTR = KNOWLEDGE_PRIORITY_RULES + (
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
            "source": h.get("source", "seed"),
        }
        for h in hits if h["score"] >= RAG_MIN_SCORE
    ]


def _build_messages(message: str, history: List[Dict[str, Any]],
                    grounded: bool, client_context: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    system_content = BASE_PROMPT + (GROUNDED_INSTR if grounded else UNGROUNDED_INSTR)
    if client_context:
        from .auth_agent import context_block_for
        block = context_block_for(client_context)
        if block:
            system_content = system_content + block
    trimmed = history[-(RAG_HISTORY_TURNS * 2):]
    history_msgs = [{"role": m["role"], "content": m["content"]} for m in trimmed]
    return [{"role": "system", "content": system_content}] + history_msgs + [
        {"role": "user", "content": message},
    ]


def _build_citations(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Surface up to 5 citations: prefer distinct doc_ids, but if fewer than 3 distinct
    docs pass the score threshold, fall back to including additional chunks from the
    top-scoring docs so the UI always has a meaningful citation set."""
    qualifying = [h for h in hits if h["score"] >= RAG_MIN_SCORE]
    if not qualifying:
        return []

    def _enrich(h: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "doc_id": h["doc_id"],
            "doc_title": h["doc_title"],
            "section": h["section"],
            "score": round(h["score"], 4),
            "raw_score": round(h.get("raw_score", h["score"]), 4),
            "text": h["text"],
            "source": h.get("source", "seed"),
            "subsource": h.get("subsource"),
            "is_official": h.get("source") == "smifs_knowledge",
        }

    citations: List[Dict[str, Any]] = []
    seen_docs: set = set()
    for h in qualifying:
        if h["doc_id"] in seen_docs:
            continue
        seen_docs.add(h["doc_id"])
        citations.append(_enrich(h))
        if len(citations) >= 5:
            break
    if len(citations) < 3:
        existing_keys = {(c["doc_id"], c["section"]) for c in citations}
        for h in qualifying:
            key = (h["doc_id"], h["section"])
            if key in existing_keys:
                continue
            existing_keys.add(key)
            citations.append(_enrich(h))
            if len(citations) >= 5:
                break
    return citations


async def _retrieve(message: str) -> Tuple[List[Dict[str, Any]], bool, Dict[str, Any]]:
    """Phase 9 retrieval with source weighting + product-topic gating."""
    restrict: Optional[List[str]] = None
    if guardrails.is_product_topic(message):
        # Hard-gate product / offering questions to official corpora.
        restrict = ["smifs_knowledge", "seed"]
    hits = await rag.search_weighted(message, top_k=RAG_TOP_K, restrict_sources=restrict)
    grounded = bool(hits) and any(h["score"] >= RAG_MIN_SCORE for h in hits)
    analysis = guardrails.analyse_retrieval(hits)
    return hits, grounded, analysis


async def answer(message: str, history: List[Dict[str, Any]],
                 client_context: Optional[Dict[str, Any]] = None,
                 session_id: Optional[str] = None,
                 db=None) -> Dict[str, Any]:
    """Non-streaming entry point. Returns {reply_text, citations, grounded, model}."""
    hits, grounded, analysis = await _retrieve(message)
    citations = _build_citations(hits) if grounded else []

    # Phase 9 — refusal enforcement: for product topics without KB coverage,
    # short-circuit with an honest escalation instead of letting the LLM riff.
    if db is not None and guardrails.should_refuse_product_query(message, analysis):
        await guardrails.log_event(
            db, session_id=session_id, message=message,
            reply_text=guardrails.REFUSAL_REPLY, analysis=analysis,
            claims=[], action="refused",
        )
        return {
            "reply_text": guardrails.REFUSAL_REPLY,
            "citations": citations,
            "grounded": False,
            "model": None,
            "intent_hint": "ESCALATION",
        }

    messages = _build_messages(message, history, grounded, client_context)
    chunks = _hits_to_chunks(hits) if grounded else None

    result = await chat_with_fallback(
        messages, context_chunks=chunks, session_id=session_id, intent="KNOWLEDGE",
    )
    reply_text = extract_reply(result["data"])
    model_used = result["data"].get("model") or result["model"]

    # Phase 9 — post-gen flagging: detect unsupported confident claims.
    if db is not None and reply_text:
        claims = guardrails.detect_claims(reply_text)
        if claims and not guardrails.citation_supports_claims(claims, reply_text, citations):
            await guardrails.log_event(
                db, session_id=session_id, message=message,
                reply_text=reply_text, analysis=analysis,
                claims=claims, action="unchecked_claim",
            )

    return {
        "reply_text": reply_text,
        "citations": citations,
        "grounded": grounded,
        "model": model_used,
    }


async def stream_answer(message: str, history: List[Dict[str, Any]],
                        client_context: Optional[Dict[str, Any]] = None,
                        session_id: Optional[str] = None,
                        db=None) -> AsyncGenerator[Tuple[str, Any], None]:
    """Streaming entry point.
    Yields (event_type, payload):
      - ('citations', [...])  — emitted ONCE up-front so the UI can render citation chips early
      - ('token', str)        — incremental content tokens
      - ('done', {'reply_text','citations','grounded','model'})
    """
    hits, grounded, analysis = await _retrieve(message)
    citations = _build_citations(hits) if grounded else []
    yield ("citations", citations)

    # Phase 9 — refusal short-circuit for product queries with no KB coverage.
    if db is not None and guardrails.should_refuse_product_query(message, analysis):
        await guardrails.log_event(
            db, session_id=session_id, message=message,
            reply_text=guardrails.REFUSAL_REPLY, analysis=analysis,
            claims=[], action="refused",
        )
        yield ("token", guardrails.REFUSAL_REPLY)
        yield ("done", {
            "reply_text": guardrails.REFUSAL_REPLY,
            "citations": citations,
            "grounded": False,
            "model": None,
            "intent_hint": "ESCALATION",
        })
        return

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
        yield ("token", full_text)

    # Phase 9 — post-gen claim flagging (best-effort)
    if db is not None and full_text:
        claims = guardrails.detect_claims(full_text)
        if claims and not guardrails.citation_supports_claims(claims, full_text, citations):
            await guardrails.log_event(
                db, session_id=session_id, message=message,
                reply_text=full_text, analysis=analysis,
                claims=claims, action="unchecked_claim",
            )

    yield ("done", {
        "reply_text": full_text,
        "citations": citations,
        "grounded": grounded,
        "model": model_used,
    })
