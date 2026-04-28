from fastapi import FastAPI, APIRouter, HTTPException, Header, Depends
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import uuid
import httpx
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

import rag

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Hub AI config
LLMHUB_API_KEY = os.environ['LLMHUB_API_KEY']
LLMHUB_BASE_URL = os.environ['LLMHUB_BASE_URL'].rstrip('/')
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', '')

# Model fallback chain. Hub AI key for SMIFS routes via "auto" to gemma4-local.
# Explicit OpenAI/Anthropic names kept as fallbacks for future provider permissions.
MODEL_CANDIDATES = ["auto", "gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet"]

# RAG retrieval thresholds
RAG_TOP_K = 5
RAG_MIN_SCORE = 0.25  # below this, treat as out-of-knowledge-base
RAG_HISTORY_TURNS = 10  # max prior turns sent to LLM

BASE_SYSTEM_PROMPT = (
    "You are the Lead Wealth-Engagement Agent for SMIFS Management Services Limited. "
    "Maintain a sophisticated, precise, empathetic, and professional tone — the voice of a "
    "high-level wealth manager. Keep replies concise, considered, and free of marketing fluff. "
    "If you do not yet have enough information about a client's goals, ask one clarifying "
    "question at a time."
)

GROUNDED_INSTRUCTIONS = (
    "\n\nThe following internal SMIFS knowledge base passages are provided to ground your reply. "
    "Use ONLY these passages for product facts (figures, regulations, fees, taxation, processes). "
    "Do NOT invent SMIFS-specific facts that are not in the passages. If the passages do not contain "
    "the answer, say so plainly and offer to connect the client with a human advisor. "
    "Do not enumerate or quote citation IDs in your reply — citations are surfaced separately."
)

OUT_OF_KB_INSTRUCTIONS = (
    "\n\nThe internal SMIFS knowledge base does not contain a confident match for this query. "
    "Acknowledge the limit briefly, do NOT fabricate SMIFS-specific facts, and offer to connect "
    "the client with a human advisor. You may speak in general financial-literacy terms if "
    "appropriate, but never attribute specifics to SMIFS unless they are in the knowledge base."
)


# ---------------- FastAPI app ----------------
app = FastAPI(
    title="SMIFS Wealth-Engagement Agent",
    description="Phase 1 — Grounded chat with RAG over SMIFS product literature.",
    version="0.2.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ---------------- Models ----------------
class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str = Field(..., min_length=1)


class Citation(BaseModel):
    doc_id: str
    doc_title: str
    section: str
    score: float
    text: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    model: str
    grounded: bool
    citations: List[Citation] = []


class HealthResponse(BaseModel):
    status: str
    llm_reachable: bool
    model: Optional[str] = None
    detail: Optional[str] = None
    rag_chunks: int = 0
    embedder: Optional[str] = None


class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=RAG_TOP_K, ge=1, le=20)


class RagSearchHit(BaseModel):
    doc_id: str
    doc_title: str
    section: str
    score: float
    text: str


# ---------------- Hub AI client ----------------
async def call_hub_ai(messages: List[Dict[str, str]], model: str) -> Dict[str, Any]:
    url = f"{LLMHUB_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {LLMHUB_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": 0.4}
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


