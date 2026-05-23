from fastapi import FastAPI, APIRouter, HTTPException, Header, Depends, Request
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import json
import asyncio
import logging
import uuid
import httpx
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

# Load env BEFORE importing agent modules (they read env at module level).
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import rag
import mocks
import widget_config
import identity as id_mod
import hardening
import handoff as handoff_mod
import resilience
from agents import orchestrator, router as router_agent
from agents.llm import call_with_fallback, extract_reply, last_ok, bind_db as bind_llm_db, ROUTER_CHAIN, CHAT_CHAIN, reset_cache as reset_llm_cache
from admin import build_admin_router

# MongoDB
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', '')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
hardening.install_log_filters()
ADMIN_TOKEN_STRENGTH = hardening.check_admin_token_strength()
CORS_ALLOW_ORIGINS, CORS_MODE = hardening.resolve_cors_origins()
logger = logging.getLogger(__name__)
logger.info("CORS resolved: mode=%s origins=%s", CORS_MODE, CORS_ALLOW_ORIGINS)

# ---------------- FastAPI ----------------
app = FastAPI(
    title="Mackertich ONE Advisor",
    description="Phase 6 — Real identity (employee / client / visitor) via OrgLens.",
    version="0.6.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
api_router = APIRouter(prefix="/api")


# ---------------- Models ----------------
class HealthResponse(BaseModel):
    status: str
    llm_reachable: bool
    model: Optional[str] = None
    detail: Optional[str] = None
    rag_chunks: int = 0
    embedder: Optional[str] = None
    last_chat_model: Optional[str] = None
    last_router_model: Optional[str] = None
    orglens_reachable: Optional[bool] = None
    orglens_permissions: Optional[List[str]] = None
    cors_mode: Optional[str] = None
    admin_token_strength: Optional[str] = None
    rate_limiting: Optional[str] = None


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str = Field(default="")


class TurnRequest(BaseModel):
    session_id: Optional[str] = None
    message: str = Field(default="")


class TurnResponse(BaseModel):
    session_id: str
    trace: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    model: Optional[str] = None
    intent: Optional[str] = None
    prior_session_id: Optional[str] = None
    resume_offer: Optional[List[Dict[str, Any]]] = None


class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class LeadSubmitRequest(BaseModel):
    form_type: str = Field(..., min_length=1)
    fields: Dict[str, Any]
    context: Dict[str, Any] = {}
    session_id: Optional[str] = None


class LeadSubmitResponse(BaseModel):
    lead_id: str
    message: str


class HandoffRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    handoff_type: str = Field(..., pattern="^(whatsapp|email)$")
    channel_target: str = Field(..., pattern="^(rm|hrbp|advisor)$")
    user_question: str = Field("", max_length=2000)
    context_snippet: Optional[str] = Field(None, max_length=4000)


class HandoffResponse(BaseModel):
    handoff_id: str
    lead_id: str
    target_display_name: Optional[str] = None
    target_kind: Optional[str] = None
    target_has_contact: bool
    target_contact_masked: Optional[str] = None
    handoff_type: str
    deep_link: Optional[str] = None
    fallback_link: Optional[str] = None
    should_callback_form: bool
    message_preview: str


# ---------------- Auth ----------------
def require_admin(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")
    return True


# ---------------- Rate limiting ----------------
_LIMITER = hardening.get_limiter()
_RATE_LIMITED_BLOCKS = [
    {"type": "text", "text": "Too many requests right now. Please pause for a minute and try again."},
    {"type": "escalation_card", "data": {"reason": "rate_limited"}},
]


def _enforce_chat_rate_limit(request: Request, session_id: Optional[str]) -> None:
    """Raise 429 if either the session or the IP has exceeded its window.
    Body is shaped so the FE can render it as a normal chat turn."""
    ip = hardening.client_ip_from(request)
    if session_id:
        ok, retry = _LIMITER.check("chat:session", session_id, limit=30, window_s=60)
        if not ok:
            raise HTTPException(
                status_code=429,
                detail={"reason": "rate_limited_session", "blocks": _RATE_LIMITED_BLOCKS,
                        "retry_after": retry},
                headers={"Retry-After": str(retry)},
            )
    ok_ip, retry_ip = _LIMITER.check("chat:ip", ip, limit=60, window_s=60)
    if not ok_ip:
        raise HTTPException(
            status_code=429,
            detail={"reason": "rate_limited_ip", "blocks": _RATE_LIMITED_BLOCKS,
                    "retry_after": retry_ip},
            headers={"Retry-After": str(retry_ip)},
        )


def _enforce_leads_rate_limit(request: Request) -> None:
    ip = hardening.client_ip_from(request)
    ok, retry = _LIMITER.check("leads:ip", ip, limit=10, window_s=60)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail="Too many lead submissions. Please wait a minute.",
            headers={"Retry-After": str(retry)},
        )


# ---------------- Routes ----------------
@api_router.get("/")
async def root():
    return {"service": "Mackertich ONE Advisor", "vertical_of": "SMIFS Ltd", "phase": 6}


@api_router.get("/health", response_model=HealthResponse)
async def health():
    chunk_count = await rag.ensure_index_loaded(db)
    ping_msgs = [
        {"role": "system", "content": "Respond with the single word: ok"},
        {"role": "user", "content": "ping"},
    ]
    # Probe OrgLens permissions in parallel with the LLM ping.
    orglens_perms: Optional[List[str]] = None
    orglens_ok: Optional[bool] = None
    try:
        perms = await id_mod.probe_permissions()
        orglens_perms = list(perms.get("permissions", []))
        orglens_ok = True
    except Exception as e:
        logger.warning("OrgLens permissions probe failed: %s", str(e)[:120])
        orglens_ok = False
    try:
        result = await call_with_fallback(ping_msgs, task="chat", temperature=0.0, max_tokens=4)
        resolved = result["data"].get("model") or result["model"]
        return HealthResponse(
            status="ok", llm_reachable=True, model=resolved,
            rag_chunks=chunk_count, embedder=rag.EMBEDDER_KIND,
            last_chat_model=last_ok("chat"), last_router_model=last_ok("router"),
            orglens_reachable=orglens_ok, orglens_permissions=orglens_perms,
            cors_mode=CORS_MODE, admin_token_strength=ADMIN_TOKEN_STRENGTH,
            rate_limiting="in_process",
        )
    except httpx.HTTPStatusError as e:
        body = e.response.text if e.response is not None else ""
        return HealthResponse(
            status="ok", llm_reachable=False,
            detail=f"HTTP {e.response.status_code}: {body[:200]}",
            rag_chunks=chunk_count, embedder=rag.EMBEDDER_KIND,
            orglens_reachable=orglens_ok, orglens_permissions=orglens_perms,
            cors_mode=CORS_MODE, admin_token_strength=ADMIN_TOKEN_STRENGTH,
            rate_limiting="in_process",
        )
    except Exception as e:
        return HealthResponse(
            status="ok", llm_reachable=False, detail=str(e)[:200],
            rag_chunks=chunk_count, embedder=rag.EMBEDDER_KIND,
            orglens_reachable=orglens_ok, orglens_permissions=orglens_perms,
            cors_mode=CORS_MODE, admin_token_strength=ADMIN_TOKEN_STRENGTH,
            rate_limiting="in_process",
        )


@api_router.post("/rag/search")
async def rag_search(req: RagSearchRequest):
    await rag.ensure_index_loaded(db)
    return await rag.search(req.query, top_k=req.top_k)


@api_router.post("/agent/turn", response_model=TurnResponse)
async def agent_turn(req: TurnRequest, request: Request):
    _enforce_chat_rate_limit(request, req.session_id)
    # Phase 13 fix-1 — empty / whitespace message must yield a graceful 200,
    # NOT a Pydantic 422. We handle it here BEFORE invoking the orchestrator
    # (it would normalise + reply anyway, but we want a guaranteed 200 path
    # with intent="EMPTY_INPUT" even if the orchestrator itself blew up).
    if not (req.message or "").strip():
        return await _empty_input_response(req.session_id)
    try:
        return await orchestrator.run_turn(db, req.session_id, req.message)
    except Exception as e:
        # Phase 13 — always-reply guarantee. Build a role-aware envelope so the
        # FE never sees a raw 5xx / empty body. PERSIST the envelope into
        # conversations so /api/sessions shows the fallback in history.
        error_id = resilience.new_error_id()
        session_ctx = await _session_role_ctx(req.session_id)
        env = resilience.graceful_envelope(
            session_id=session_ctx.get("session_id"),
            error_id=error_id,
            session_type=session_ctx.get("session_type"),
            auth_state=session_ctx.get("auth_state"),
            identity_obj=session_ctx.get("identity"),
            reason="internal_error",
        )
        logger.exception("agent_turn failed (error_id=%s)", error_id)
        await resilience.log_error(
            db, error_id=error_id, exc=e,
            session_id=session_ctx.get("session_id"),
            endpoint="/api/agent/turn",
            role_state_value=resilience.role_state(
                session_ctx.get("session_type"), session_ctx.get("auth_state"),
                session_ctx.get("identity")),
            user_message=req.message,
        )
        # Best-effort persistence — never let the fallback path itself raise.
        try:
            await orchestrator._persist_turn(
                db, env["session_id"] or (req.session_id or ""),
                req.message, env, env.get("intent"),
            )
        except Exception:
            logger.exception("persisting fallback envelope failed (non-fatal)")
        return env


async def _session_role_ctx(session_id: Optional[str]) -> Dict[str, Any]:
    """Best-effort lookup of (session_id, session_type, auth_state, identity)
    for the envelope builder. Tolerates a dead Mongo by returning {}."""
    if not session_id:
        return {"session_id": None}
    try:
        row = await db.sessions.find_one({"_id": session_id}, {"_id": 0}) or {}
        return {
            "session_id": session_id,
            "session_type": row.get("session_type"),
            "auth_state": row.get("auth_state"),
            "identity": row.get("identity"),
        }
    except Exception:
        return {"session_id": session_id}


async def _empty_input_response(session_id: Optional[str]) -> Dict[str, Any]:
    """Build a TurnResponse-shaped graceful reply for empty / whitespace input.

    Persists the (user='', assistant=<nudge>) pair to conversations so the FE
    session history stays consistent with all other turns.
    """
    sid = session_id or str(uuid.uuid4())
    # Ensure the session exists so /api/sessions/{sid} works on the next fetch.
    try:
        await orchestrator._get_or_create_session(db, sid)
        from agents import auth_agent
        await auth_agent.get_or_create_session_row(db, sid)
    except Exception:
        logger.exception("empty-input session init failed (non-fatal)")
    out = resilience.empty_input_reply()
    intent = out.get("intent_hint") or "EMPTY_INPUT"
    try:
        await orchestrator._persist_turn(db, sid, "", out, intent)
    except Exception:
        logger.exception("empty-input persist failed (non-fatal)")
    return {
        "session_id": sid,
        "trace": [{"step": "resilience", "kind": "empty_input"}],
        "blocks": out["blocks"],
        "citations": [],
        "model": None,
        "intent": intent,
    }


@api_router.post("/agent/turn/stream")
async def agent_turn_stream(req: TurnRequest, request: Request):
    _enforce_chat_rate_limit(request, req.session_id)

    # Phase 13 fix-1 — empty / whitespace must also be graceful in the stream
    # variant. We emit a single `result` SSE event with the same envelope
    # `/api/agent/turn` returns and close cleanly.
    if not (req.message or "").strip():
        empty_payload = await _empty_input_response(req.session_id)
        async def _empty_source():
            yield f"event: result\ndata: {json.dumps(empty_payload, ensure_ascii=False)}\n\n"
        headers = {
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream",
        }
        return StreamingResponse(_empty_source(), headers=headers, media_type="text/event-stream")

    queue: asyncio.Queue = asyncio.Queue()

    async def emit_status(event: Dict[str, Any]) -> None:
        await queue.put(("status", event))

    async def emit_token(token: str) -> None:
        await queue.put(("token", {"text": token}))

    async def emit_citations(citations: List[Dict[str, Any]]) -> None:
        await queue.put(("citations", citations))

    async def runner():
        try:
            payload = await orchestrator.run_turn(
                db, req.session_id, req.message,
                emit_status=emit_status, emit_token=emit_token, emit_citations=emit_citations,
            )
            await queue.put(("result", payload))
        except Exception as e:
            # Phase 13 — never propagate raw 5xx. Emit a `warning` event so the
            # FE can show "I hit a hiccup", followed by a graceful `result`
            # envelope. Persist to errors/conversations so admin can audit.
            error_id = resilience.new_error_id()
            logger.exception("stream runner failed (error_id=%s)", error_id)
            session_ctx = await _session_role_ctx(req.session_id)
            env = resilience.graceful_envelope(
                session_id=session_ctx.get("session_id"),
                error_id=error_id,
                session_type=session_ctx.get("session_type"),
                auth_state=session_ctx.get("auth_state"),
                identity_obj=session_ctx.get("identity"),
                reason="internal_error",
            )
            await resilience.log_error(
                db, error_id=error_id, exc=e,
                session_id=session_ctx.get("session_id"),
                endpoint="/api/agent/turn/stream",
                role_state_value=resilience.role_state(
                    session_ctx.get("session_type"), session_ctx.get("auth_state"),
                    session_ctx.get("identity")),
                user_message=req.message,
            )
            try:
                await orchestrator._persist_turn(
                    db, env["session_id"] or (req.session_id or ""),
                    req.message, env, env.get("intent"),
                )
            except Exception:
                logger.exception("persist fallback envelope failed (non-fatal)")
            await queue.put(("warning", {"error_id": error_id,
                                          "message": "I hit a hiccup — sending you a graceful reply."}))
            await queue.put(("result", env))
        finally:
            await queue.put(("__done__", None))

    # Phase 13 — heartbeat every 10s; hard cap stream lifetime at 60s.
    HEARTBEAT_S = 10.0
    HARD_CAP_S = 60.0

    async def event_source():
        task = asyncio.create_task(runner())
        loop = asyncio.get_event_loop()
        deadline = loop.time() + HARD_CAP_S
        try:
            while True:
                if await request.is_disconnected():
                    task.cancel()
                    break
                remaining = deadline - loop.time()
                if remaining <= 0:
                    # Stream timed out — emit graceful timeout & end.
                    error_id = resilience.new_error_id()
                    session_ctx = await _session_role_ctx(req.session_id)
                    env = resilience.graceful_envelope(
                        session_id=session_ctx.get("session_id"),
                        error_id=error_id,
                        session_type=session_ctx.get("session_type"),
                        auth_state=session_ctx.get("auth_state"),
                        identity_obj=session_ctx.get("identity"),
                        reason="timeout",
                    )
                    yield f"event: warning\ndata: {json.dumps({'error_id': error_id, 'message': 'Took too long, sending a graceful reply.'})}\n\n"
                    yield f"event: result\ndata: {json.dumps(env, ensure_ascii=False)}\n\n"
                    task.cancel()
                    break
                try:
                    timeout = min(HEARTBEAT_S, remaining)
                    event_type, data = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    # SSE comment-line heartbeat keeps the connection alive.
                    yield ": ping\n\n"
                    continue
                if event_type == "__done__":
                    break
                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Content-Type": "text/event-stream",
    }
    return StreamingResponse(event_source(), headers=headers, media_type="text/event-stream")


