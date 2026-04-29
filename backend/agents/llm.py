"""Hub AI client shared across agents (Phase 2-4).

Per-task fallback chains, per-task last-successful caching, AND cost-ledger
recording on every successful call (Phase 4).
"""
from __future__ import annotations
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

import cost_ledger

logger = logging.getLogger(__name__)

LLMHUB_API_KEY = os.environ.get("LLMHUB_API_KEY", "")
LLMHUB_BASE_URL = os.environ.get("LLMHUB_BASE_URL", "").rstrip("/")

CHAT_CHAIN = [
    "gemma-4-E4B",
    "llama-3.3-70b-versatile",
    "claude-haiku-4-5-20251001",
    "auto",
]

ROUTER_CHAIN = [
    "llama-3.3-70b-versatile",
    "gemma-4-E4B",
    "claude-haiku-4-5-20251001",
    "auto",
]

_LAST_OK: Dict[str, str] = {}


def reset_cache() -> None:
    """Wipe the per-task cached primary so the chain head is tried first.
    Called at startup whenever the chain configuration changes."""
    _LAST_OK.clear()


# Module-level DB binding so the LLM module can record cost without circular imports.
_db_handle = None


def bind_db(db) -> None:
    """Called once at startup by server.py."""
    global _db_handle
    _db_handle = db


async def _post(messages: List[Dict[str, str]], model: str, temperature: float,
                max_tokens: Optional[int], response_format: Optional[Dict[str, str]]) -> Dict[str, Any]:
    url = f"{LLMHUB_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {LLMHUB_API_KEY}", "Content-Type": "application/json"}
    payload: Dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if response_format is not None:
        payload["response_format"] = response_format
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


async def call_with_fallback(
    messages: List[Dict[str, str]],
    *,
    task: str = "chat",
    temperature: float = 0.4,
    max_tokens: Optional[int] = None,
    response_format: Optional[Dict[str, str]] = None,
    session_id: Optional[str] = None,
    intent: Optional[str] = None,
) -> Dict[str, Any]:
    """Try each model in the per-task chain; record cost on success."""
    chain = ROUTER_CHAIN if task == "router" else CHAT_CHAIN
    cached = _LAST_OK.get(task)
    ordered = ([cached] + [m for m in chain if m != cached]) if cached else list(chain)

    last_err: Optional[Exception] = None
    for model in ordered:
        t0 = time.monotonic()
        try:
            data = await _post(messages, model, temperature, max_tokens, response_format)
            _LAST_OK[task] = model
            local_latency_ms = int((time.monotonic() - t0) * 1000)
            if _db_handle is not None:
                cost_ledger.fire_and_forget_record(
                    _db_handle, task=task, session_id=session_id, intent=intent,
                    data=data, request_model=model, local_latency_ms=local_latency_ms,
                )
            return {"data": data, "model": model}
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300] if e.response is not None else ""
            logger.warning("Hub AI [%s] model %s failed: %s — %s", task, model,
                           e.response.status_code, body)
            last_err = e
            if response_format and "response_format" in body:
                try:
                    data = await _post(messages, model, temperature, max_tokens, None)
                    _LAST_OK[task] = model
                    local_latency_ms = int((time.monotonic() - t0) * 1000)
                    if _db_handle is not None:
                        cost_ledger.fire_and_forget_record(
                            _db_handle, task=task, session_id=session_id, intent=intent,
                            data=data, request_model=model, local_latency_ms=local_latency_ms,
                        )
                    return {"data": data, "model": model}
                except Exception as e2:
                    last_err = e2
            if e.response is not None and e.response.status_code == 401:
                raise
        except httpx.RequestError as e:
            logger.warning("Hub AI [%s] request error model=%s: %s", task, model, e)
            last_err = e
    if last_err:
        raise last_err
    raise RuntimeError("No models attempted")


async def chat_with_fallback(messages: List[Dict[str, str]], temperature: float = 0.4,
                             max_tokens: Optional[int] = None) -> Dict[str, Any]:
    return await call_with_fallback(messages, task="chat", temperature=temperature, max_tokens=max_tokens)


def extract_reply(data: Dict[str, Any]) -> str:
    return data["choices"][0]["message"]["content"]


def last_ok(task: str) -> Optional[str]:
    return _LAST_OK.get(task)
