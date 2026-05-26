"""Phase 6 orchestrator — visitor / employee / client flow with real OrgLens auth.

Per-turn flow:
  1. Persist user message (with PAN scrubbed)
  2. Auth pre-check (state machine in agents.auth_agent):
       - locked            → locked response
       - awaiting_role     → consume reply as role
       - awaiting_identifier → consume as email/UCC
       - awaiting_pan      → consume as PAN
       - anonymous + role-trigger in message → kick off employee/client flow
       - otherwise         → router → specialist
  3. Inject role-specific identity context for verified sessions
  4. Persist assistant turn
"""
from __future__ import annotations
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import identity as id_mod
import resilience
from .llm import call_with_fallback, extract_reply, stream_chat_with_fallback
from . import api_agent, auth_agent, form_agent, rag_agent
from .router import classify

logger = logging.getLogger(__name__)

StatusEmitter = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]
TokenEmitter = Optional[Callable[[str], Awaitable[None]]]
CitationsEmitter = Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]]


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
    """All persisted user text is run through redact_pii_in_text — plaintext
    PANs, emails and phone numbers are masked before they ever land on disk.
    Assistant text is PAN-scrubbed only; assistant replies never contain the
    user's raw identifiers by construction."""
    now = datetime.now(timezone.utc).isoformat()
    now_dt = datetime.now(timezone.utc)
    stamped: List[Dict[str, Any]] = []
    for m in new_msgs:
        cleaned = dict(m)
        if "content" in cleaned and isinstance(cleaned["content"], str):
            if cleaned.get("role") == "user":
                cleaned["content"] = id_mod.redact_pii_in_text(cleaned["content"])
            else:
                cleaned["content"] = id_mod.redact_pan_in_text(cleaned["content"])
        if "blocks" in cleaned and isinstance(cleaned["blocks"], list):
            cleaned["blocks"] = [_redact_block(b) for b in cleaned["blocks"]]
        cleaned["ts"] = now
        stamped.append(cleaned)
    await db.conversations.update_one(
        {"session_id": session_id},
        {"$push": {"messages": {"$each": stamped}}, "$set": {"updated_at": now}},
    )
    # Phase 7 — bump session activity timestamp on every turn so idle expiry
    # is measured from the last chat, not the last auth state transition.
    await db.sessions.update_one(
        {"_id": session_id},
        {"$set": {"updated_at": now, "updated_at_dt": now_dt, "lifecycle": "active"}},
    )


