"""Phase 3 orchestrator — auth-aware multi-agent flow.

Per-turn flow:
  1. Persist user message
  2. Auth pre-check:
       - Auto-clear expired lockouts
       - If locked → return locked response
       - If awaiting q1/q2 → consume message as the answer (skip Router)
       - If anonymous AND message contains a client identifier → begin verification
  3. Otherwise: Router → specialist branch
  4. Inject CLIENT_CONTEXT into LLM-using branches when session is verified
  5. Persist assistant turn (blocks + intent + citations)
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .llm import call_with_fallback, extract_reply
from . import api_agent, auth_agent, form_agent, rag_agent
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


def _maybe_inject_context(system_prompt: str, client_ctx: Optional[Dict[str, Any]]) -> str:
    if client_ctx:
        return system_prompt + auth_agent.client_context_block(client_ctx)
    return system_prompt


async def _branch_small_talk(message: str, history: List[Dict[str, Any]],
                             client_ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    msgs = [{"role": "system", "content": _maybe_inject_context(SMALL_TALK_PROMPT, client_ctx)}]
    msgs += [{"role": m["role"], "content": m["content"]} for m in history[-6:]]
    msgs.append({"role": "user", "content": message})
    result = await call_with_fallback(msgs, task="chat", temperature=0.5, max_tokens=200)
    return {
        "blocks": [{"type": "text", "text": extract_reply(result["data"])}],
        "citations": [],
        "model": result["data"].get("model") or result["model"],
    }


async def _branch_knowledge(message: str, history: List[Dict[str, Any]],
                            client_ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = await rag_agent.answer(message, history, client_context=client_ctx)
    blocks: List[Dict[str, Any]] = [{
        "type": "text",
        "text": out["reply_text"],
        "grounded": out["grounded"],
    }]
    return {"blocks": blocks, "citations": out["citations"], "model": out["model"]}


async def _branch_lead_capture(message: str, subject: Optional[str], history: List[Dict[str, Any]],
                               client_ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    rag_out = await rag_agent.answer(message, history, client_context=client_ctx)
    schema = form_agent.lead_capture_form(asset_class=subject)
    closing = "\n\nIf you'd like to take this forward, share a few details below and a senior advisor will reach out shortly."
    blocks: List[Dict[str, Any]] = [
        {"type": "text", "text": rag_out["reply_text"].strip() + closing, "grounded": rag_out["grounded"]},
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
            "Note: prices shown are illustrative and updated periodically; for live execution, please confirm with our dealing desk."
        )
        return {"blocks": [{"type": "text", "text": intro}, {"type": "market_card", "data": record}],
                "citations": [], "model": None}
    available = await api_agent.list_available_market_symbols(db, limit=6)
    text = (
        f"I couldn't locate a live quote for '{query}' in our coverage right now. "
        f"Available demo tickers include: {', '.join(available)}. "
        "Would you like a quote on one of these instead?"
    )
    return {"blocks": [{"type": "text", "text": text}], "citations": [], "model": None}


async def _branch_client_lookup(db, session_id: str, message: str, subject: Optional[str],
                                row: Dict[str, Any], client_ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Phase 3: this branch is reached only when the session is anonymous (verified
    sessions with portfolio questions are answered by KNOWLEDGE branch with context).
    Either kick off verification (if a code was provided) or ask for one."""
    # If verified, surface the client_card directly with the holdings summary.
    if client_ctx:
        return {
            "blocks": [
                {"type": "text", "text": f"Here's your account summary, {client_ctx['name'].split()[0]}."},
                {"type": "client_card", "data": {
                    "code": client_ctx["code"],
                    "name": client_ctx["name"],
                    "holdings_summary": client_ctx["holdings_summary"],
                    "verified": True,
                }},
            ],
            "citations": [], "model": None,
        }

    identifier = api_agent.extract_client_identifier(message) or (
        subject if subject and subject.upper().startswith("SMIFS") else None
    )
    if not identifier:
        text = (
            "To pull up your portfolio I'll need to verify your identity. "
            "Could you share your SMIFS client code (e.g. SMIFS001) or registered phone number?"
        )
        return {"blocks": [{"type": "text", "text": text}], "citations": [], "model": None}

    # Begin the verification flow.
    return await auth_agent.begin_verification(db, session_id, identifier)


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
    convo = await _get_or_create_session(db, session_id)
    sid = convo["session_id"]
    history = convo.get("messages", [])
    auth_row = await auth_agent.get_or_create_session_row(db, sid)
    state = auth_row.get("auth_state", "anonymous")
    trace: List[Dict[str, Any]] = []
    intent: Optional[str] = None

    # ---- 1) Auth pre-check ----
    if state == "locked":
        await _emit(emit_status, {"step": "auth", "label": "Verification temporarily locked"})
        out = await auth_agent.locked_response()
        intent = "AUTH_LOCKED"
        trace.append({"step": "auth", "auth_state": "locked"})
    elif state in {"awaiting_q1", "awaiting_q2"}:
        await _emit(emit_status, {"step": "auth", "label": "Verifying your identity"})
        out = await auth_agent.handle_answer(db, sid, auth_row, message)
        # After the answer, the row may now be verified/locked/awaiting_q2
        new_row = await db.sessions.find_one({"_id": sid}, {"_id": 0}) or {}
        intent = "AUTH_VERIFIED" if new_row.get("auth_state") == "verified" else (
            "AUTH_LOCKED" if new_row.get("auth_state") == "locked" else "AUTH_CHALLENGE"
        )
        trace.append({"step": "auth", "from": state, "to": new_row.get("auth_state")})
    else:
        # Anonymous — check for a client code in the message; begin verification immediately.
        ident = api_agent.extract_client_identifier(message)
        if ident:
            await _emit(emit_status, {"step": "auth", "label": "Looking up your record"})
            out = await auth_agent.begin_verification(db, sid, ident)
            new_row = await db.sessions.find_one({"_id": sid}, {"_id": 0}) or {}
            intent = "AUTH_CHALLENGE" if new_row.get("auth_state") == "awaiting_q1" else "AUTH_NOT_FOUND"
            trace.append({"step": "auth", "identifier": ident, "to": new_row.get("auth_state")})
        else:
            # ---- 2) Router → specialist ----
            await _emit(emit_status, {"step": "router", "label": "Routing your question"})
            routing = await classify(message, history)
            intent = routing["intent"]
            subject = routing.get("subject")
            trace.append({
                "step": "router", "intent": intent, "confidence": routing["confidence"],
                "rationale": routing["rationale"], "subject": subject,
            })
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
            client_ctx = await auth_agent.get_verified_client(db, sid)

            if intent == "KNOWLEDGE":
                out = await _branch_knowledge(message, history, client_ctx)
            elif intent == "LEAD_CAPTURE":
                out = await _branch_lead_capture(message, subject, history, client_ctx)
            elif intent == "CALLBACK_REQUEST":
                out = await _branch_callback(message, history)
            elif intent == "MARKET_DATA":
                out = await _branch_market(db, message, subject, history)
            elif intent == "CLIENT_LOOKUP":
                out = await _branch_client_lookup(db, sid, message, subject, auth_row, client_ctx)
            elif intent == "ESCALATION":
                out = await _branch_escalation(message)
            else:  # SMALL_TALK
                out = await _branch_small_talk(message, history, client_ctx)
            trace.append({"step": "specialist", "intent": intent, "status": "ok"})

    # Persist
    payload = {
        "session_id": sid,
        "trace": trace,
        "blocks": out["blocks"],
        "citations": out.get("citations", []),
        "model": out.get("model"),
        "intent": intent,
    }
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
