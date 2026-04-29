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

# ---- Hub AI routing hints (re-probed Apr 2026 — see HUB_AI_CAPABILITIES.md) ----
# Hub recognises exactly one keyword today: prefer:"fast" → llama-3.3-70b-versatile (groq).
# Field & values are env-tunable so we can adopt new keywords as Hub expands its vocabulary.
HUB_HINT_FIELD = os.environ.get("HUB_HINT_FIELD", "routing_hint").strip()
HUB_HINT_CHAT_RAW = os.environ.get("HUB_HINT_CHAT", '{"prefer":"fast"}').strip()
HUB_HINT_ROUTER_RAW = os.environ.get("HUB_HINT_ROUTER", "").strip()


def _parse_hint(raw: str) -> Optional[Dict[str, Any]]:
    """Parse an env-supplied hint. Empty string means 'no hint'."""
    if not raw:
        return None
    try:
        import json as _json
        v = _json.loads(raw)
        return v if isinstance(v, dict) and v else None
    except Exception:
        logger.warning("Could not parse hint JSON: %s", raw)
        return None


HUB_HINT_CHAT = _parse_hint(HUB_HINT_CHAT_RAW)
HUB_HINT_ROUTER = _parse_hint(HUB_HINT_ROUTER_RAW)


def hint_for(task: str) -> Optional[Dict[str, Any]]:
    if task == "router":
        return HUB_HINT_ROUTER
    return HUB_HINT_CHAT

CHAT_CHAIN = [
    "auto",
    "llama-3.3-70b-versatile",
    "gemma-4-E4B",
    "claude-haiku-4-5-20251001",
]

