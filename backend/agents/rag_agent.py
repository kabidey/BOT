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

# Phase 10 — canonical WM-fallback trigger phrase (kept in sync with
# identity.wealth_manager_fallback_text). Detected in generated replies to
# synthesise an escalation_card block even if the keyword short-circuit
# didn't fire (e.g. third-party fund names the product-topic list misses).
_WM_FALLBACK_PHRASE = "don't have that information in your record"


def _maybe_synthesize_wm_block(reply_text: str,
                               session_type: Optional[str],
                               auth_state: Optional[str],
                               client_context: Optional[Dict[str, Any]],
                               existing_blocks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Safety net: if a verified client got the WM fallback phrase in the
    reply but no escalation_card was produced upstream, synthesise it here.

    Returns (fallback_blocks, intent_hint).
    """
    if session_type != "client" or auth_state != "verified":
        return existing_blocks, None
    if existing_blocks:  # already handled by the short-circuit
        return existing_blocks, None
    if not reply_text or _WM_FALLBACK_PHRASE not in reply_text.lower():
        return existing_blocks, None
    import fallback as _fb
    fb = _fb.make_wealth_manager_fallback(session_type, auth_state, client_context)
    return (fb.get("extra_blocks") or []), fb.get("intent_hint", "ESCALATION")


def _should_short_circuit_to_wm(message: str,
                                session_type: Optional[str],
                                auth_state: Optional[str],
                                hits: List[Dict[str, Any]],
                                analysis: Dict[str, Any]) -> bool:
    """Phase 11 bug-3 fix — smarter short-circuit to WM fallback.

    Rules:
      • Verified employee → never short-circuit here (let them use KB).
      • Verified client → always escalate any product-topic question
        (Phase 10 behaviour preserved).
      • Visitor / unverified → escalate if brand-specific (Mackertich,
        SMIFS, Sapphire, Alchemy …) OR product-topic WITHOUT strong seed
        grounding (top score < 0.45). Otherwise let generic educational
        questions like "What is an AIF?" answer from seed.
    """
    if session_type == "employee" and auth_state == "verified":
        return False
    if session_type == "client" and auth_state == "verified":
        return guardrails.is_product_topic(message)
    if guardrails.is_brand_specific_product_topic(message):
        return True
    if guardrails.is_product_topic(message):
        return not guardrails.has_strong_grounding(analysis, hits=hits, min_score=0.45)
    return False


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
    "\n\nPhase 16 — context_chunks may carry tag preambles (e.g. `[Vehicle: Sapphire AIF · AIF]`, "
    "`[Updated: 2026-03-24]`, `[Version: v8]`, `[Focused · Active]`). When citing a specific vehicle "
    "or bedrock asset, you MAY weave in the version or update date naturally (e.g. 'per the Mar 2026 "
    "vehicle update' or 'as of Fortnightly Offering v8'). Prefer vehicles tagged `Focused` and `Active` "
    "when suggesting the SMIFS house view. Never invent dates or version numbers — only cite what the "
    "preambles explicitly list."
)

UNGROUNDED_INSTR = KNOWLEDGE_PRIORITY_RULES + (
    "\n\nThe internal SMIFS knowledge base does not contain a confident match for this query. "
    "Acknowledge the limit briefly and offer to connect the client with a human advisor. "
    "You may speak in general financial-literacy terms, but do not attribute specifics to SMIFS."
)


def _hits_to_chunks(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert RAG hits (passing the score threshold) to Hub AI `context_chunks` payload.

    Phase 16 — when a hit carries projected metadata (vehicle, version, updated
    timestamp), inject a compact tag preamble into the chunk `text` so the LLM
    can cite "per the 24 Mar 2026 vehicle update / v8" rather than producing a
    generic answer.
    """
    out: List[Dict[str, Any]] = []
    for h in hits:
        if h["score"] < RAG_MIN_SCORE:
            continue
        preamble_lines: List[str] = []
        sub = h.get("subsource")
        if sub:
            preamble_lines.append(f"[Type: {sub}]")
        if h.get("vehicle_name"):
            vtype = h.get("vehicle_type")
            preamble_lines.append(
                f"[Vehicle: {h['vehicle_name']}" + (f" · {vtype}" if vtype else "") + "]"
            )
        if h.get("version_no") is not None:
            preamble_lines.append(f"[Version: v{h['version_no']}]")
        if h.get("updated_at_iso"):
            preamble_lines.append(f"[Updated: {h['updated_at_iso'][:10]}]")
        flags: List[str] = []
        if h.get("is_focused") is True:
            flags.append("Focused")
        if h.get("is_active") is True:
            flags.append("Active")
        if flags:
            preamble_lines.append(f"[{' · '.join(flags)}]")
        prov = h.get("provider") or h.get("vertical")
        if prov:
            preamble_lines.append(f"[Provider: {prov}]")
        text = h["text"]
        if preamble_lines:
            text = "  ".join(preamble_lines) + "\n---\n" + text
        out.append({
            "id": f"{h['doc_id']}::{h['section']}",
            "text": text,
            "title": h["doc_title"],
            "section": h["section"],
            "source": h.get("source", "seed"),
        })
    return out


def _build_messages(message: str, history: List[Dict[str, Any]],
                    grounded: bool, client_context: Optional[Dict[str, Any]],
                    session_type: Optional[str] = None) -> List[Dict[str, str]]:
    system_content = BASE_PROMPT + (GROUNDED_INSTR if grounded else UNGROUNDED_INSTR)
    if client_context:
        from .auth_agent import context_block_for
        block = context_block_for(client_context)
        if block:
            system_content = system_content + block
    # Phase 10 — visitor gets an explicit "no product specifics" addon.
    if (session_type == "visitor") or (session_type is None and not client_context):
        import identity as _id
        system_content = system_content + _id.visitor_context_block()
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
        out = {
            "doc_id": h["doc_id"],
            "doc_title": h["doc_title"],
            "section": h["section"],
            "score": round(h["score"], 4),
            "raw_score": round(h.get("raw_score", h["score"]), 4),
            "text": h["text"],
            "source": h.get("source", "seed"),
            "subsource": h.get("subsource"),
            "is_official": h.get("source") == "smifs_knowledge",
            # Phase 16 — additive citation metadata (backwards-compatible).
            "doc_type": h.get("doc_type") or h.get("subsource"),
            "vehicle_id": h.get("vehicle_id"),
            "vehicle_name": h.get("vehicle_name"),
            "vehicle_type": h.get("vehicle_type"),
            "version_no": h.get("version_no"),
            "version_major": h.get("version_major"),
            "updated_at": h.get("updated_at_iso"),
            "is_focused": h.get("is_focused"),
            "is_active": h.get("is_active"),
            "provider": h.get("provider"),
            "language": h.get("language"),
            "audience": h.get("audience") or "all",
        }
        return {k: v for k, v in out.items() if v is not None}

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


async def _retrieve(message: str, session_type: Optional[str] = None,
                    auth_state: Optional[str] = None) -> Tuple[List[Dict[str, Any]], bool, Dict[str, Any]]:
    """Phase 9 retrieval + Phase 10/16 role gating.

    - employee + verified → all sources, all audiences
    - client + verified   → seed only (product specifics still come from
                            CLIENT_PROFILE / escalate to RM). Phase 16 audience
                            allow-list `["all"]` is also applied so any
                            employee-only SMIFS chunk that ever leaks into seed
                            retrieval is dropped.
    - visitor (anonymous) → seed only (generic education only)
    - mid-verification    → retrieval disabled; return empty
    """
    if auth_state not in (None, "verified", "anonymous"):
        return [], False, guardrails.analyse_retrieval([])
    restrict_audiences: Optional[List[str]] = None
    if session_type == "employee" and auth_state == "verified":
        restrict: Optional[List[str]] = None
        if guardrails.is_product_topic(message):
            restrict = ["smifs_knowledge", "seed"]
    else:
        # Client + visitor: never see SMIFS Knowledge.
        restrict = ["seed"]
        # Phase 16 — additionally drop any chunk tagged `audience=employee_only`.
        restrict_audiences = ["all"]
    hits = await rag.search_weighted(
        message, top_k=RAG_TOP_K,
        restrict_sources=restrict, restrict_audiences=restrict_audiences,
    )
    grounded = bool(hits) and any(h["score"] >= RAG_MIN_SCORE for h in hits)
    analysis = guardrails.analyse_retrieval(hits)
    return hits, grounded, analysis


async def answer(message: str, history: List[Dict[str, Any]],
                 client_context: Optional[Dict[str, Any]] = None,
                 session_id: Optional[str] = None,
                 session_type: Optional[str] = None,
                 auth_state: Optional[str] = None,
                 db=None) -> Dict[str, Any]:
    """Non-streaming entry point. Returns {reply_text, citations, grounded, model}."""
    hits, grounded, analysis = await _retrieve(message, session_type=session_type, auth_state=auth_state)
    citations = _build_citations(hits) if grounded else []

    # Phase 11 — smarter short-circuit: always escalate brand-specific /
    # verified-client product questions; let generic visitor questions answer
    # from seed when grounding is strong.
    if db is not None and _should_short_circuit_to_wm(message, session_type, auth_state, hits, analysis):
        import fallback as _fb
        fb = _fb.make_wealth_manager_fallback(session_type, auth_state, client_context)
        await guardrails.log_event(
            db, session_id=session_id, message=message,
            reply_text=fb["reply_text"], analysis=analysis, claims=[], action="refused",
        )
        return {
            "reply_text": fb["reply_text"], "citations": citations,
            "grounded": False, "model": None,
            "intent_hint": fb.get("intent_hint", "ESCALATION"),
            "fallback_blocks": fb.get("extra_blocks") or [],
        }

    # Phase 9 — refusal for employees too if KB has no strong coverage.
    if db is not None and guardrails.should_refuse_product_query(message, analysis) and (
        session_type == "employee" and auth_state == "verified"
    ):
        await guardrails.log_event(
            db, session_id=session_id, message=message,
            reply_text=guardrails.REFUSAL_REPLY, analysis=analysis, claims=[], action="refused",
        )
        return {
            "reply_text": guardrails.REFUSAL_REPLY, "citations": citations,
            "grounded": False, "model": None, "intent_hint": "ESCALATION",
        }

    messages = _build_messages(message, history, grounded, client_context, session_type=session_type)
    chunks = _hits_to_chunks(hits) if grounded else None
    result = await chat_with_fallback(
        messages, context_chunks=chunks, session_id=session_id, intent="KNOWLEDGE",
    )
    reply_text = extract_reply(result["data"])
    model_used = result["data"].get("model") or result["model"]
    if db is not None and reply_text:
        claims = guardrails.detect_claims(reply_text)
        if claims and not guardrails.citation_supports_claims(claims, reply_text, citations):
            await guardrails.log_event(
                db, session_id=session_id, message=message,
                reply_text=reply_text, analysis=analysis,
                claims=claims, action="unchecked_claim",
            )
    # Phase 10 safety net — synthesise escalation_card for verified-client
    # WM-fallback replies the keyword short-circuit missed.
    synth_blocks, synth_intent = _maybe_synthesize_wm_block(
        reply_text, session_type, auth_state, client_context, existing_blocks=[],
    )
    out: Dict[str, Any] = {
        "reply_text": reply_text, "citations": citations,
        "grounded": grounded, "model": model_used,
    }
    if synth_blocks:
        out["fallback_blocks"] = synth_blocks
        if synth_intent:
            out["intent_hint"] = synth_intent
    return out


async def stream_answer(message: str, history: List[Dict[str, Any]],
                        client_context: Optional[Dict[str, Any]] = None,
                        session_id: Optional[str] = None,
                        session_type: Optional[str] = None,
                        auth_state: Optional[str] = None,
                        db=None) -> AsyncGenerator[Tuple[str, Any], None]:
    hits, grounded, analysis = await _retrieve(message, session_type=session_type, auth_state=auth_state)
    citations = _build_citations(hits) if grounded else []
    yield ("citations", citations)

    # Phase 11 — smarter short-circuit (streaming path).
    if db is not None and _should_short_circuit_to_wm(message, session_type, auth_state, hits, analysis):
        import fallback as _fb
        fb = _fb.make_wealth_manager_fallback(session_type, auth_state, client_context)
        await guardrails.log_event(
            db, session_id=session_id, message=message,
            reply_text=fb["reply_text"], analysis=analysis, claims=[], action="refused",
        )
        yield ("token", fb["reply_text"])
        yield ("done", {
            "reply_text": fb["reply_text"], "citations": citations,
            "grounded": False, "model": None,
            "intent_hint": fb.get("intent_hint", "ESCALATION"),
            "fallback_blocks": fb.get("extra_blocks") or [],
        })
        return

    if db is not None and guardrails.should_refuse_product_query(message, analysis) and (
        session_type == "employee" and auth_state == "verified"
    ):
        await guardrails.log_event(
            db, session_id=session_id, message=message,
            reply_text=guardrails.REFUSAL_REPLY, analysis=analysis, claims=[], action="refused",
        )
        yield ("token", guardrails.REFUSAL_REPLY)
        yield ("done", {
            "reply_text": guardrails.REFUSAL_REPLY, "citations": citations,
            "grounded": False, "model": None, "intent_hint": "ESCALATION",
        })
        return

    messages = _build_messages(message, history, grounded, client_context, session_type=session_type)
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

    if db is not None and full_text:
        claims = guardrails.detect_claims(full_text)
        if claims and not guardrails.citation_supports_claims(claims, full_text, citations):
            await guardrails.log_event(
                db, session_id=session_id, message=message,
                reply_text=full_text, analysis=analysis,
                claims=claims, action="unchecked_claim",
            )

    # Phase 10 safety net (streaming path).
    synth_blocks, synth_intent = _maybe_synthesize_wm_block(
        full_text, session_type, auth_state, client_context, existing_blocks=[],
    )
    done_payload: Dict[str, Any] = {
        "reply_text": full_text, "citations": citations,
        "grounded": grounded, "model": model_used,
    }
    if synth_blocks:
        done_payload["fallback_blocks"] = synth_blocks
        if synth_intent:
            done_payload["intent_hint"] = synth_intent
    yield ("done", done_payload)
