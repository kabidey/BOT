from fastapi import FastAPI, APIRouter, HTTPException
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


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Hub AI config
LLMHUB_API_KEY = os.environ['LLMHUB_API_KEY']
LLMHUB_BASE_URL = os.environ['LLMHUB_BASE_URL'].rstrip('/')

# Model fallback chain (try in order). The Hub AI key for SMIFS routes via "auto"
# to whichever provider is enabled (currently gemma4-local). Explicit OpenAI/Anthropic
# names are kept as fallbacks in case provider permissions change later.
MODEL_CANDIDATES = ["auto", "gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet"]

SYSTEM_PROMPT = (
    "You are the Lead Wealth-Engagement Agent for SMIFS Management Services Limited. "
    "Maintain a sophisticated, precise, empathetic, and professional tone — the voice of a "
    "high-level wealth manager. Keep replies concise, considered, and free of marketing fluff. "
    "If you do not yet have enough information about a client's goals, ask one clarifying "
    "question at a time."
)

# FastAPI app
app = FastAPI(
    title="SMIFS Wealth-Engagement Agent",
    description="Phase 0 — Minimal multi-agent chat backed by Hub AI.",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
api_router = APIRouter(prefix="/api")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# -------------------- Models --------------------
class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    model: str


class HealthResponse(BaseModel):
    status: str
    llm_reachable: bool
    model: Optional[str] = None
    detail: Optional[str] = None


# -------------------- Hub AI client --------------------
async def call_hub_ai(messages: List[Dict[str, str]], model: str) -> Dict[str, Any]:
    """Call Hub AI chat-completions. Raises httpx.HTTPStatusError on non-2xx."""
    url = f"{LLMHUB_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LLMHUB_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
    }
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


async def chat_with_fallback(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Try each model in MODEL_CANDIDATES until one returns 2xx.
    Caches the last-successful model on the function to avoid repeating known-failed
    provider permission checks (~2s saved per request)."""
    # Promote last successful model to the front of the chain
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
            # 401 = bad key globally; no point trying other models. Other 4xx (402 wallet,
            # 403 provider-not-allowed) may be model/provider-specific, so keep trying.
            if e.response is not None and e.response.status_code == 401:
                raise
        except httpx.RequestError as e:
            logger.warning("Hub AI request error for model %s: %s", model, e)
            last_err = e
    if last_err:
        raise last_err
    raise RuntimeError("No models attempted")


# -------------------- Conversation persistence --------------------
async def get_or_create_session(session_id: Optional[str]) -> Dict[str, Any]:
    if session_id:
        existing = await db.conversations.find_one({"session_id": session_id}, {"_id": 0})
        if existing:
            return existing
    new_id = session_id or str(uuid.uuid4())
    doc = {
        "session_id": new_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "messages": [],  # list of {role, content, ts}
    }
    await db.conversations.insert_one(dict(doc))
    return doc


async def append_messages(session_id: str, new_msgs: List[Dict[str, str]]):
    now = datetime.now(timezone.utc).isoformat()
    await db.conversations.update_one(
        {"session_id": session_id},
        {
            "$push": {"messages": {"$each": [{**m, "ts": now} for m in new_msgs]}},
            "$set": {"updated_at": now},
        },
    )


# -------------------- Routes --------------------
@api_router.get("/")
async def root():
    return {"service": "SMIFS Wealth-Engagement Agent", "phase": 0}


@api_router.get("/health", response_model=HealthResponse)
async def health():
    """Tiny ping to Hub AI to verify the key works."""
    ping_msgs = [
        {"role": "system", "content": "Respond with the single word: ok"},
        {"role": "user", "content": "ping"},
    ]
    try:
        result = await chat_with_fallback(ping_msgs)
        resolved = result["data"].get("model") or result["model"]
        return HealthResponse(status="ok", llm_reachable=True, model=resolved)
    except httpx.HTTPStatusError as e:
        body = e.response.text if e.response is not None else ""
        return HealthResponse(
            status="ok",
            llm_reachable=False,
            detail=f"HTTP {e.response.status_code}: {body[:200]}",
        )
    except Exception as e:
        return HealthResponse(status="ok", llm_reachable=False, detail=str(e)[:200])


@api_router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    session = await get_or_create_session(req.session_id)
    sid = session["session_id"]

    # Build message list: system + history + new user msg
    history = [{"role": m["role"], "content": m["content"]} for m in session.get("messages", [])]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [
        {"role": "user", "content": req.message}
    ]

    try:
        result = await chat_with_fallback(messages)
    except httpx.HTTPStatusError as e:
        body = e.response.text if e.response is not None else ""
        logger.error("Hub AI failure: %s %s", e.response.status_code, body)
        raise HTTPException(
            status_code=502,
            detail=f"Hub AI error ({e.response.status_code}): {body[:300]}",
        )
    except Exception as e:
        logger.exception("Hub AI request failed")
        raise HTTPException(status_code=502, detail=f"Hub AI request failed: {e}")

    data = result["data"]
    # Prefer the resolved model name from Hub AI (e.g. "gemma-4-E4B") over our request label ("auto")
    model_used = data.get("model") or result["model"]
    try:
        reply = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise HTTPException(status_code=502, detail=f"Unexpected Hub AI response shape: {data}")

    await append_messages(
        sid,
        [
            {"role": "user", "content": req.message},
            {"role": "assistant", "content": reply},
        ],
    )

    return ChatResponse(session_id=sid, reply=reply, model=model_used)


@api_router.get("/conversations/{session_id}")
async def get_conversation(session_id: str):
    doc = await db.conversations.find_one({"session_id": session_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    return doc


# Include router & CORS
app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