ROUTER_CHAIN = [
    "auto",
    "llama-3.3-70b-versatile",
    "gemma-4-E4B",
    "claude-haiku-4-5-20251001",
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
                max_tokens: Optional[int], response_format: Optional[Dict[str, str]],
                context_chunks: Optional[List[Dict[str, Any]]] = None,
                routing_hint: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{LLMHUB_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {LLMHUB_API_KEY}", "Content-Type": "application/json"}
    payload: Dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if response_format is not None:
        payload["response_format"] = response_format
    if context_chunks:
        payload["context_chunks"] = context_chunks
    # Hub honours routing_hint:{"prefer":"fast"} only when model=="auto" — see HUB_AI_CAPABILITIES.md.
    # Sending it on a named model is harmless (silently echoed back as routing_resolved).
    if routing_hint and model == "auto":
        payload[HUB_HINT_FIELD] = routing_hint
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
    context_chunks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Try each model in the per-task chain; record cost on success."""
    chain = ROUTER_CHAIN if task == "router" else CHAT_CHAIN
    cached = _LAST_OK.get(task)
    ordered = ([cached] + [m for m in chain if m != cached]) if cached else list(chain)
    rh = hint_for(task)

    last_err: Optional[Exception] = None
    for model in ordered:
        t0 = time.monotonic()
        try:
            data = await _post(messages, model, temperature, max_tokens, response_format,
                               context_chunks=context_chunks, routing_hint=rh)
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
                    data = await _post(messages, model, temperature, max_tokens, None,
                                       context_chunks=context_chunks, routing_hint=rh)
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
                             max_tokens: Optional[int] = None,
                             context_chunks: Optional[List[Dict[str, Any]]] = None,
                             session_id: Optional[str] = None,
                             intent: Optional[str] = None) -> Dict[str, Any]:
    return await call_with_fallback(
        messages, task="chat", temperature=temperature, max_tokens=max_tokens,
        context_chunks=context_chunks, session_id=session_id, intent=intent,
    )


async def stream_chat_with_fallback(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.4,
    max_tokens: Optional[int] = None,
    context_chunks: Optional[List[Dict[str, Any]]] = None,
    session_id: Optional[str] = None,
    intent: Optional[str] = None,
):
    """Async generator yielding (event_type, payload) tuples:
       ('token', str), ('done', {'reply_text': ..., 'model': ..., 'data': {...minimal usage doc}})

    Tries each model in CHAT_CHAIN; first that opens an SSE stream wins.
    Tokens are streamed as they arrive. Final 'done' event carries full text + an
    OpenAI-style stub for cost-ledger compatibility (Hub does not return usage in
    streaming mode, so we estimate tokens locally).
    """
    chain = CHAT_CHAIN
    cached = _LAST_OK.get("chat")
    ordered = ([cached] + [m for m in chain if m != cached]) if cached else list(chain)
    rh = hint_for("chat")

    url = f"{LLMHUB_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {LLMHUB_API_KEY}", "Content-Type": "application/json"}

    last_err: Optional[Exception] = None
    for model in ordered:
        payload: Dict[str, Any] = {
            "model": model, "messages": messages, "temperature": temperature, "stream": True,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if context_chunks:
            payload["context_chunks"] = context_chunks
        if rh and model == "auto":
            payload[HUB_HINT_FIELD] = rh

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=60.0)) as http:
                async with http.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", "ignore")[:300]
                        logger.warning("Hub AI [stream] model %s failed: %s — %s", model, resp.status_code, body)
                        last_err = httpx.HTTPStatusError(f"{resp.status_code}", request=resp.request, response=resp)
                        continue
                    full_text_parts: List[str] = []
                    resolved_model: Optional[str] = None
                    saw_any_token = False
                    try:
                        async for raw_line in resp.aiter_lines():
                            if not raw_line:
                                continue
                            if raw_line.startswith(":"):
                                continue  # SSE comment / heartbeat
                            if not raw_line.startswith("data:"):
                                continue
                            data_str = raw_line[5:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = __import__("json").loads(data_str)
                            except Exception:
                                continue
                            resolved_model = resolved_model or chunk.get("model")
                            choices = chunk.get("choices") or []
                            if not choices:
                                continue
                            delta = (choices[0] or {}).get("delta") or {}
                            token = delta.get("content")
                            if token:
                                saw_any_token = True
                                full_text_parts.append(token)
                                yield ("token", token)
                    except (httpx.RemoteProtocolError, httpx.ReadError) as stream_close:
                        # Hub AI doesn't send a [DONE] sentinel — it closes the TCP
                        # connection after the final chunk. httpx surfaces this as
                        # "peer closed connection without sending complete message body".
                        # If we already received tokens, treat the close as a graceful
                        # end-of-stream; otherwise re-raise so we try the next model.
                        if not saw_any_token:
                            raise
                        logger.debug("Hub AI [stream] tcp close after %d tokens: %s",
                                     len(full_text_parts), stream_close)
                    full_text = "".join(full_text_parts)
                    _LAST_OK["chat"] = model
                    local_latency_ms = int((time.monotonic() - t0) * 1000)
                    # Estimate token counts (4 chars ≈ 1 token) for cost-ledger parity
                    prompt_chars = sum(len(m.get("content", "")) for m in messages) + sum(
                        len(c.get("text", "")) for c in (context_chunks or [])
                    )
                    est_in_tokens = max(1, prompt_chars // 4)
                    est_out_tokens = max(1, len(full_text) // 4)
                    stub_data = {
                        "model": resolved_model or model,
                        "choices": [{"message": {"role": "assistant", "content": full_text}}],
                        "usage": {
                            "prompt_tokens": est_in_tokens,
                            "completion_tokens": est_out_tokens,
                            "total_tokens": est_in_tokens + est_out_tokens,
                        },
                        "latency_ms": local_latency_ms,
                        "stream_estimated": True,
                    }
                    if _db_handle is not None:
                        cost_ledger.fire_and_forget_record(
                            _db_handle, task="chat", session_id=session_id, intent=intent,
                            data=stub_data, request_model=model, local_latency_ms=local_latency_ms,
                        )
                    yield ("done", {"reply_text": full_text, "model": resolved_model or model, "data": stub_data})
                    return
        except httpx.RequestError as e:
            logger.warning("Hub AI [stream] request error model=%s: %s", model, e)
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError("No models attempted in stream")


def extract_reply(data: Dict[str, Any]) -> str:
    return data["choices"][0]["message"]["content"]


def last_ok(task: str) -> Optional[str]:
    return _LAST_OK.get(task)