@api_router.post("/leads", response_model=LeadSubmitResponse)
async def submit_lead(req: LeadSubmitRequest, request: Request):
    _enforce_leads_rate_limit(request)
    if req.form_type not in {"lead_capture", "callback"}:
        raise HTTPException(status_code=400, detail=f"Unknown form_type: {req.form_type}")
    lead_id = str(uuid.uuid4())
    fields = req.fields or {}
    lead_email = (fields.get("email") or "").strip() or None
    lead_phone = (fields.get("phone") or fields.get("phone_number") or "").strip() or None
    e_hash = id_mod.email_hash(lead_email) if lead_email else ""
    p_hash = id_mod.phone_hash(lead_phone) if lead_phone else ""
    doc = {
        "_id": lead_id,
        "lead_id": lead_id,
        "brand": "Mackertich ONE",
        "parent_company": "SMIFS Ltd",
        "session_id": req.session_id,
        "form_type": req.form_type,
        "fields": fields,
        "context": req.context,
        "status": "new",
        "email_hash": e_hash,
        "phone_hash": p_hash,
        "email_display": id_mod.mask_email_display(lead_email) if lead_email else None,
        "phone_display": id_mod.mask_phone_display(lead_phone) if lead_phone else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.leads.insert_one(doc)
    # Phase 7 — write hashes to the session row for rehydration lookup
    if req.session_id and (e_hash or p_hash):
        set_fields: Dict[str, Any] = {}
        if e_hash:
            set_fields["email_hash"] = e_hash
        if p_hash:
            set_fields["phone_hash"] = p_hash
        await db.sessions.update_one(
            {"_id": req.session_id}, {"$set": set_fields}, upsert=False,
        )
    return LeadSubmitResponse(
        lead_id=lead_id,
        message="Thank you. A Mackertich ONE senior advisor will reach out within one business day.",
    )


# --- Phase 11: one-tap WhatsApp / Email handoff ---
@api_router.post("/handoff", response_model=HandoffResponse)
async def create_handoff(req: HandoffRequest):
    try:
        out = await handoff_mod.create_handoff(
            db,
            session_id=req.session_id,
            handoff_type=req.handoff_type,
            channel_target=req.channel_target,
            user_question=req.user_question,
            context_snippet=req.context_snippet,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return HandoffResponse(**out)


# --- Backward-compat /api/chat (Phase 0/1 shape) ---
class LegacyChatResponse(BaseModel):
    session_id: str
    reply: str
    model: Optional[str] = None
    grounded: bool = False
    citations: List[Dict[str, Any]] = []


@api_router.post("/chat", response_model=LegacyChatResponse)
async def chat(req: ChatRequest):
    # Phase 13 fix-1 — same empty-input nudge for the legacy endpoint.
    if not (req.message or "").strip():
        payload = await _empty_input_response(req.session_id)
        text = next((b.get("text", "") for b in payload["blocks"] if b.get("type") == "text"), "")
        return LegacyChatResponse(
            session_id=payload["session_id"], reply=text, model=None,
            grounded=False, citations=[],
        )
    try:
        payload = await orchestrator.run_turn(db, req.session_id, req.message)
    except Exception as e:
        error_id = resilience.new_error_id()
        session_ctx = await _session_role_ctx(req.session_id)
        payload = resilience.graceful_envelope(
            session_id=session_ctx.get("session_id"),
            error_id=error_id,
            session_type=session_ctx.get("session_type"),
            auth_state=session_ctx.get("auth_state"),
            identity_obj=session_ctx.get("identity"),
            reason="internal_error",
        )
        logger.exception("legacy chat failed (error_id=%s)", error_id)
        await resilience.log_error(
            db, error_id=error_id, exc=e,
            session_id=session_ctx.get("session_id"),
            endpoint="/api/chat",
            role_state_value=resilience.role_state(
                session_ctx.get("session_type"), session_ctx.get("auth_state"),
                session_ctx.get("identity")),
            user_message=req.message,
        )
    text_parts: List[str] = []
    for b in payload["blocks"]:
        if b.get("type") == "text":
            text_parts.append(b.get("text", ""))
    grounded = any(b.get("grounded") for b in payload["blocks"] if b.get("type") == "text")
    return LegacyChatResponse(
        session_id=payload["session_id"],
        reply="\n\n".join(text_parts).strip(),
        model=payload.get("model"),
        grounded=grounded,
        citations=payload.get("citations", []),
    )


@api_router.get("/conversations/{session_id}")
async def get_conversation(session_id: str):
    doc = await db.conversations.find_one({"session_id": session_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    return doc


# --- Phase 3/6 session endpoint ---
@api_router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Returns auth state + identity (for the verified chip and rehydration)."""
    from agents import auth_agent
    convo = await db.conversations.find_one({"session_id": session_id}, {"_id": 0})
    if not convo:
        raise HTTPException(status_code=404, detail="Session not found")
    auth_row = await auth_agent.get_or_create_session_row(db, session_id)
    identity_obj = None
    if auth_row.get("auth_state") == auth_agent.VERIFIED:
        identity_obj = auth_row.get("identity")
    history = []
    for m in convo.get("messages", []):
        entry = {"role": m.get("role"), "ts": m.get("ts")}
        if m.get("role") == "user":
            entry["text"] = m.get("content", "")
        else:
            entry["blocks"] = m.get("blocks") or [{"type": "text", "text": m.get("content", "")}]
            entry["citations"] = m.get("citations") or []
            entry["intent"] = m.get("intent")
            entry["model"] = m.get("model")
        history.append(entry)
    return {
        "session_id": session_id,
        "session_type": auth_row.get("session_type", "visitor"),
        "auth_state": auth_row.get("auth_state"),
        "lifecycle": auth_row.get("lifecycle", "active"),
        "identity": identity_obj,
        # Back-compat: old FE expects `client` key
        "client": ({"name": (identity_obj or {}).get("first_name") or "Client",
                    "code": (identity_obj or {}).get("ucc") or (identity_obj or {}).get("employee_id"),
                    "type": (identity_obj or {}).get("type")} if identity_obj else None),
        "history": history,
        "created_at": convo.get("created_at"),
        "updated_at": convo.get("updated_at"),
    }


@api_router.post("/sessions/{session_id}/signout")
async def session_signout(session_id: str):
    from agents import auth_agent
    import lifecycle as _lc
    row = await auth_agent.signout(db, session_id)
    # Phase 7 — explicit sign-out marks the session ended (no rehydration)
    await db.sessions.update_one(
        {"_id": session_id},
        {"$set": {"lifecycle": "ended", "ended_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {
        "session_id": session_id,
        "session_type": row.get("session_type", "visitor"),
        "auth_state": row.get("auth_state", "anonymous"),
        "identity": None,
        "client": None,
        "message": "Signed out. You may continue as a visitor.",
    }


# ---------------- Phase 10 — explicit role gate ----------------
class SelectRoleRequest(BaseModel):
    role: str = Field(..., description="'client' | 'employee' | 'visitor'")


@api_router.post("/sessions/{session_id}/select_role")
async def session_select_role(session_id: str, payload: SelectRoleRequest):
    from agents import auth_agent, orchestrator
    result = await auth_agent.select_role(db, session_id, payload.role)
    # Persist the bot's reply to conversations so GET /sessions can replay it
    if result and result.get("blocks"):
        await orchestrator._append_messages(
            db, session_id,
            [{"role": "assistant",
              "content": orchestrator._flatten_text(result["blocks"]),
              "blocks": result["blocks"],
              "citations": result.get("citations") or []}],
        )
    sess = await db.sessions.find_one({"_id": session_id}, {"_id": 0}) or {}
    return {
        "session_id": session_id,
        "session_type": sess.get("session_type"),
        "auth_state": sess.get("auth_state"),
        "blocks": result.get("blocks", []),
        "intent": result.get("intent_hint"),
    }


# ---------------- Phase 7 — rehydration endpoints ----------------
class ResumeRequest(BaseModel):
    prior_session_id: str = Field(..., min_length=1)


@api_router.get("/sessions/{session_id}/rehydration_candidates")
async def get_rehydration_candidates(session_id: str):
    import lifecycle as _lc
    candidates = await _lc.rehydration_candidates_for_session(db, session_id)
    return {"session_id": session_id, "candidates": candidates}


@api_router.post("/sessions/{session_id}/resume")
async def session_resume(session_id: str, req: ResumeRequest):
    import lifecycle as _lc
    try:
        await _lc.resume(db, session_id, req.prior_session_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    # Return merged history in the same shape as /api/sessions/{id}
    return await get_session(session_id)


@api_router.post("/sessions/{session_id}/decline_resume")
async def session_decline_resume(session_id: str):
    import lifecycle as _lc
    n = await _lc.decline_all_priors(db, session_id)
    return {"session_id": session_id, "ended_prior_sessions": n}


# --- Phase 5 public widget config ---
@api_router.get("/widget/config")
async def public_widget_config(request: Request):
    cfg = await widget_config.get()
    origin = request.headers.get("origin")
    if not widget_config.origin_allowed(origin, cfg):
        raise HTTPException(status_code=403, detail="Origin not permitted")
    public = widget_config._public_view(cfg)
    headers: Dict[str, str] = {"Cache-Control": "public, max-age=60", "Vary": "Origin"}
    headers["Access-Control-Allow-Origin"] = origin or "*"
    from fastapi.responses import JSONResponse
    return JSONResponse(content=public, headers=headers)


@api_router.options("/widget/config")
async def widget_config_preflight(request: Request):
    cfg = await widget_config.get()
    origin = request.headers.get("origin")
    headers = {
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }
    headers["Access-Control-Allow-Origin"] = origin if (widget_config.origin_allowed(origin, cfg) and origin) else "*"
    from fastapi.responses import Response
    return Response(status_code=204, headers=headers)


# ---------------- App wiring ----------------
app.include_router(api_router)
app.include_router(build_admin_router(db))
bind_llm_db(db)
widget_config.bind_db(db)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    try:
        active = await rag.detect_active_embedder()
        existing = await db.doc_chunks.count_documents({})
        if existing == 0:
            logger.info("doc_chunks empty — running seed ingestion (embedder=%s).", active)
            res = await rag.reingest(db)
            logger.info("Startup ingestion complete: %s", res)
        else:
            persisted = await rag.persisted_dim(db)
            expected = 1536 if active == "hub_ai" else 384
            if persisted and persisted != expected:
                logger.warning("Embedding dim mismatch (persisted=%d, expected=%d) — re-ingesting.",
                               persisted, expected)
                await db.doc_chunks.delete_many({})
                res = await rag.reingest(db)
                logger.info("Re-ingestion after dim mismatch: %s", res)
            else:
                await rag.ensure_index_loaded(db)
        await mocks.seed_if_empty(db)
        try:
            # Phase 7 — sessions TTL is 30 days (was 24h). Drop legacy index if present.
            existing = await db.sessions.index_information()
            for name, info in existing.items():
                if info.get("expireAfterSeconds") == 86400:
                    await db.sessions.drop_index(name)
                    logger.info("Dropped legacy 24h TTL index on sessions: %s", name)
                    break
            await db.sessions.create_index("updated_at_dt", expireAfterSeconds=2592000, name="ttl_updated_at_dt_30d")
            # Phase 7 — identity-hash lookup indexes
            await db.sessions.create_index("emp_id_hash", name="emp_id_hash_idx", sparse=True)
            await db.sessions.create_index("ucc_hash", name="ucc_hash_idx", sparse=True)
            await db.sessions.create_index("email_hash", name="email_hash_idx", sparse=True)
            await db.sessions.create_index("phone_hash", name="phone_hash_idx", sparse=True)
            await db.sessions.create_index("lifecycle", name="lifecycle_idx", sparse=True)
            await db.leads.create_index("email_hash", name="leads_email_hash_idx", sparse=True)
            await db.leads.create_index("phone_hash", name="leads_phone_hash_idx", sparse=True)
            await db.llm_calls.create_index("created_at_dt", expireAfterSeconds=90 * 86400, name="ttl_created_at_dt")
            await db.llm_calls.create_index([("created_at", -1)], name="created_at_desc")
            await db.leads.create_index([("created_at", -1)], name="leads_created_at_desc")
            await db.session_archives.create_index([("archived_at", -1)], name="archives_archived_at_desc")
            await db.session_archives.create_index("session_type", name="archives_session_type")
            # Phase 13 — errors + security_events collections (90-day TTL).
            await db.errors.create_index([("created_at", -1)], name="errors_created_at_desc")
            await db.errors.create_index("error_id", name="errors_error_id_idx", sparse=True)
            await db.security_events.create_index([("created_at", -1)], name="security_events_created_at_desc")
            await db.security_events.create_index("kind", name="security_events_kind_idx", sparse=True)
        except Exception:
            logger.exception("TTL index creation failed (non-fatal)")
        reset_llm_cache()
        logger.info("LLM chains active — CHAT_CHAIN=%s ROUTER_CHAIN=%s", CHAT_CHAIN, ROUTER_CHAIN)
        try:
            cfg = await widget_config.get(force_refresh=True)
            logger.info("widget_config loaded — brand=%s allowed_origins=%s",
                        cfg.get("brand_name"), cfg.get("allowed_origins"))
        except Exception:
            logger.exception("widget_config init failed (non-fatal)")
        # OrgLens permission self-check
        try:
            perms = await id_mod.probe_permissions()
            logger.info("OrgLens permissions OK — key=%s scopes=%s",
                        perms.get("key_id"), perms.get("permissions"))
        except Exception as e:
            logger.warning("OrgLens self-check failed: %s", str(e)[:160])
        # Phase 7 — warn on fallback identity-hash secret
        if not id_mod.IDENTITY_HASH_SECRET:
            logger.warning("⚠️  IDENTITY_HASH_SECRET is not set — falling back to combined PAN HMAC key. "
                           "Set a dedicated 32-byte secret in env before production traffic.")
        try:
            probe = await router_agent.classify("What is an AIF?", history=[])
            logger.info("Router self-check OK — model=%s intent=%s confidence=%.2f",
                        probe.get("model"), probe.get("intent"), probe.get("confidence", 0.0))
        except Exception:
            logger.exception("Router self-check failed (non-fatal).")
        # Phase 9 — SMIFS Knowledge API auto-sync (background, non-blocking)
        try:
            import knowledge_sync
            asyncio.create_task(knowledge_sync.startup_sync_if_empty(db))
        except Exception:
            logger.exception("SMIFS KB auto-sync scheduling failed (non-fatal).")
        # Phase 11 — recurring delta-sync scheduler
        try:
            import knowledge_sync
            asyncio.create_task(knowledge_sync.delta_sync_loop(db))
        except Exception:
            logger.exception("SMIFS KB delta-sync scheduler failed to start (non-fatal).")
    except Exception:
        logger.exception("Startup initialization failed.")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
