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
from agents import orchestrator
from agents.llm import call_with_fallback, extract_reply, last_ok

# MongoDB
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', '')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------- FastAPI ----------------
app = FastAPI(
    title="SMIFS Wealth-Engagement Agent",
    description="Phase 2 — Multi-agent orchestrator with rich JSON payloads.",
    version="0.3.0",
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


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str = Field(..., min_length=1)


class TurnRequest(BaseModel):
    session_id: Optional[str] = None
    message: str = Field(..., min_length=1)


class TurnResponse(BaseModel):
    session_id: str
    trace: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    model: Optional[str] = None
    intent: Optional[str] = None


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


# ---------------- Auth ----------------
def require_admin(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")
    return True


# ---------------- Routes ----------------
@api_router.get("/")
async def root():
    return {"service": "SMIFS Wealth-Engagement Agent", "phase": 2}


@api_router.get("/health", response_model=HealthResponse)
async def health():
    chunk_count = await rag.ensure_index_loaded(db)
    ping_msgs = [
        {"role": "system", "content": "Respond with the single word: ok"},
        {"role": "user", "content": "ping"},
    ]
    try:
        result = await call_with_fallback(ping_msgs, task="chat", temperature=0.0, max_tokens=4)
        resolved = result["data"].get("model") or result["model"]
        return HealthResponse(
            status="ok",
            llm_reachable=True,
            model=resolved,
            rag_chunks=chunk_count,
            embedder=rag.EMBEDDER_KIND,
            last_chat_model=last_ok("chat"),
            last_router_model=last_ok("router"),
        )
    except httpx.HTTPStatusError as e:
        body = e.response.text if e.response is not None else ""
        return HealthResponse(
            status="ok", llm_reachable=False,
            detail=f"HTTP {e.response.status_code}: {body[:200]}",
            rag_chunks=chunk_count, embedder=rag.EMBEDDER_KIND,
        )
    except Exception as e:
        return HealthResponse(
            status="ok", llm_reachable=False, detail=str(e)[:200],
            rag_chunks=chunk_count, embedder=rag.EMBEDDER_KIND,
        )


@api_router.post("/admin/reingest")
async def admin_reingest(_: bool = Depends(require_admin)):
    return await rag.reingest(db)


@api_router.post("/rag/search")
async def rag_search(req: RagSearchRequest):
    await rag.ensure_index_loaded(db)
    return await rag.search(req.query, top_k=req.top_k)


# --- Phase 2 primary endpoint ---
@api_router.post("/agent/turn", response_model=TurnResponse)
async def agent_turn(req: TurnRequest):
    try:
        return await orchestrator.run_turn(db, req.session_id, req.message)
    except httpx.HTTPStatusError as e:
        body = e.response.text if e.response is not None else ""
        logger.error("agent_turn upstream %s: %s", e.response.status_code, body)
        raise HTTPException(status_code=502, detail=f"Hub AI error ({e.response.status_code}): {body[:300]}")
    except Exception as e:
        logger.exception("agent_turn failed")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/agent/turn/stream")
async def agent_turn_stream(req: TurnRequest, request: Request):
    """Server-Sent Events stream of router → specialist status events, then final result."""
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: Dict[str, Any]) -> None:
        await queue.put(("status", event))

    async def runner():
        try:
            payload = await orchestrator.run_turn(db, req.session_id, req.message, emit_status=emit)
            await queue.put(("result", payload))
        except httpx.HTTPStatusError as e:
            body = e.response.text if e.response is not None else ""
            await queue.put(("error", {"detail": f"Hub AI error ({e.response.status_code}): {body[:300]}"}))
        except Exception as e:
            logger.exception("stream runner failed")
            await queue.put(("error", {"detail": str(e)}))
        finally:
            await queue.put(("__done__", None))

    async def event_source():
        task = asyncio.create_task(runner())
        try:
            while True:
                if await request.is_disconnected():
                    task.cancel()
                    break
                try:
                    event_type, data = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Heartbeat to keep the connection alive
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
        "X-Accel-Buffering": "no",  # disable proxy buffering for SSE
        "Content-Type": "text/event-stream",
    }
    return StreamingResponse(event_source(), headers=headers, media_type="text/event-stream")


@api_router.post("/leads", response_model=LeadSubmitResponse)
async def submit_lead(req: LeadSubmitRequest):
    if req.form_type not in {"lead_capture", "callback"}:
        raise HTTPException(status_code=400, detail=f"Unknown form_type: {req.form_type}")
    lead_id = str(uuid.uuid4())
    doc = {
        "_id": lead_id,
        "lead_id": lead_id,
        "session_id": req.session_id,
        "form_type": req.form_type,
        "fields": req.fields,
        "context": req.context,
        "status": "new",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.leads.insert_one(doc)
    return LeadSubmitResponse(
        lead_id=lead_id,
        message="Thank you. A senior advisor will reach out within one business day.",
    )


# --- Backward-compat /api/chat (Phase 0/1 shape) ---
class LegacyChatResponse(BaseModel):
    session_id: str
    reply: str
    model: Optional[str] = None
    grounded: bool = False
    citations: List[Dict[str, Any]] = []


@api_router.post("/chat", response_model=LegacyChatResponse)
async def chat(req: ChatRequest):
    payload = await orchestrator.run_turn(db, req.session_id, req.message)
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


# --- Phase 3 session endpoints ---
@api_router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Returns auth_state + verified client info (if any) + full conversation history.
    Used by the frontend on mount to rehydrate the chat thread."""
    from agents import auth_agent
    convo = await db.conversations.find_one({"session_id": session_id}, {"_id": 0})
    if not convo:
        raise HTTPException(status_code=404, detail="Session not found")
    auth_row = await auth_agent.get_or_create_session_row(db, session_id)
    client_info = None
    if auth_row.get("auth_state") == "verified" and auth_row.get("client_code"):
        client = await db.mock_clients.find_one(
            {"code": auth_row["client_code"]},
            {"_id": 0, "verify_questions": 0, "phone": 0},
        )
        if client:
            client_info = {"name": client.get("name"), "code": client.get("code")}
    # Strip MongoDB-internal _id from auth row (already excluded above) and shape history
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
        "auth_state": auth_row.get("auth_state"),
        "client": client_info,
        "history": history,
        "created_at": convo.get("created_at"),
        "updated_at": convo.get("updated_at"),
    }


@api_router.post("/sessions/{session_id}/signout")
async def session_signout(session_id: str):
    """Idempotent — safe to call on any session_id, even if no row exists."""
    from agents import auth_agent
    row = await auth_agent.signout(db, session_id)
    return {
        "session_id": session_id,
        "auth_state": row.get("auth_state", "anonymous"),
        "client": None,
        "message": "Signed out. You may continue as a prospect.",
    }


# ---------------- App wiring ----------------
app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    try:
        # 1. RAG ingestion
        existing = await db.doc_chunks.count_documents({})
        if existing == 0:
            logger.info("doc_chunks empty — running seed ingestion.")
            res = await rag.reingest(db)
            logger.info("Startup ingestion complete: %s", res)
        else:
            await rag.ensure_index_loaded(db)
        # 2. Mock data seeding (idempotent)
        await mocks.seed_if_empty(db)
    except Exception:
        logger.exception("Startup initialization failed.")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
