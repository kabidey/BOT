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
import re
import httpx
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta

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


# ---------------- Phase 24b.fix3 — client-side stream error telemetry ----------------
class ClientStreamError(BaseModel):
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    turn_count: Optional[int] = None
    error_name: Optional[str] = None
    error_message: Optional[str] = None
    user_agent: Optional[str] = None
    last_http_status: Optional[int] = None
    retried: Optional[bool] = None
    # Phase 32b — optional "axios:GET:/api/foo" tag so we can correlate user-
    # visible error banners with the exact endpoint that 4xx'd, without
    # needing browser DevTools. Used by the global axios interceptor.
    error_context: Optional[str] = None


@api_router.post("/client_errors")
async def report_client_error(payload: ClientStreamError):
    """FE posts here whenever the SSE catch in Chat.jsx fires (the
    'advisory engine unreachable' path). Stored in `client_stream_errors`
    for offline RCA. Never blocks; always returns 200.
    """
    try:
        await db.client_stream_errors.insert_one({
            "ts": datetime.now(timezone.utc).isoformat(),
            **payload.model_dump(exclude_none=True),
        })
    except Exception:
        logger.exception("client_stream_errors insert failed (non-fatal)")
    return {"ok": True}


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


# ---------------- Phase 26c — Dynamic forms ----------------
def _check_admin_request(request: Request) -> None:
    """Phase 26c — bearer + legacy header tolerance (mirrors admin.require_admin)."""
    bearer = ""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        bearer = auth.split(" ", 1)[1].strip()
    legacy = request.headers.get("x-admin-token") or ""
    presented = bearer or legacy
    if not ADMIN_TOKEN or presented != ADMIN_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing admin token. Send 'Authorization: Bearer <token>' or 'X-Admin-Token: <token>'.",
        )


class FormSubmitRequest(BaseModel):
    form_id: str = Field(..., min_length=1)
    form_data: Dict[str, Any]
    session_id: Optional[str] = None
    context: Dict[str, Any] = {}


class FormSubmitResponse(BaseModel):
    submission_id: str
    message: str
    email_status: str


_VALID_FORM_IDS = {
    "demand_capture", "referral_capture", "feedback_capture",
    "complaint_capture", "callback_request",
}


@api_router.post("/forms/submit", response_model=FormSubmitResponse)
async def submit_dynamic_form(req: FormSubmitRequest, request: Request):
    """Phase 26c — receive a dynamic form submission, persist, fire email."""
    _enforce_leads_rate_limit(request)
    if req.form_id not in _VALID_FORM_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown form_id: {req.form_id}")

    submission_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Pull last 8 conversation turns for excerpt (best-effort).
    excerpt: List[Dict[str, Any]] = []
    if req.session_id:
        try:
            conv = await db.conversations.find_one(
                {"session_id": req.session_id},
                {"_id": 0, "messages": {"$slice": -8}},
            )
            for m in (conv or {}).get("messages", []) or []:
                excerpt.append({
                    "role": m.get("role"),
                    "content": (m.get("content") or "")[:800],
                })
        except Exception:
            logger.exception("forms/submit: conversation excerpt fetch failed")

    sess = await db.sessions.find_one({"_id": req.session_id}, {"_id": 0}) if req.session_id else None
    persona = (sess or {}).get("session_type") or req.context.get("persona") or "visitor"

    doc = {
        "_id": submission_id,
        "submission_id": submission_id,
        "form_id": req.form_id,
        "form_data": req.form_data,
        "context": req.context or {},
        "session_id": req.session_id,
        "persona": persona,
        "conversation_excerpt": excerpt,
        "submitted_at": now,
        "email_status": "pending",
        "email_detail": None,
        "priority": "high" if req.form_id == "complaint_capture" else "normal",
    }
    await db.forms_submissions.insert_one(doc)

    # Mark cooldown bookkeeping on the session.
    if req.session_id:
        try:
            from agents.dynamic_forms import mark_form_seen
            await mark_form_seen(db, req.session_id, req.form_id)
        except Exception:
            logger.exception("forms/submit: mark_form_seen failed")

    # Fire-and-forget email (do it inline so admin sees status immediately, but
    # don't bubble up errors to the user — submission already persisted).
    email_status = "pending"
    email_detail = None
    try:
        import forms_email
        result = forms_email.send(doc)
        email_status = result.get("status") or "pending"
        email_detail = result.get("detail")
        await db.forms_submissions.update_one(
            {"_id": submission_id},
            {"$set": {"email_status": email_status, "email_detail": email_detail}},
        )
    except Exception as e:
        logger.exception("forms/submit: email dispatch crashed")
        email_status = "failed"
        email_detail = f"{type(e).__name__}: {e}"
        await db.forms_submissions.update_one(
            {"_id": submission_id},
            {"$set": {"email_status": "failed", "email_detail": email_detail}},
        )

    # Success message — prefer the schema's own copy if it travelled in `context`.
    msg_map = {
        "demand_capture":   "Submitted. Our research desk will get back to you within 24h.",
        "referral_capture": "Thank you for trusting us with their future. We'll reach out gently and reference your introduction.",
        "feedback_capture": "Thank you — your feedback shapes how we serve.",
        "complaint_capture": "We take this seriously. A senior advisor will personally reach out within 4 business hours.",
        "callback_request": "Got it — a senior advisor will reach out at your preferred time.",
    }
    return FormSubmitResponse(
        submission_id=submission_id,
        message=msg_map.get(req.form_id, "Submitted."),
        email_status=email_status,
    )