def _redact_block(b: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(b, dict):
        return b
    if b.get("type") == "text" and isinstance(b.get("text"), str):
        b = {**b, "text": id_mod.redact_pan_in_text(b["text"])}
    return b


async def _emit(emit: StatusEmitter, event: Dict[str, Any]) -> None:
    if emit is not None:
        try:
            await emit(event)
        except Exception:
            logger.exception("status emit failed")


# ---------- Phase 13 short-circuit persistence helpers ----------
def _append_too_long_notice(out: Dict[str, Any]) -> None:
    """Append the 'we trimmed your message' notice to the first text block."""
    blocks = out.get("blocks") or []
    for b in blocks:
        if b.get("type") == "text":
            b["text"] = (b.get("text") or "") + resilience.too_long_notice()
            return
    blocks.insert(0, {"type": "text", "text": resilience.too_long_notice().lstrip()})
    out["blocks"] = blocks


async def _persist_turn(db, sid: str, user_message: str,
                        out: Dict[str, Any], intent: Optional[str]) -> None:
    """Append a (user, assistant) pair to conversations. Used by the resilience
    short-circuits so their replies show up in /api/sessions history."""
    await _append_messages(db, sid, [
        {"role": "user", "content": user_message or ""},
        {
            "role": "assistant",
            "content": _flatten_text(out.get("blocks") or []),
            "blocks": out.get("blocks") or [],
            "citations": out.get("citations") or [],
            "intent": intent,
            "model": out.get("model"),
        },
    ])


def _final_payload(sid: str, out: Dict[str, Any], trace: List[Dict[str, Any]],
                   intent: Optional[str],
                   prior_session_id: Optional[str],
                   expiry_resume_offer: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Wrap an out dict in the standard TurnResponse shape."""
    payload: Dict[str, Any] = {
        "session_id": sid,
        "trace": trace,
        "blocks": out.get("blocks") or [],
        "citations": out.get("citations") or [],
        "model": out.get("model"),
        "intent": intent,
    }
    if prior_session_id:
        payload["prior_session_id"] = prior_session_id
    if expiry_resume_offer:
        payload["resume_offer"] = expiry_resume_offer
    return payload


# ---------- specialist branches ----------
SMALL_TALK_PROMPT = (
    "You are the Mackertich ONE Advisor — the wealth-engagement agent for Mackertich ONE, "
    "the wealth-management vertical of SMIFS Ltd. "
    "Reply briefly and warmly to the greeting or social message. "
    "Do not pitch products. End with a soft offer to help (e.g. 'How may I assist you today?')."
)


# Phase 18 — Workstream B (multilingual). Three-locale v1 (en / hi / ta).
# Locale instruction is appended (non-negotiable wording — adherence is
# load-bearing) to the system prompt for every branch when the session
# carries a non-English locale.
SUPPORTED_LOCALES = {"en", "hi", "ta"}
_LOCALE_INSTRUCTION = {
    "hi": (
        "\n\nRespond entirely in Hindi. Use Devanagari script for Hindi. "
        "Keep technical terms (PAN, UCC, NAV, AUM, ARN, SIP, NCD) in English "
        "where they are proper nouns."
    ),
    "ta": (
        "\n\nRespond entirely in Tamil. Use Tamil script for Tamil. "
        "Keep technical terms (PAN, UCC, NAV, AUM, ARN, SIP, NCD) in English "
        "where they are proper nouns."
    ),
}


def _maybe_inject_context(system_prompt: str, identity_obj: Optional[Dict[str, Any]],
                          locale: Optional[str] = None) -> str:
    block = auth_agent.context_block_for(identity_obj)
    out = system_prompt + block if block else system_prompt
    # Phase 18 — locale instruction. The locale travels separately from
    # identity (visitors have no identity but may still want Hindi/Tamil),
    # so honour the explicit `locale` arg first, then fall back to anything
    # the identity blob carries. English (default) leaves the prompt untouched.
    loc = (locale or (identity_obj or {}).get("locale") or "en").lower()
    if loc in _LOCALE_INSTRUCTION:
        out = out + _LOCALE_INSTRUCTION[loc]
    return out


def locale_instruction(locale: Optional[str]) -> str:
    """Public hook so the RAG agent (which builds its own system prompt)
    can append the same locale instruction string. Returns "" for English."""
    loc = (locale or "en").lower()
    return _LOCALE_INSTRUCTION.get(loc, "")


async def _branch_small_talk(message: str, history: List[Dict[str, Any]],
                             identity_obj: Optional[Dict[str, Any]],
                             emit_token: TokenEmitter = None,
                             locale: Optional[str] = None) -> Dict[str, Any]:
    msgs = [{"role": "system", "content": _maybe_inject_context(SMALL_TALK_PROMPT, identity_obj, locale=locale)}]
    msgs += [{"role": m["role"], "content": m["content"]} for m in history[-6:]]
    msgs.append({"role": "user", "content": message})
    if emit_token is not None:
        full_text = ""
        model: Optional[str] = None
        try:
            async for ev, data in stream_chat_with_fallback(
                msgs, temperature=0.5, max_tokens=200, intent="SMALL_TALK",
            ):
                if ev == "token":
                    full_text += data
                    await emit_token(data)
                elif ev == "done":
                    full_text = data.get("reply_text", full_text) or full_text
                    model = data.get("model")
            return {"blocks": [{"type": "text", "text": full_text}], "citations": [], "model": model}
        except Exception as e:
            logger.warning("Small-talk stream failed (%s); falling back to non-streaming.", e)
    result = await call_with_fallback(msgs, task="chat", temperature=0.5, max_tokens=200, intent="SMALL_TALK")
    text = extract_reply(result["data"])
    if emit_token is not None:
        await emit_token(text)
    return {
        "blocks": [{"type": "text", "text": text}],
        "citations": [],
        "model": result["data"].get("model") or result["model"],
    }


async def _branch_knowledge(message: str, history: List[Dict[str, Any]],
                            identity_obj: Optional[Dict[str, Any]],
                            session_id: Optional[str] = None,
                            emit_token: TokenEmitter = None,
                            emit_citations: CitationsEmitter = None,
                            db=None,
                            session_type: Optional[str] = None,
                            auth_state: Optional[str] = None,
                            locale: Optional[str] = None) -> Dict[str, Any]:
    if emit_token is not None:
        full_text = ""
        citations: List[Dict[str, Any]] = []
        grounded = False
        model: Optional[str] = None
        intent_hint: Optional[str] = None
        fallback_blocks: List[Dict[str, Any]] = []
        async for ev, data in rag_agent.stream_answer(
            message, history, client_context=identity_obj, session_id=session_id,
            session_type=session_type, auth_state=auth_state, db=db, locale=locale,
        ):
            if ev == "citations":
                citations = data
                if emit_citations is not None:
                    await emit_citations(citations)
            elif ev == "token":
                full_text += data
                await emit_token(data)
            elif ev == "done":
                full_text = data.get("reply_text", full_text) or full_text
                citations = data.get("citations", citations)
                grounded = bool(data.get("grounded"))
                model = data.get("model")
                intent_hint = data.get("intent_hint")
                fallback_blocks = data.get("fallback_blocks") or []
        blocks: List[Dict[str, Any]] = [{"type": "text", "text": full_text, "grounded": grounded}]
        blocks.extend(fallback_blocks)
        result: Dict[str, Any] = {"blocks": blocks, "citations": citations, "model": model}
        if intent_hint:
            result["intent_hint"] = intent_hint
        return result
    out = await rag_agent.answer(
        message, history, client_context=identity_obj, session_id=session_id,
        session_type=session_type, auth_state=auth_state, db=db, locale=locale,
    )
    blocks = [{"type": "text", "text": out["reply_text"], "grounded": out["grounded"]}]
    if out.get("fallback_blocks"):
        blocks.extend(out["fallback_blocks"])
    result = {"blocks": blocks, "citations": out["citations"], "model": out["model"]}
    if out.get("intent_hint"):
        result["intent_hint"] = out["intent_hint"]
    return result


async def _branch_lead_capture(message: str, subject: Optional[str], history: List[Dict[str, Any]],
                               identity_obj: Optional[Dict[str, Any]],
                               session_id: Optional[str] = None,
                               emit_token: TokenEmitter = None,
                               emit_citations: CitationsEmitter = None) -> Dict[str, Any]:
    closing = "\n\nIf you'd like to take this forward, share a few details below and a Mackertich ONE senior advisor will reach out shortly."
    schema = form_agent.lead_capture_form(asset_class=subject)
    if emit_token is not None:
        full_text = ""
        citations: List[Dict[str, Any]] = []
        grounded = False
        model: Optional[str] = None
        async for ev, data in rag_agent.stream_answer(
            message, history, client_context=identity_obj, session_id=session_id,
        ):
            if ev == "citations":
                citations = data
                if emit_citations is not None:
                    await emit_citations(citations)
            elif ev == "token":
                full_text += data
                await emit_token(data)
            elif ev == "done":
                full_text = data.get("reply_text", full_text) or full_text
                citations = data.get("citations", citations)
                grounded = bool(data.get("grounded"))
                model = data.get("model")
        if emit_token is not None:
            await emit_token(closing)
        text = full_text.strip() + closing
        return {
            "blocks": [
                {"type": "text", "text": text, "grounded": grounded},
                {"type": "form", "schema": schema},
            ],
            "citations": citations, "model": model,
        }
    rag_out = await rag_agent.answer(message, history, client_context=identity_obj, session_id=session_id)
    return {
        "blocks": [
            {"type": "text", "text": rag_out["reply_text"].strip() + closing, "grounded": rag_out["grounded"]},
            {"type": "form", "schema": schema},
        ],
        "citations": rag_out["citations"], "model": rag_out["model"],
    }


async def _branch_callback(message: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    schema = form_agent.callback_form()
    blocks = [
        {"type": "text", "text": "Of course — please share a few details and a Mackertich ONE senior advisor will arrange a callback at your preferred time."},
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
                                row: Dict[str, Any], identity_obj: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Reached only when the router intent is CLIENT_LOOKUP. Either:
      * Verified session → reissue the verified card
      * Anonymous + identifier in message → kick off the right flow
      * Anonymous, no identifier → start role inquiry
    """
    if identity_obj:
        if identity_obj.get("type") == "employee":
            return {
                "blocks": [
                    {"type": "text", "text": f"Here's your record on file, {identity_obj.get('first_name') or 'there'}."},
                    {"type": "employee_card", "data": {**identity_obj, "verified": True}},
                ],
                "citations": [], "model": None,
            }
        return {
            "blocks": [
                {"type": "text", "text": f"Here's your account summary, {identity_obj.get('first_name') or 'there'}."},
                {"type": "client_card", "data": {**identity_obj, "verified": True}},
            ],
            "citations": [], "model": None,
        }
    # Fast-path: identifier already in the message
    smifs_email = id_mod.extract_smifs_email(message)
    if smifs_email:
        return await auth_agent.start_employee_flow(db, session_id, smifs_email)
    ucc = id_mod.extract_ucc(message, require_client_context=True)
    if ucc:
        return await auth_agent.start_client_flow(db, session_id, ucc)
    return await auth_agent.start_role_inquiry(db, session_id)


async def _branch_escalation(message: str) -> Dict[str, Any]:
    blocks = [
        {"type": "text", "text": (
            "This is a question best handled by a senior advisor in person. "
            "Let me connect you to a Mackertich ONE senior advisor — they'll reach out within one business day."
        )},
        {"type": "escalation_card", "data": {"reason": "advisor_required"}},
    ]
    return {"blocks": blocks, "citations": [], "model": None}


async def _branch_directory(session_id: str, tool_name: Optional[str],
                            tool_args: Dict[str, Any],
                            identity_obj: Optional[Dict[str, Any]],
                            session_context: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 8 — dispatch a directory_* tool. Non-employees get a polite decline."""
    # Guardrail: directory access is STAFF ONLY.
    if (session_context.get("session_type") != "employee"
            or session_context.get("auth_state") != "verified"
            or not identity_obj or identity_obj.get("type") != "employee"):
        return {
            "blocks": [{"type": "text", "text": (
                "Directory access is for SMIFS staff only. I can still help you with product "
                "knowledge, market data, or connect you with a relationship manager."
            )}],
            "citations": [], "model": None,
        }
    if not tool_name:
        return {
            "blocks": [{"type": "text", "text": "Could you rephrase that? I wasn't sure which directory lookup you meant."}],
            "citations": [], "model": None,
        }
    from . import directory_agent as _da
    if tool_name not in _da.DIRECTORY_TOOL_NAMES:
        return {
            "blocks": [{"type": "text", "text": f"Unsupported directory tool: {tool_name}."}],
            "citations": [], "model": None,
        }
    return await _da.execute(tool_name, tool_args, session_id, identity_obj)


async def _branch_client_query(session_id: str, tool_name: Optional[str],
                               tool_args: Dict[str, Any],
                               identity_obj: Optional[Dict[str, Any]],
                               session_context: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 12 — dispatch a client_* tool. Strictly gated to verified clients."""
    if (session_context.get("session_type") != "client"
            or session_context.get("auth_state") != "verified"
            or not identity_obj or identity_obj.get("type") != "client"):
        return {
            "blocks": [{"type": "text", "text": (
                "Live account data is available once you're verified as a Mackertich ONE client. "
                "Share your UCC and PAN to unlock it."
            )}],
            "citations": [], "model": None,
        }
    if not tool_name:
        return {
            "blocks": [{"type": "text", "text": "Could you rephrase that? I wasn't sure which account view you needed."}],
            "citations": [], "model": None,
        }
    from . import client_agent as _ca
    if tool_name not in _ca.CLIENT_TOOL_NAMES:
        return {
            "blocks": [{"type": "text", "text": f"Unsupported client tool: {tool_name}."}],
            "citations": [], "model": None,
        }
    return await _ca.execute(tool_name, tool_args, session_id, identity_obj)


# ---------- main orchestrator ----------
async def run_turn(db, session_id: Optional[str], message: str,
                   emit_status: StatusEmitter = None,
                   emit_token: TokenEmitter = None,
                   emit_citations: CitationsEmitter = None) -> Dict[str, Any]:
    # Phase 13 — input normalisation (truncate / detect edge inputs).
    raw_message = message
    cleaned, edge_kind = resilience.normalise_input(message)
    message = cleaned or ""
    too_long_notice_appended = False
    if edge_kind == "too_long":
        # Continue with the truncated message but tag the reply with a notice.
        too_long_notice_appended = True
        edge_kind = None

    # Phase 7 — idle expiry & rehydration offer
    import lifecycle as _lc
    expiry = await _lc.maybe_expire_and_mint(db, session_id)
    effective_sid = expiry["session_id"]
    prior_session_id = expiry.get("prior_session_id")
    expiry_resume_offer = expiry.get("resume_offer")

    convo = await _get_or_create_session(db, effective_sid)
    sid = convo["session_id"]
    history = convo.get("messages", [])
    auth_row = await auth_agent.get_or_create_session_row(db, sid)
    state = auth_row.get("auth_state", auth_agent.ANON)
    trace: List[Dict[str, Any]] = []
    intent: Optional[str] = None

    # ---- Phase 13: edge-input early reply (empty / single-char / emoji-only) ----
    if edge_kind in ("empty", "whitespace"):
        out = resilience.empty_input_reply()
        intent = out.get("intent_hint") or "SMALL_TALK"
        trace.append({"step": "resilience", "kind": "empty_input"})
        await _persist_turn(db, sid, raw_message, out, intent)
        return _final_payload(sid, out, trace, intent,
                              prior_session_id, expiry_resume_offer)
    if edge_kind == "single_char":
        out = resilience.single_char_reply()
        intent = out.get("intent_hint") or "SMALL_TALK"
        trace.append({"step": "resilience", "kind": "single_char"})
        await _persist_turn(db, sid, raw_message, out, intent)
        return _final_payload(sid, out, trace, intent,
                              prior_session_id, expiry_resume_offer)
    if edge_kind == "emoji_only":
        out = resilience.emoji_only_reply()
        intent = out.get("intent_hint") or "SMALL_TALK"
        trace.append({"step": "resilience", "kind": "emoji_only"})
        await _persist_turn(db, sid, raw_message, out, intent)
        return _final_payload(sid, out, trace, intent,
                              prior_session_id, expiry_resume_offer)

    # ---- Phase 13: adversarial-input short-circuit ----
    # Active on every state except LOCKED. Even mid-auth-challenge, an
    # injection / profanity / off-topic curveball deserves a graceful steer
    # back to wealth-management — it does NOT consume any auth slot.
    if state != auth_agent.LOCKED:
        identity_obj_for_sc = auth_row.get("identity") if state == auth_agent.VERIFIED else None
        sc = resilience.short_circuit(
            message, history,
            identity_obj=identity_obj_for_sc,
            session_type=auth_row.get("session_type", "visitor"),
            auth_state=auth_row.get("auth_state"),
        )
        if sc is not None:
            out, ctx = sc
            if ctx.get("security_event"):
                await resilience.log_security_event(
                    db, kind=ctx["kind"], session_id=sid,
                    role_state_value=resilience.role_state(
                        auth_row.get("session_type"), auth_row.get("auth_state"),
                        identity_obj_for_sc),
                    user_message=raw_message, action=ctx.get("action", "deflected"),
                )
            intent = out.get("intent_hint") or "OUT_OF_SCOPE"
            trace.append({"step": "resilience", "kind": ctx.get("kind"),
                          "action": ctx.get("action")})
            if too_long_notice_appended:
                _append_too_long_notice(out)
            await _persist_turn(db, sid, raw_message, out, intent)
            return _final_payload(sid, out, trace, intent,
                                  prior_session_id, expiry_resume_offer)

    # ---- Phase 13: repeated-turn loop guard (only for ANON visitors, not in
    # auth challenges where the user might genuinely re-send the same PAN).
    if state == auth_agent.ANON and history:
        last_user = next((m.get("content") for m in reversed(history)
                          if m.get("role") == "user"), None)
        if resilience.is_repeated(message, last_user):
            out = resilience.repeated_reply()
            intent = out.get("intent_hint") or "ESCALATION"
            trace.append({"step": "resilience", "kind": "repeated"})
            await _persist_turn(db, sid, raw_message, out, intent)
            return _final_payload(sid, out, trace, intent,
                                  prior_session_id, expiry_resume_offer)

    # ---- Phase 13: self-healing for identifier inputs ----
    # During auth challenges we apply UCC/PAN/email healing so users with a
    # typo (O→0, PAN spacing, gnail.com) aren't stuck in retry hell.
    if state in (auth_agent.AWAIT_IDENT, auth_agent.AWAIT_PAN, auth_agent.ANON):
        healed_message, applied = resilience.self_heal_message(message)
        if applied:
            trace.append({"step": "resilience", "kind": "self_heal",
                          "applied": applied})
            message = healed_message

    # ---- 1) Auth pre-check ----
    if state == auth_agent.LOCKED:
        await _emit(emit_status, {"step": "auth", "label": "Verification temporarily locked"})
        out = await auth_agent.locked_response()
        intent = "AUTH_LOCKED"
        trace.append({"step": "auth", "auth_state": "locked"})
    elif state == auth_agent.AWAIT_PAN:
        await _emit(emit_status, {"step": "auth", "label": "Verifying your identity"})
        out = await auth_agent.handle_pan_response(db, sid, message)
        new_row = await db.sessions.find_one({"_id": sid}, {"_id": 0}) or {}
        if new_row.get("auth_state") == auth_agent.VERIFIED:
            intent = "AUTH_VERIFIED"
        elif new_row.get("auth_state") == auth_agent.LOCKED:
            intent = "AUTH_LOCKED"
        else:
            intent = "AUTH_PAN_RETRY"
        trace.append({"step": "auth", "from": "awaiting_pan", "to": new_row.get("auth_state")})
    elif state == auth_agent.AWAIT_IDENT:
        await _emit(emit_status, {"step": "auth", "label": "Looking up your record"})
        out = await auth_agent.handle_identifier_response(db, sid, message)
        new_row = await db.sessions.find_one({"_id": sid}, {"_id": 0}) or {}
        ns = new_row.get("auth_state")
        intent = ("AUTH_PAN_REQUEST" if ns == auth_agent.AWAIT_PAN
                  else "AUTH_NOT_FOUND" if ns == auth_agent.ANON
                  else "AUTH_CHALLENGE")
        trace.append({"step": "auth", "from": "awaiting_identifier", "to": ns})
    elif state == auth_agent.AWAIT_ROLE:
        # Phase 20 — visitor escape hatch. If the user isn't picking a role
        # but is asking a firm-wide aggregate question (e.g. "how many active
        # clients does SMIFS have?"), don't blackhole them into the role
        # challenge — try the visitor tool surface first. We keep the role
        # gate intact: only visitor-allowed tools (firm_stats,
        # client_corpus_stats, departments_list, locations_list,
        # designations_list) are visible to this caller. If Phase 20 returns
        # ok=false or empty, fall through to the original role challenge.
        msg_l = (message or "").strip().lower()
        # Quick keyword pre-filter — a real role-pick is short and contains
        # one of the role words; anything longer is almost certainly a
        # question the visitor wants answered.
        is_role_pick = (
            len(msg_l) < 25 and (
                msg_l.startswith("employee") or msg_l.startswith("emp ")
                or msg_l.startswith("client") or msg_l.startswith("visitor")
                or "i am a client" in msg_l or "i am an employee" in msg_l
                or "i'm a client" in msg_l or "i'm an employee" in msg_l
                or "ucc" in msg_l[:8] or id_mod.extract_smifs_email(message)
            )
        )
        p20_visitor_handled = False
        if (not is_role_pick
                and os.environ.get("PHASE_20_TOOLS_ENABLED", "false").lower() == "true"):
            try:
                from orglens_tools import orchestrator as _p20, registry as _p20reg
                # Only worth attempting if SOME visitor-eligible tool exists.
                if _p20reg.visible_to("visitor"):
                    p20 = await _p20.run(db, sid, message,
                                          session={"session_type": "visitor",
                                                   "auth_state": auth_agent.ANON,
                                                   "identity": None},
                                          identity_obj=None,
                                          session_context={"session_type": "visitor",
                                                            "auth_state": auth_agent.ANON,
                                                            "locale": (auth_row.get("locale") or "en")})
                    blocks_have_content = (
                        bool(p20.get("ok"))
                        and any(b.get("type") in ("text", "table", "chart", "image",
                                                    "employee_card", "client_card")
                                for b in (p20.get("blocks") or []))
                    )
                    if blocks_have_content:
                        out = {"blocks": p20["blocks"], "model": p20.get("model"),
                                "intent_hint": "TOOLS_PIPELINE_VISITOR"}
                        intent = "TOOLS_PIPELINE_VISITOR"
                        trace.append({"step": "phase20_visitor_bypass", "ok": True,
                                       "classification": p20.get("classification"),
                                       "tool_trace": p20.get("trace")})
                        p20_visitor_handled = True
            except Exception:
                logger.exception("Phase 20 visitor bypass failed (non-fatal)")
        if not p20_visitor_handled:
            await _emit(emit_status, {"step": "auth", "label": "Identifying your role"})
            out = await auth_agent.handle_role_response(db, sid, message)
            new_row = await db.sessions.find_one({"_id": sid}, {"_id": 0}) or {}
            ns = new_row.get("auth_state")
            intent = ("AUTH_PAN_REQUEST" if ns == auth_agent.AWAIT_PAN
                      else "AUTH_CHALLENGE" if ns in (auth_agent.AWAIT_IDENT, auth_agent.AWAIT_ROLE)
                      else "AUTH_NOT_FOUND" if ns == auth_agent.ANON
                      else "AUTH_VERIFIED")
            trace.append({"step": "auth", "from": "awaiting_role", "to": ns})
    else:
        # ---- Anonymous OR Verified. ----
        # Role-trigger detection ONLY for anonymous users — a VERIFIED user
        # saying "what's my employee id?" must NOT restart the role flow.
        smifs_email = id_mod.extract_smifs_email(message) if state == auth_agent.ANON else None
        role_intent = id_mod.detect_role_intent(message) if state == auth_agent.ANON else None
        if smifs_email:
            await _emit(emit_status, {"step": "auth", "label": "Looking up your employee record"})
            out = await auth_agent.start_employee_flow(db, sid, smifs_email)
            new_row = await db.sessions.find_one({"_id": sid}, {"_id": 0}) or {}
            ns = new_row.get("auth_state")
            intent = "AUTH_PAN_REQUEST" if ns == auth_agent.AWAIT_PAN else "AUTH_NOT_FOUND"
            trace.append({"step": "auth", "trigger": "smifs_email", "to": ns})
        elif role_intent == "client":
            ucc = id_mod.extract_ucc(message, require_client_context=True)
            await _emit(emit_status, {"step": "auth", "label": "Looking up your client record"})
            out = await auth_agent.start_client_flow(db, sid, ucc)
            new_row = await db.sessions.find_one({"_id": sid}, {"_id": 0}) or {}
            ns = new_row.get("auth_state")
            intent = ("AUTH_PAN_REQUEST" if ns == auth_agent.AWAIT_PAN
                      else "AUTH_CHALLENGE" if ns == auth_agent.AWAIT_IDENT
                      else "AUTH_NOT_FOUND")
            trace.append({"step": "auth", "trigger": "client_hint", "to": ns})
        elif role_intent == "employee":
            out = await auth_agent.start_employee_flow(db, sid, None)
            intent = "AUTH_CHALLENGE"
            trace.append({"step": "auth", "trigger": "employee_hint", "to": "awaiting_identifier"})
        elif role_intent == "ambiguous_verify":
            out = await auth_agent.start_role_inquiry(db, sid)
            intent = "AUTH_CHALLENGE"
            trace.append({"step": "auth", "trigger": "verify_hint", "to": "awaiting_role"})
        else:
            # ---- 2) Router → specialist ----
            await _emit(emit_status, {"step": "router", "label": "Routing your question"})
            auth_row = await db.sessions.find_one({"_id": sid}, {"_id": 0}) or {}
            session_context = {
                "session_type": auth_row.get("session_type", "visitor"),
                "auth_state": auth_row.get("auth_state"),
                "locale": (auth_row.get("locale") or "en"),
            }
            routing = await classify(message, history, session_context=session_context)
            intent = routing["intent"]
            subject = routing.get("subject")
            trace.append({
                "step": "router", "intent": intent, "confidence": routing["confidence"],
                "rationale": routing["rationale"], "subject": subject,
                "tool_name": routing.get("tool_name"),
            })
            label_for = {
                "KNOWLEDGE": "Consulting the Research Assistant",
                "MARKET_DATA": "Pulling market data",
                "BMIA_COMPLIANCE": "Searching SEBI / RBI / MCA corpus",
                "BMIA_FUNDAMENTALS": "Fetching NSE fundamentals",
                "BMIA_BRIEFING": "Fetching today's market briefing",
                "CLIENT_LOOKUP": "Looking up your record",
                "LEAD_CAPTURE": "Preparing your form",
                "CALLBACK_REQUEST": "Preparing callback details",
                "ESCALATION": "Connecting a human advisor",
                "SMALL_TALK": "Drafting a reply",
                "DIRECTORY_QUERY": "Querying the SMIFS directory",
                "CLIENT_QUERY": "Reading your account from the back-office",
            }
            await _emit(emit_status, {"step": "specialist", "intent": intent, "label": label_for.get(intent, "Working")})
            identity_obj = await auth_agent.get_verified_identity(db, sid)

            # ---- Phase 20 — feature-flagged tool-aware pipeline ----
            # When PHASE_20_TOOLS_ENABLED=true AND the router landed on a
            # data-shaped intent (CLIENT_*, DIRECTORY_*), try the dynamic
            # tool registry first. Falls through to the legacy branches when
            # the new pipeline can't handle the question or returns ok=False.
            if (os.environ.get("PHASE_20_TOOLS_ENABLED", "false").lower() == "true"
                    and intent in ("CLIENT_LOOKUP", "CLIENT_QUERY", "DIRECTORY_QUERY",
                                    "KNOWLEDGE", "SMALL_TALK")):
                try:
                    from orglens_tools import orchestrator as _p20
                    p20 = await _p20.run(db, sid, message,
                                          session=auth_row,
                                          identity_obj=identity_obj,
                                          session_context=session_context)
                    if p20.get("ok"):
                        # ---- KNOWLEDGE fallback to legacy RAG ----
                        # Phase 20 has no tool for vehicle/NCD/MF prospectus
                        # questions — those live in the RAG corpus. If we're
                        # on the KNOWLEDGE intent AND the new pipeline produced
                        # a text-only refusal (no structured tool data),
                        # fall through so `_branch_knowledge` can answer with
                        # citations + a vehicle_cta. This is the ESCAPE HATCH
                        # for misclassified queries — most KNOWLEDGE turns
                        # still get the new pipeline. See matrix_run_v3.md.
                        p20_blocks = p20.get("blocks") or []
                        p20_block_types = [b.get("type") for b in p20_blocks
                                            if isinstance(b, dict)]
                        p20_text = " ".join(
                            (b.get("text") or "") for b in p20_blocks
                            if isinstance(b, dict) and b.get("type") == "text"
                        ).lower()
                        # Did any tool actually get called?
                        tool_rounds = sum(
                            1 for tt in (p20.get("trace") or [])
                            if isinstance(tt, dict)
                            and tt.get("step") == "llm_round"
                            and tt.get("tool_calls")
                        )
                        refusal_markers = ("don't have", "do not have",
                                           "no tool", "outside my scope",
                                           "unable to retrieve", "i can't help",
                                           "cannot help", "couldn't find",
                                           "could not find", "no data",
                                           "not available", "no information")
                        looks_like_refusal = (
                            intent == "KNOWLEDGE"
                            and set(p20_block_types) <= {"text"}
                            and (tool_rounds == 0
                                 or any(m in p20_text for m in refusal_markers))
                        )
                        if looks_like_refusal:
                            trace.append({"step": "phase20_fallback_to_rag",
                                           "reason": ("no_tools_called"
                                                       if tool_rounds == 0
                                                       else "refusal_markers"),
                                           "tool_rounds": tool_rounds,
                                           "classification": p20.get("classification"),
                                           "tool_trace": p20.get("trace")})
                            # Telemetry — log to tool_calls so we can audit
                            # how often the fallback fires in prod.
                            try:
                                await db.tool_calls.insert_one({
                                    "session_id": sid,
                                    "turn_id": None,
                                    "tool_name": "phase20_fallback_to_rag",
                                    "ok": True,
                                    "hit_cache": False,
                                    "latency_ms": 0,
                                    "error_kind": None,
                                    "role": session_context.get("session_type") or "anon",
                                    "params_redacted": {
                                        "intent": intent,
                                        "reason": ("no_tools_called"
                                                    if tool_rounds == 0
                                                    else "refusal_markers"),
                                    },
                                    "created_at": datetime.now(timezone.utc).isoformat(),
                                })
                            except Exception:
                                logger.debug("phase20_fallback_to_rag telemetry write failed", exc_info=True)
                            out = None  # fall through to _branch_knowledge
                        else:
                            out = {"blocks": p20_blocks,
                                    "model": p20.get("model"),
                                    "intent_hint": "TOOLS_PIPELINE"}
                            trace.append({"step": "phase20", "ok": True,
                                           "classification": p20.get("classification"),
                                           "tool_trace": p20.get("trace")})
                            intent = "TOOLS_PIPELINE"
                    else:
                        trace.append({"step": "phase20", "ok": False,
                                       "reason": p20.get("reason"),
                                       "classification": p20.get("classification")})
                        out = None  # fall through to legacy branches
                except Exception:
                    logger.exception("Phase 20 pipeline failed; falling through")
                    out = None
            else:
                out = None

            if out is None and intent == "KNOWLEDGE":
                out = await _branch_knowledge(
                    message, history, identity_obj, session_id=sid,
                    emit_token=emit_token, emit_citations=emit_citations, db=db,
                    session_type=session_context.get("session_type"),
                    auth_state=session_context.get("auth_state"),
                    locale=session_context.get("locale"),
                )
                if isinstance(out, dict) and out.get("intent_hint"):
                    intent = out["intent_hint"]
            elif out is None and intent == "LEAD_CAPTURE":
                out = await _branch_lead_capture(message, subject, history, identity_obj, session_id=sid,
                                                 emit_token=emit_token, emit_citations=emit_citations)
            elif out is None and intent == "CALLBACK_REQUEST":
                out = await _branch_callback(message, history)
            elif out is None and intent == "MARKET_DATA":
                out = await _branch_market(db, message, subject, history)
            elif out is None and intent == "CLIENT_LOOKUP":
                out = await _branch_client_lookup(db, sid, message, subject, auth_row, identity_obj)
                hint = out.get("intent_hint") if isinstance(out, dict) else None
                if hint:
                    intent = hint
            elif out is None and intent == "DIRECTORY_QUERY":
                out = await _branch_directory(
                    sid, routing.get("tool_name"), routing.get("tool_args") or {},
                    identity_obj, session_context,
                )
            elif out is None and intent == "CLIENT_QUERY":
                out = await _branch_client_query(
                    sid, routing.get("tool_name"), routing.get("tool_args") or {},
                    identity_obj, session_context,
                )
            elif out is None and intent == "ESCALATION":
                out = await _branch_escalation(message)
            # ---------------- Phase 24c — BMIA intents ----------------
            elif out is None and intent == "BMIA_COMPLIANCE":
                from agents import bmia_branches as _bb
                tool_args = routing.get("tool_args") or {}
                out = await _bb.branch_compliance(
                    message, sources=tool_args.get("sources"),
                    top_k=int(tool_args.get("top_k") or 5),
                )
            elif out is None and intent == "BMIA_FUNDAMENTALS":
                from agents import bmia_branches as _bb
                tool_args = routing.get("tool_args") or {}
                out = await _bb.branch_fundamentals(
                    message, symbol_hint=tool_args.get("symbol"),
                    slice_kind=tool_args.get("slice") or "profile",
                )
            elif out is None and intent == "BMIA_BRIEFING":
                from agents import bmia_branches as _bb
                tool_args = routing.get("tool_args") or {}
                out = await _bb.branch_briefing(
                    message, date=tool_args.get("date"),
                    sections=tool_args.get("sections"),
                )
            elif out is None:  # SMALL_TALK fallback
                out = await _branch_small_talk(
                    message, history, identity_obj, emit_token=emit_token,
                    locale=session_context.get("locale"),
                )
            trace.append({"step": "specialist", "intent": intent, "status": "ok"})

    # Promote intent_hint into the final intent if the auth agent emitted one.
    if isinstance(out, dict) and out.get("intent_hint"):
        intent = out["intent_hint"]

    # Phase 13 — append the too-long notice if we trimmed the input.
    if too_long_notice_appended:
        _append_too_long_notice(out)

    payload = {
        "session_id": sid,
        "trace": trace,
        "blocks": out["blocks"],
        "citations": out.get("citations", []),
        "model": out.get("model"),
        "intent": intent,
    }
    # Phase 7 — if this turn was a fresh-mint from idle-expiry, expose the
    # prior id + any resume offers so the FE can render the rehydration card.
    if prior_session_id:
        payload["prior_session_id"] = prior_session_id
    if expiry_resume_offer:
        payload["resume_offer"] = expiry_resume_offer
        payload["blocks"] = [
            {"type": "resume_offer", "data": {"candidates": expiry_resume_offer}}
        ] + payload["blocks"]
    # If the auth_agent attached a resume offer (on verification), forward it.
    if isinstance(out, dict) and out.get("resume_offer") and not expiry_resume_offer:
        payload["resume_offer"] = out["resume_offer"]
    await _append_messages(db, sid, [
        {"role": "user", "content": message},
        {
            "role": "assistant",
            "content": _flatten_text(out["blocks"]),
            "blocks": out["blocks"],
            "citations": out.get("citations", []),
            "intent": intent,
            "model": out.get("model"),
            # Phase 11 — mark fallback replies so the Knowledge Gaps tab
            # can aggregate "what couldn't we answer" across all roles.
            "wm_fallback": any(
                (b.get("type") == "escalation_card" and (b.get("data") or {}).get("reason") in ("rm_required", "advisor_required"))
                or (b.get("type") == "form" and (b.get("data") or {}).get("endpoint") == "/api/leads/callback")
                for b in (out.get("blocks") or [])
            ),
        },
    ])

    # If we just transitioned to verified, snapshot to the archive collection
    # (best-effort, fire-and-forget).
    if intent == "AUTH_VERIFIED":
        try:
            from archives import snapshot_on_verify
            await snapshot_on_verify(db, sid)
        except Exception:
            logger.exception("archive snapshot_on_verify failed (non-fatal)")
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
            parts.append(f"[Client: UCC {d.get('ucc')} · {d.get('branch_name') or ''}]")
        elif b.get("type") == "employee_card":
            d = b.get("data", {})
            parts.append(f"[Employee: {d.get('name')} · {d.get('designation') or ''}]")
        elif b.get("type") == "escalation_card":
            parts.append("[Connect with advisor]")
        elif b.get("type") == "directory_card":
            d = b.get("data", {})
            parts.append(f"[Directory: {d.get('name')} · {d.get('designation') or ''} · {d.get('department') or ''}]")
        elif b.get("type") == "directory_list":
            d = b.get("data", {})
            parts.append(f"[Directory list: {d.get('title')} · {len(d.get('items') or [])} of {d.get('total', 0)}]")
        elif b.get("type") == "org_stats_card":
            d = b.get("data", {})
            parts.append(f"[Org stats: {d.get('total_employees')} total · {d.get('active_employees')} active]")
        elif b.get("type") == "reporting_chain_card":
            d = b.get("data", {})
            parts.append(f"[Reporting chain: {len(d.get('chain') or [])} levels]")
    return "\n\n".join(parts).strip()
