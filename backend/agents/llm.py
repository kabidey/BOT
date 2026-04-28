"""Hub AI client shared across agents (Phase 2).

Per-task fallback chains and per-task last-successful caching, since model
permissions and reliability differ between providers.
"""
from __future__ import annotations
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

LLMHUB_API_KEY = os.environ.get("LLMHUB_API_KEY", "")
LLMHUB_BASE_URL = os.environ.get("LLMHUB_BASE_URL", "").rstrip("/")

# Probed Feb 2026: Hub AI key has provider permissions for openai + anthropic + groq +
# gemma4-local. Note that some claude IDs are silently re-routed to llama by the Hub.

# General chat / RAG / small-talk fallback chain — quality first.
CHAT_CHAIN = [
    "gpt-4o-mini",
    "claude-haiku-4-5-20251001",
    "claude-3-5-sonnet-20241022",
    "llama-3.3-70b-versatile",
    "gemma-4-E4B",
    "auto",
]

# Router / intent classification — needs reliable structured JSON output, so we put
# OpenAI and Claude Haiku at the front (both excel at constrained JSON).
ROUTER_CHAIN = [
    "gpt-4o-mini",
    "claude-haiku-4-5-20251001",
    "llama-3.3-70b-versatile",
    "gemma-4-E4B",
    "auto",
]

# Module-level per-task last-successful caches (separate so router can converge on a
# different model than chat without invalidating the chat cache).
_LAST_OK: Dict[str, str] = {}


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
) -> Dict[str, Any]:
    """Try each model in the per-task chain until one returns 2xx. Returns
    {"data": ..., "model": <requested model id>}.

    Caches last-successful per task so we don't waste calls on every request.
    """
    chain = ROUTER_CHAIN if task == "router" else CHAT_CHAIN
    cached = _LAST_OK.get(task)
    ordered = ([cached] + [m for m in chain if m != cached]) if cached else list(chain)

    last_err: Optional[Exception] = None
    for model in ordered:
        try:
            data = await _post(messages, model, temperature, max_tokens, response_format)
            _LAST_OK[task] = model
            return {"data": data, "model": model}
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300] if e.response is not None else ""
            logger.warning("Hub AI [%s] model %s failed: %s — %s", task, model,
                           e.response.status_code, body)
            last_err = e
            # If response_format is the cause and this is the only fail, retry once without it.
            if response_format and "response_format" in body:
                try:
                    data = await _post(messages, model, temperature, max_tokens, None)
                    _LAST_OK[task] = model
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


# Backward-compat wrapper for code paths that imported chat_with_fallback in Phase 1.
async def chat_with_fallback(messages: List[Dict[str, str]], temperature: float = 0.4,
                             max_tokens: Optional[int] = None) -> Dict[str, Any]:
    return await call_with_fallback(messages, task="chat", temperature=temperature, max_tokens=max_tokens)


def extract_reply(data: Dict[str, Any]) -> str:
    return data["choices"][0]["message"]["content"]


def last_ok(task: str) -> Optional[str]:
    return _LAST_OK.get(task)