@api_router.get("/admin/forms/submissions")
async def admin_list_form_submissions(
    request: Request,
    form_id: Optional[str] = None,
    persona: Optional[str] = None,
    email_status: Optional[str] = None,
    limit: int = 100,
):
    """Phase 26e — list form submissions for the admin Forms tab."""
    _check_admin_request(request)
    q: Dict[str, Any] = {}
    if form_id: q["form_id"] = form_id
    if persona: q["persona"] = persona
    if email_status: q["email_status"] = email_status
    limit = max(1, min(int(limit or 100), 500))
    cur = db.forms_submissions.find(q, {"_id": 0}).sort("submitted_at", -1).limit(limit)
    rows = await cur.to_list(length=limit)
    counts = {
        "total":   await db.forms_submissions.estimated_document_count(),
        "pending": await db.forms_submissions.count_documents({"email_status": "pending"}),
        "failed":  await db.forms_submissions.count_documents({"email_status": "failed"}),
    }
    return {"rows": rows, "counts": counts}


@api_router.post("/admin/forms/{submission_id}/retry")
async def admin_retry_form_email(submission_id: str, request: Request):
    """Phase 26e — retry sending the email for a previously failed submission."""
    _check_admin_request(request)
    doc = await db.forms_submissions.find_one({"_id": submission_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        import forms_email
        result = forms_email.send(doc)
        await db.forms_submissions.update_one(
            {"_id": submission_id},
            {"$set": {"email_status": result.get("status"), "email_detail": result.get("detail")}},
        )
        return {"ok": result.get("ok"), "status": result.get("status"), "detail": result.get("detail")}
    except Exception as e:
        await db.forms_submissions.update_one(
            {"_id": submission_id},
            {"$set": {"email_status": "failed", "email_detail": str(e)}},
        )
        raise HTTPException(status_code=500, detail=str(e))


# ---------------- Phase 26.2.D — Cost ledger + insights ----------------
@api_router.get("/admin/cost_ledger")
async def admin_cost_ledger(
    request: Request,
    kind: Optional[str] = None,
    event_type: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 50,
):
    """Phase 26.2.D — unified cost-ledger feed.

    Returns rows from BOTH collections in a single shape:
      - `db.llm_calls`  (every Hub AI completion — task = router/chat/etc.)
      - `db.cost_ledger` (fan-out synthesis events with event_type)

    Query params:
      kind         — filter by row kind (e.g. 'fanout_synthesis', 'llm_compose', 'router')
      event_type   — for fan-out rows: 'ticker' | 'product' | 'identity'
      since        — ISO timestamp (rows newer than this)
      limit        — 1..500, default 50
    """
    _check_admin_request(request)
    limit = max(1, min(int(limit or 50), 500))

    rows: List[Dict[str, Any]] = []

    # ---- Pull fan-out rows ----
    if kind in (None, "fanout_synthesis"):
        q: Dict[str, Any] = {}
        if event_type:
            q["event_type"] = event_type
        if since:
            q["ts"] = {"$gte": since}
        cur = db.cost_ledger.find(q, {"_id": 0}).sort("ts", -1).limit(limit)
        async for r in cur:
            rows.append({
                "ts":          r.get("ts"),
                "kind":        "fanout_synthesis",
                "event_type":  r.get("event_type"),
                "model":       r.get("model"),
                "tokens":      r.get("tokens"),
                "prompt_tokens":     r.get("prompt_tokens"),
                "completion_tokens": r.get("completion_tokens"),
                "usd":         r.get("usd"),
                "subject":     r.get("subject"),
                "elapsed_ms":  r.get("elapsed_ms"),
                "fanout_ms":   r.get("fanout_ms"),
                "synthesis_ms": r.get("synthesis_ms"),
                "ok":          r.get("ok"),
                "timeout":     r.get("timeout"),
                "error":       r.get("error"),
                "session_id":  r.get("session_id"),
            })

    # ---- Pull llm_calls rows ----
    if kind in (None, "llm_compose", "router") or (kind and kind not in {"fanout_synthesis"}):
        q2: Dict[str, Any] = {}
        if since:
            q2["created_at"] = {"$gte": since}
        # Map admin 'kind' → llm_calls.task
        if kind == "router":
            q2["task"] = "router"
        elif kind == "llm_compose":
            q2["task"] = "chat"
        elif kind and kind not in {"fanout_synthesis", "llm_compose", "router"}:
            q2["task"] = kind
        cur = db.llm_calls.find(q2, {"_id": 0}).sort("created_at", -1).limit(limit)
        async for r in cur:
            t = r.get("task")
            mapped_kind = "router" if t == "router" else "llm_compose"
            rows.append({
                "ts":         r.get("created_at"),
                "kind":       mapped_kind,
                "event_type": None,
                "model":      r.get("model_resolved") or r.get("model_requested"),
                "tokens":     r.get("total_tokens"),
                "input_tokens":  r.get("input_tokens"),
                "output_tokens": r.get("output_tokens"),
                "usd":         None,
                "inr":         r.get("cost_inr"),
                "latency_ms":  r.get("latency_ms"),
                "session_id":  r.get("session_id"),
                "intent":      r.get("intent"),
            })

    # Sort by ts desc, trim to limit
    rows.sort(key=lambda r: r.get("ts") or "", reverse=True)
    rows = rows[:limit]

    counts = {
        "llm_calls_total":   await db.llm_calls.estimated_document_count(),
        "fanout_total":      await db.cost_ledger.count_documents({"kind": "fanout_synthesis"}),
    }
    # Cumulative INR cost (best-effort, only what's in llm_calls)
    pipeline = [{"$group": {"_id": None, "total_inr": {"$sum": "$cost_inr"}}}]
    total_inr = 0.0
    async for doc in db.llm_calls.aggregate(pipeline):
        total_inr = float(doc.get("total_inr") or 0.0)
    return {"rows": rows, "counts": counts, "total_inr": round(total_inr, 4)}


@api_router.get("/admin/insight/top_asks")
async def admin_top_asks(request: Request, days: int = 7, limit: int = 10):
    """Phase 26.2.D — weekly aggregate of the most-asked tickers + products,
    surfaced from the fan-out cost-ledger rows."""
    _check_admin_request(request)
    limit = max(1, min(int(limit or 10), 50))
    days = max(1, min(int(days or 7), 90))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    async def _top(event_type: str) -> List[Dict[str, Any]]:
        pipeline = [
            {"$match": {"kind": "fanout_synthesis",
                        "event_type": event_type,
                        "ts": {"$gte": cutoff}}},
            {"$group": {"_id": "$subject",
                        "count": {"$sum": 1},
                        "last_at": {"$max": "$ts"}}},
            {"$sort": {"count": -1, "last_at": -1}},
            {"$limit": limit},
        ]
        out: List[Dict[str, Any]] = []
        async for doc in db.cost_ledger.aggregate(pipeline):
            out.append({"subject": doc.get("_id"),
                        "count": doc.get("count"),
                        "last_at": doc.get("last_at")})
        return out

    return {
        "since": cutoff,
        "days": days,
        "tickers":  await _top("ticker"),
        "products": await _top("product"),
        "identity": await _top("identity"),
    }


# ---------------- Phase 27 — Errors collection admin readouts ----------------
def _errors_row_to_canonical(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Map the on-disk `errors` doc to the canonical response shape Phase 27
    asks for. Tolerates both new and legacy field names so this works even
    if a future schema change happens.

    On-disk schema (resilience.log_error):
      error_id, created_at, endpoint, session_id, role_state,
      exc_type, exc_message, traceback, user_message_excerpt
    """
    exc_type = doc.get("exc_type") or ""
    exc_message = doc.get("exc_message") or ""
    if exc_type and exc_message:
        exc = f"{exc_type}: {exc_message}"
    else:
        exc = exc_type or exc_message or doc.get("exc") or ""
    return {
        "error_id":     doc.get("error_id"),
        "created_at":   doc.get("created_at"),
        "endpoint":     doc.get("endpoint"),
        "session_id":   doc.get("session_id"),
        "user_message": doc.get("user_message_excerpt") or doc.get("user_message"),
        "exc":          exc,
        "exc_type":     exc_type or None,
        "traceback":    doc.get("traceback"),
        "request_meta": {
            "role_state": doc.get("role_state"),
        },
    }


@api_router.get("/admin/errors/recent")
async def admin_errors_recent(
    request: Request,
    limit: int = 20,
    endpoint: Optional[str] = None,
    session_prefix: Optional[str] = None,
    since_minutes: Optional[int] = None,
):
    """Phase 27 — read-only window into the `errors` collection so prod
    tracebacks are pull-able from outside a sandboxed container.

    Query params:
      limit            — max rows (1..100, default 20)
      endpoint         — filter by endpoint path, e.g. `/api/agent/turn/stream`
      session_prefix   — case-insensitive prefix match on session_id (e.g. `BA0ED6F5`)
      since_minutes    — only rows created in the last N minutes
    """
    _check_admin_request(request)
    limit = max(1, min(int(limit or 20), 100))
    q: Dict[str, Any] = {}
    if endpoint:
        q["endpoint"] = endpoint
    if session_prefix:
        # `created_at` is stored as ISO-8601 string, session_id stored verbatim.
        # Anchor the regex so we only match true prefixes; escape user input.
        q["session_id"] = {"$regex": "^" + re.escape(session_prefix), "$options": "i"}
    if since_minutes is not None:
        try:
            mins = max(1, min(int(since_minutes), 60 * 24 * 30))  # 30-day cap
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=mins)).isoformat()
            q["created_at"] = {"$gte": cutoff}
        except (TypeError, ValueError):
            pass
    cursor = db.errors.find(q, {"_id": 0}).sort([("created_at", -1)]).limit(limit)
    rows: List[Dict[str, Any]] = []
    async for doc in cursor:
        rows.append(_errors_row_to_canonical(doc))
    return {"count": len(rows), "rows": rows}


@api_router.get("/admin/errors/summary")
async def admin_errors_summary(request: Request):
    """Phase 27 — last-24h grouped counts of errors for the dashboard."""
    _check_admin_request(request)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()
    base_match = {"$match": {"created_at": {"$gte": cutoff}}}

    total = await db.errors.count_documents({"created_at": {"$gte": cutoff}})

    async def _agg(pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        async for doc in db.errors.aggregate(pipeline):
            out.append(doc)
        return out

    by_endpoint = await _agg([
        base_match,
        {"$group": {"_id": "$endpoint", "count": {"$sum": 1},
                    "last_at": {"$max": "$created_at"}}},
        {"$sort": {"count": -1, "last_at": -1}},
        {"$limit": 25},
    ])
    by_endpoint = [{"endpoint": d.get("_id"), "count": d.get("count"),
                    "last_at": d.get("last_at")} for d in by_endpoint]

    by_exc = await _agg([
        base_match,
        {"$group": {"_id": "$exc_type", "count": {"$sum": 1},
                    "last_at": {"$max": "$created_at"},
                    "sample_message": {"$last": "$exc_message"}}},
        {"$sort": {"count": -1, "last_at": -1}},
        {"$limit": 25},
    ])
    by_exception_type = [{"exc_type": d.get("_id") or "Unknown",
                          "count": d.get("count"),
                          "last_at": d.get("last_at"),
                          "sample_message": d.get("sample_message")}
                         for d in by_exc]

    # Hourly buckets — ISO `created_at` strings sort lexicographically by hour
    # if we strip to 'YYYY-MM-DDTHH'. Use $substr for that.
    by_hour_raw = await _agg([
        base_match,
        {"$group": {"_id": {"$substr": ["$created_at", 0, 13]},
                    "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
        {"$limit": 24},
    ])
    by_hour = [{"hour": d.get("_id"), "count": d.get("count")} for d in by_hour_raw]

    return {
        "since": cutoff,
        "total": total,
        "by_endpoint": by_endpoint,
        "by_exception_type": by_exception_type,
        "by_hour": by_hour,
    }


# ---------------- Phase 27 — Admin-triggered re-embed migration ----------------
import admin_reembed as _admin_reembed


class ReembedRunRequest(BaseModel):
    dry_run: bool = False
    batch_size: int = 50
    max_chunks: Optional[int] = None
    purge_legacy: bool = False


@api_router.post("/admin/reembed/run")
async def admin_reembed_run(req: ReembedRunRequest, request: Request):
    """Phase 27 — kick off (or simulate) the doc_chunks re-embed migration.

    Body:
      dry_run        — if true, returns counts + cost estimate without writing.
      batch_size     — chunks per Hub AI /embeddings call (1..100, default 50).
      max_chunks     — optional cap; useful for incremental rollouts.
      purge_legacy   — if true, after migration delete any chunks that are
                       still on a non-target dim/model.

    Returns the handle immediately; poll `GET /admin/reembed/status/{job_id}`.
    """
    _check_admin_request(request)
    batch_size = max(1, min(int(req.batch_size or 50), 100))
    max_chunks = req.max_chunks
    if max_chunks is not None:
        max_chunks = max(0, int(max_chunks))
        if max_chunks == 0:
            max_chunks = None
    return await _admin_reembed.kickoff(
        db,
        dry_run=bool(req.dry_run),
        batch_size=batch_size,
        max_chunks=max_chunks,
        purge_legacy=bool(req.purge_legacy),
    )


@api_router.get("/admin/reembed/status/{job_id}")
async def admin_reembed_status(job_id: str, request: Request):
    """Phase 27 — poll a single re-embed job's progress + status."""
    _check_admin_request(request)
    doc = await _admin_reembed.get_status(db, job_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")
    return doc


@api_router.get("/admin/reembed/jobs")
async def admin_reembed_jobs(request: Request, limit: int = 20):
    """Phase 27 — list recent re-embed jobs (DESC by started_at)."""
    _check_admin_request(request)
    limit = max(1, min(int(limit or 20), 100))
    return {"jobs": await _admin_reembed.list_jobs(db, limit=limit)}


@api_router.get("/admin/reembed/estimate")
async def admin_reembed_estimate(request: Request):
    """Phase 27 — cheap read-only count + cost estimate without starting a job."""
    _check_admin_request(request)
    return await _admin_reembed.estimate(db)


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
        "locale": auth_row.get("locale") or "en",
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


# ---------------- Phase 18 — multilingual locale ----------------
SUPPORTED_LOCALES = {"en", "hi", "ta"}


class LocaleRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    locale: str = Field(..., pattern="^(en|hi|ta)$")


class LocaleResponse(BaseModel):
    session_id: str
    locale: str
    supported: List[str]


@api_router.post("/agent/locale", response_model=LocaleResponse)
async def set_locale(req: LocaleRequest):
    """Phase 18 — Workstream B. Update the session-level locale that the
    orchestrator injects into every LLM system prompt for the next turn.

    Forms + structured data stay in English by design; only the chat
    response prose is localised.
    """
    if req.locale not in SUPPORTED_LOCALES:
        raise HTTPException(status_code=400, detail=f"Unsupported locale: {req.locale}")
    from agents import auth_agent
    # Ensure the session row exists so the upsert never silently no-ops.
    await auth_agent.get_or_create_session_row(db, req.session_id)
    now_dt = datetime.now(timezone.utc)
    await db.sessions.update_one(
        {"_id": req.session_id},
        {"$set": {"locale": req.locale,
                  "updated_at": now_dt.isoformat(),
                  "updated_at_dt": now_dt}},
    )
    return LocaleResponse(
        session_id=req.session_id, locale=req.locale,
        supported=sorted(SUPPORTED_LOCALES),
    )


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
import sales_api
api_router.include_router(sales_api.build_router(db))


# ---------------- Phase 20 — chart serving ----------------
from fastapi.responses import FileResponse as _FileResponse
from pathlib import Path as _Path

_CHARTS_DIR = _Path("/app/uploads/charts")


@api_router.get("/charts/{chart_id}.png")
async def get_chart_png(chart_id: str):
    """Serve a generated chart PNG. Path-traversal-safe.
    Files older than 24h are swept on every read (cheap)."""
    safe = "".join(c for c in chart_id if c.isalnum())
    if not safe or len(safe) > 64:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="invalid_chart_id")
    p = _CHARTS_DIR / f"{safe}.png"
    if not p.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="chart_not_found")
    return _FileResponse(str(p), media_type="image/png")


app.include_router(api_router)
app.include_router(build_admin_router(db))
bind_llm_db(db)
widget_config.bind_db(db)
# Phase 24c — let BMIA client log errors to Mongo.
try:
    from agents import bmia_client as _bmia_init
    _bmia_init.bind_db(db)
except Exception:
    pass
# Phase 22 — Silent device-fingerprint guard. MUST be added AFTER the routers
# include, but the middleware itself is order-independent of CORS (added next).
import fingerprint_middleware as _fp_mw
app.add_middleware(_fp_mw.FingerprintGuardMiddleware, db=db)
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
            # Phase 24a.3 — expected dim depends on the active Hub AI embed model.
            # text-embedding-3-large = 3072, text-embedding-3-small = 1536, local MiniLM = 384.
            if active == "hub_ai":
                model = (os.environ.get("HUB_EMBED_MODEL") or "text-embedding-3-large").lower()
                expected = 3072 if "3-large" in model else 1536
            else:
                expected = 384
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
            # Phase 22 — fingerprint guard indexes.
            try:
                import fingerprint_guard as _fpg
                await _fpg.ensure_indexes(db)
            except Exception:
                logger.exception("fingerprint_guard.ensure_indexes failed (non-fatal)")
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
        # Phase 16 — one-time mode=full backfill so previously indexed chunks
        # pick up new per-subsource metadata (audience, vehicle_id, version_no, …).
        try:
            import knowledge_sync
            asyncio.create_task(knowledge_sync.phase16_backfill_if_needed(db))
        except Exception:
            logger.exception("Phase 16 backfill scheduling failed (non-fatal).")
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