async def chat_with_fallback(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Try each model in MODEL_CANDIDATES until one returns 2xx. Caches last-successful."""
    cached = getattr(chat_with_fallback, "_last_ok", None)
    chain = [cached] + [m for m in MODEL_CANDIDATES if m != cached] if cached else list(MODEL_CANDIDATES)
    last_err: Optional[Exception] = None
    for model in chain:
        try:
            data = await call_hub_ai(messages, model)
            chat_with_fallback._last_ok = model  # type: ignore[attr-defined]
            return {"data": data, "model": model}
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300] if e.response is not None else ""
            logger.warning("Hub AI model %s failed: %s — %s", model, e.response.status_code, body)
            last_err = e
            if e.response is not None and e.response.status_code == 401:
                raise
        except httpx.RequestError as e:
            logger.warning("Hub AI request error for model %s: %s", model, e)
            last_err = e
    if last_err:
        raise last_err
    raise RuntimeError("No models attempted")


# ---------------- Conversation persistence ----------------
async def get_or_create_session(session_id: Optional[str]) -> Dict[str, Any]:
    if session_id:
        existing = await db.conversations.find_one({"session_id": session_id}, {"_id": 0})
        if existing:
            return existing
    new_id = session_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    doc = {"session_id": new_id, "created_at": now, "updated_at": now, "messages": []}
    await db.conversations.insert_one(dict(doc))
    return doc


async def append_messages(session_id: str, new_msgs: List[Dict[str, Any]]):
    now = datetime.now(timezone.utc).isoformat()
    stamped = [{**m, "ts": now} for m in new_msgs]
    await db.conversations.update_one(
        {"session_id": session_id},
        {"$push": {"messages": {"$each": stamped}}, "$set": {"updated_at": now}},
    )


# ---------------- Auth helper ----------------
def require_admin(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")
    return True


# ---------------- Routes ----------------
@api_router.get("/")
async def root():
    return {"service": "SMIFS Wealth-Engagement Agent", "phase": 1}


@api_router.get("/health", response_model=HealthResponse)
async def health():
    """Tiny LLM ping + RAG index status."""
    chunk_count = await rag.ensure_index_loaded(db)
    ping_msgs = [
        {"role": "system", "content": "Respond with the single word: ok"},
        {"role": "user", "content": "ping"},
    ]
    try:
        result = await chat_with_fallback(ping_msgs)
        resolved = result["data"].get("model") or result["model"]
        return HealthResponse(
            status="ok",
            llm_reachable=True,
            model=resolved,
            rag_chunks=chunk_count,
            embedder=rag.EMBEDDER_KIND,
        )
    except httpx.HTTPStatusError as e:
        body = e.response.text if e.response is not None else ""
        return HealthResponse(
            status="ok",
            llm_reachable=False,
            detail=f"HTTP {e.response.status_code}: {body[:200]}",
            rag_chunks=chunk_count,
            embedder=rag.EMBEDDER_KIND,
        )
    except Exception as e:
        return HealthResponse(
            status="ok",
            llm_reachable=False,
            detail=str(e)[:200],
            rag_chunks=chunk_count,
            embedder=rag.EMBEDDER_KIND,
        )


@api_router.post("/admin/reingest")
async def admin_reingest(_: bool = Depends(require_admin)):
    return await rag.reingest(db)


@api_router.post("/rag/search", response_model=List[RagSearchHit])
async def rag_search(req: RagSearchRequest):
    await rag.ensure_index_loaded(db)
    hits = await rag.search(req.query, top_k=req.top_k)
    return hits


@api_router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    await rag.ensure_index_loaded(db)
    session = await get_or_create_session(req.session_id)
    sid = session["session_id"]

    # 1. Retrieve top-k chunks for the user query
    hits = await rag.search(req.message, top_k=RAG_TOP_K)
    grounded = bool(hits) and any(h["score"] >= RAG_MIN_SCORE for h in hits)

    # 2. Build system prompt
    if grounded:
        kb_block = "\n\n".join(
            f"[{i+1}] ({h['doc_title']} · §{h['section']})\n{h['text']}"
            for i, h in enumerate(hits) if h["score"] >= RAG_MIN_SCORE
        )
        system_content = (
            BASE_SYSTEM_PROMPT
            + GROUNDED_INSTRUCTIONS
            + "\n\n--- KNOWLEDGE BASE ---\n"
            + kb_block
            + "\n--- END KNOWLEDGE BASE ---"
        )
    else:
        system_content = BASE_SYSTEM_PROMPT + OUT_OF_KB_INSTRUCTIONS

    # 3. Build messages: system + last N turns + new user
    history_msgs = session.get("messages", [])
    trimmed = history_msgs[-(RAG_HISTORY_TURNS * 2):]  # user+assistant per turn
    history = [{"role": m["role"], "content": m["content"]} for m in trimmed]
    messages = [{"role": "system", "content": system_content}] + history + [
        {"role": "user", "content": req.message}
    ]

    # 4. LLM call
    try:
        result = await chat_with_fallback(messages)
    except httpx.HTTPStatusError as e:
        body = e.response.text if e.response is not None else ""
        logger.error("Hub AI failure: %s %s", e.response.status_code, body)
        raise HTTPException(status_code=502, detail=f"Hub AI error ({e.response.status_code}): {body[:300]}")
    except Exception as e:
        logger.exception("Hub AI request failed")
        raise HTTPException(status_code=502, detail=f"Hub AI request failed: {e}")

    data = result["data"]
    model_used = data.get("model") or result["model"]
    try:
        reply = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise HTTPException(status_code=502, detail=f"Unexpected Hub AI response shape: {data}")

    # 5. Build citations (top 3 above threshold)
    citations: List[Citation] = []
    if grounded:
        for h in hits[:3]:
            if h["score"] >= RAG_MIN_SCORE:
                citations.append(Citation(
                    doc_id=h["doc_id"],
                    doc_title=h["doc_title"],
                    section=h["section"],
                    score=round(h["score"], 4),
                    text=h["text"],
                ))

    # 6. Persist
    await append_messages(
        sid,
        [
            {"role": "user", "content": req.message},
            {
                "role": "assistant",
                "content": reply,
                "model": model_used,
                "grounded": grounded,
                "citations": [c.model_dump() for c in citations],
            },
        ],
    )

    return ChatResponse(
        session_id=sid,
        reply=reply,
        model=model_used,
        grounded=grounded,
        citations=citations,
    )


@api_router.get("/conversations/{session_id}")
async def get_conversation(session_id: str):
    doc = await db.conversations.find_one({"session_id": session_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    return doc


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
    """Auto-ingest seed docs if doc_chunks is empty; otherwise just load index."""
    try:
        existing = await db.doc_chunks.count_documents({})
        if existing == 0:
            logger.info("doc_chunks is empty — running seed ingestion.")
            res = await rag.reingest(db)
            logger.info("Startup ingestion complete: %s", res)
        else:
            logger.info("doc_chunks already populated (%d) — loading index.", existing)
            await rag.ensure_index_loaded(db)
    except Exception:
        logger.exception("Startup RAG initialization failed; chat will run without grounding.")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
