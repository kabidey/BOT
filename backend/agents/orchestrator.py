"""Phase 2 multi-agent orchestrator.

Run-turn flow:
  1. Persist user message
  2. Router → intent
  3. Branch to specialist; emit status events along the way
  4. Assemble blocks[] (text, form, market_card, client_card, escalation_card)
  5. Persist assistant turn (with blocks + citations + intent)
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .llm import chat_with_fallback, extract_reply
from . import api_agent, form_agent, rag_agent
from .router import classify

logger = logging.getLogger(__name__)

StatusEmitter = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]


# ---------- helpers ----------
async def _get_or_create_session(db, session_id: Optional[str]) -> Dict[str, Any]:
    if session_id:
        existing = await db.conversations.find_one({"session_id": session_id}, {"_id": 0})
        if existing:
            return existing
    new_id = session_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    doc = {"session_id": new_id, "created_at": now, "updated_at": now, "messages": []}
    await db.conversations.insert_one(dict(doc))
    return doc


async def _append_messages(db, session_id: str, new_msgs: List[Dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    stamped = [{**m, "ts": now} for m in new_msgs]
    await db.conversations.update_one(
        {"session_id": session_id},
        {"$push": {"messages": {"$each": stamped}}, "$set": {"updated_at": now}},
    )


async def _emit(emit: StatusEmitter, event: Dict[str, Any]) -> None:
    if emit is not None:
        try:
            await emit(event)
        except Exception:
            logger.exception("status emit failed")


# ---------- specialist branches ----------
SMALL_TALK_PROMPT = (
    "You are the SMIFS Wealth Advisor agent. Reply briefly and warmly to the greeting or social message. "
    "Do not pitch products. End with a soft offer to help (e.g. 'How may I assist you today?')."
)


async def _branch_small_talk(message: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    msgs = [{"role": "system", "content": SMALL_TALK_PROMPT}]
    msgs += [{"role": m["role"], "content": m["content"]} for m in history[-6:]]
    msgs.append({"role": "user", "content": message})
    result = await chat_with_fallback(msgs, temperature=0.5, max_tokens=200)
    return {
        "blocks": [{"type": "text", "text": extract_reply(result["data"])}],
        "citations": [],
        "model": result["data"].get("model") or result["model"],
    }


async def _branch_knowledge(message: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = await rag_agent.answer(message, history)
    blocks: List[Dict[str, Any]] = [{
        "type": "text",
        "text": out["reply_text"],
        "grounded": out["grounded"],
    }]
    return {
        "blocks": blocks,
        "citations": out["citations"],
        "model": out["model"],
    }


async def _branch_lead_capture(message: str, subject: Optional[str], history: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Confirmation prose from LLM grounded in RAG (so the lead-in mentions the product correctly)
    rag_out = await rag_agent.answer(message, history)
    schema = form_agent.lead_capture_form(asset_class=subject)
    intro = rag_out["reply_text"].strip()
    # Append a one-liner inviting form
    closing = (
        "\n\nIf you'd like to take this forward, share a few details below and a senior advisor will reach out shortly."
    )
    blocks: List[Dict[str, Any]] = [
        {"type": "text", "text": intro + closing, "grounded": rag_out["grounded"]},
        {"type": "form", "schema": schema},
    ]
    return {"blocks": blocks, "citations": rag_out["citations"], "model": rag_out["model"]}


async def _branch_callback(message: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    schema = form_agent.callback_form()
    blocks = [
        {"type": "text", "text": "Of course — please share a few details and we'll arrange a callback at your preferred time."},
        {"type": "form", "schema": schema},
    ]
    return {"blocks": blocks, "citations": [], "model": None}


async def _branch_market(db, message: str, subject: Optional[str], history: List[Dict[str, Any]]) -> Dict[str, Any]:
    query = api_agent.extract_market_query(message, fallback_subject=subject)
    if not query:
        text = "Could you share the specific stock symbol or fund name you'd like a quote on?"
        return {"blocks": [{"type": "text", "text": text}], "citations": [], "model": None}

    record = await api_agent.fetch_market_data(db, query)
    if record:
        intro = (
            f"Here is the latest indicative quote for {record.get('name', record.get('symbol'))}. "
            f"Note: prices shown are illustrative and updated periodically; for live execution, please confirm with our dealing desk."
        )
        return {
            "blocks": [
                {"type": "text", "text": intro},
                {"type": "market_card", "data": record},
            ],
            "citations": [],
            "model": None,
        }
    available = await api_agent.list_available_market_symbols(db, limit=6)
    text = (
        f"I couldn't locate a live quote for '{query}' in our coverage right now. "
        f"Available demo tickers include: {', '.join(available)}. "
        "Would you like a quote on one of these instead?"
    )
    return {"blocks": [{"type": "text", "text": text}], "citations": [], "model": None}


async def _branch_client_lookup(db, message: str, subject: Optional[str]) -> Dict[str, Any]:
    identifier = api_agent.extract_client_identifier(message) or (subject if subject and subject.upper().startswith("SMIFS") else None)
    if not identifier:
        text = (
            "To pull up your portfolio I'll need to verify your identity. "
            "Could you share your SMIFS client code (e.g. SMIFS001) or registered phone number?"
        )
        return {"blocks": [{"type": "text", "text": text}], "citations": [], "model": None}

    res = await api_agent.lookup_client(db, identifier)
    if not res.get("found"):
        text = (
            f"I couldn't find an SMIFS account matching '{identifier}'. "
            "If you believe this is an error, please ask to be connected with a human advisor."
        )
        return {
            "blocks": [
                {"type": "text", "text": text},
                {"type": "escalation_card", "data": {"reason": "client_not_found", "identifier": identifier}},
            ],
            "citations": [],
            "model": None,
        }
    # Phase 2: we have the record, but Phase 3 will gate full holdings behind verification.
    text = (
        "I see your record on file. For your security, I'll need to verify your identity before sharing portfolio details — "
        "we'll add this step in the next interaction. In the meantime, here's a high-level summary."
    )
    return {
        "blocks": [
            {"type": "text", "text": text},
            {"type": "client_card", "data": {
                "code": res.get("code"),
                "name": res.get("name"),
                "holdings_summary": res.get("holdings_summary"),
                "verified": False,
            }},
        ],
        "citations": [],
        "model": None,
    }


async def _branch_escalation(message: str) -> Dict[str, Any]:
    blocks = [
        {"type": "text", "text": (
            "This is a question best handled by a senior advisor in person. "
            "Let me connect you with the SMIFS engagement team — they'll reach out within one business day."
        )},
        {"type": "escalation_card", "data": {"reason": "advisor_required"}},
    ]
    return {"blocks": blocks, "citations": [], "model": None}


# ---------- main orchestrator ----------
async def run_turn(db, session_id: Optional[str], message: str,
                   emit_status: StatusEmitter = None) -> Dict[str, Any]:
    session = await _get_or_create_session(db, session_id)
    sid = session["session_id"]
    history = session.get("messages", [])

    await _emit(emit_status, {"step": "router", "label": "Routing your question"})
    routing = await classify(message, history)
    intent = routing["intent"]
    subject = routing.get("subject")
    trace: List[Dict[str, Any]] = [{"step": "router", "intent": intent, "confidence": routing["confidence"], "rationale": routing["rationale"], "subject": subject}]

    label_for = {
        "KNOWLEDGE": "Consulting the Research Assistant",
        "MARKET_DATA": "Pulling market data",
        "CLIENT_LOOKUP": "Looking up your record",
        "LEAD_CAPTURE": "Preparing your form",
        "CALLBACK_REQUEST": "Preparing callback details",
        "ESCALATION": "Connecting a human advisor",
        "SMALL_TALK": "Drafting a reply",
    }
    await _emit(emit_status, {"step": "specialist", "intent": intent, "label": label_for.get(intent, "Working")})

    if intent == "KNOWLEDGE":
        out = await _branch_knowledge(message, history)
    elif intent == "LEAD_CAPTURE":
        out = await _branch_lead_capture(message, subject, history)
    elif intent == "CALLBACK_REQUEST":
        out = await _branch_callback(message, history)
    elif intent == "MARKET_DATA":
        out = await _branch_market(db, message, subject, history)
    elif intent == "CLIENT_LOOKUP":
        out = await _branch_client_lookup(db, message, subject)
    elif intent == "ESCALATION":
        out = await _branch_escalation(message)
    else:  # SMALL_TALK
        out = await _branch_small_talk(message, history)

    trace.append({"step": "specialist", "intent": intent, "status": "ok"})

    payload = {
        "session_id": sid,
        "trace": trace,
        "blocks": out["blocks"],
        "citations": out.get("citations", []),
        "model": out.get("model"),
        "intent": intent,
    }

    # Persist
    await _append_messages(db, sid, [
        {"role": "user", "content": message},
        {
            "role": "assistant",
            "content": _flatten_text(out["blocks"]),
            "blocks": out["blocks"],
            "citations": out.get("citations", []),
            "intent": intent,
            "model": out.get("model"),
        },
    ])

    return payload


def _flatten_text(blocks: List[Dict[str, Any]]) -> str:
    """Plain-text rendering of a block list — used for legacy /api/chat reply field."""
    parts: List[str] = []
    for b in blocks:
        if b.get("type") == "text":
            parts.append(b.get("text", ""))
        elif b.get("type") == "form":
            schema = b.get("schema", {})
            parts.append(f"[Form: {schema.get('title', schema.get('form_type', 'form'))}]")
        elif b.get("type") == "market_card":
            d = b.get("data", {})
            parts.append(f"[Quote: {d.get('symbol')} ₹{d.get('last_price')} ({d.get('change_pct')}%)]")
        elif b.get("type") == "client_card":
            d = b.get("data", {})
            parts.append(f"[Client: {d.get('name')} ({d.get('code')})]")
        elif b.get("type") == "escalation_card":
            parts.append("[Connect with advisor]")
    return "\n\n".join(parts).strip()
