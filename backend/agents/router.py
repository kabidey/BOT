"""Router agent — Phase 7 (Apr 2026).

Refactored from LLM-as-JSON-classifier to Hub AI native tool-calling.
The router defines one tool per intent; the model chooses ONE tool to call,
its name maps directly to the intent and its argument carries the `subject`.

Falls back to the legacy JSON-output classifier if the chosen model
returns no `tool_calls` (e.g. plain greeting that doesn't fit any tool).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from .llm import (
    LLMHUB_API_KEY,
    LLMHUB_BASE_URL,
    call_with_fallback,
    extract_reply,
)
from .directory_agent import DIRECTORY_TOOLS, DIRECTORY_TOOL_NAMES

logger = logging.getLogger(__name__)

INTENTS = {
    "KNOWLEDGE",        # general product / explainer / compliance — RAG
    "MARKET_DATA",      # live prices, NAV, fund performance — API agent
    "CLIENT_LOOKUP",    # user provides client code / wants to identify themselves — API agent
    "LEAD_CAPTURE",     # prospect interested in a product — Form agent
    "CALLBACK_REQUEST", # explicit "call me back" — Form agent (callback)
    "ESCALATION",       # frustration, complex/risky advice, out-of-scope — escalation card
    "SMALL_TALK",       # greetings, thanks
}

# Models with verified native tool_calls support on Hub AI (see HUB_AI_CAPABILITIES.md).
# We name them explicitly because `model:"auto"` falls through to gemma-4-e4b which
# emits raw chat-template tool tokens instead of a parsed tool_calls array.
ROUTER_TOOL_CHAIN: List[str] = [
    m.strip() for m in os.environ.get(
        "ROUTER_TOOL_CHAIN",
        "llama-3.3-70b-versatile,claude-haiku-4-5-20251001",
    ).split(",") if m.strip()
]

# The 7-intent toolbelt. Tool name = intent (lowercased). The router model picks one.
INTENT_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "answer_from_knowledge_base",
            "description": (
                "Answer a question about wealth-management products, regulations, taxation, "
                "compliance, KYC, or any factual/explanatory query that should be grounded in the "
                "SMIFS knowledge base. Examples: 'What is an AIF?', 'How are NCDs taxed?', 'What is KYC?'. "
                "Also use for any general factual question (weather, news, definitions) so the RAG "
                "agent can return grounded=false rather than hallucinate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Subject matter, e.g. 'AIF', 'NCD taxation', 'KYC'."},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_market_data",
            "description": (
                "Fetch a live price, NAV, or recent performance for a specific stock, ETF, or fund. "
                "Use only when the user names or strongly implies a specific instrument."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock symbol or fund name, e.g. 'RELIANCE', 'ICICI Bluechip'."},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_client",
            "description": (
                "Identify the user as an existing SMIFS client. Use when the user shares a client "
                "code matching SMIFS\\d+, a registered phone number, or asks about THEIR own portfolio/holdings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "Client code (e.g. 'SMIFS001') or phone number, if present."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_lead",
            "description": (
                "User has expressed concrete interest in investing in a SMIFS product (e.g. "
                "'I want to invest in NCDs', 'interested in the AIF'). Prefer this over "
                "answer_from_knowledge_base whenever the user's intent is clearly to invest."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_class": {"type": "string", "description": "e.g. 'AIF', 'NCD', 'PMS', 'Mutual Fund'."},
                },
                "required": ["asset_class"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_callback",
            "description": (
                "User explicitly asks to be called back, to speak to an advisor, or to schedule a meeting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "What the callback is about (optional)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate",
            "description": (
                "User is frustrated, asks for highly personalised advice that requires a human, "
                "or topic is clearly out-of-scope for a wealth advisor."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Brief reason for the escalation."},
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chitchat",
            "description": (
                "Pure greeting, thanks, or social chit-chat with no advisory content "
                "(e.g. 'Hello', 'Thanks', 'Good morning'). Use ONLY for true social pleasantries."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

TOOL_TO_INTENT: Dict[str, str] = {
    "answer_from_knowledge_base": "KNOWLEDGE",
    "fetch_market_data": "MARKET_DATA",
    "lookup_client": "CLIENT_LOOKUP",
    "capture_lead": "LEAD_CAPTURE",
    "request_callback": "CALLBACK_REQUEST",
    "escalate": "ESCALATION",
    "chitchat": "SMALL_TALK",
}
# Phase 8 — all directory_* tools map to a single intent carrying tool_name + args.
for _t in DIRECTORY_TOOLS:
    TOOL_TO_INTENT[_t["function"]["name"]] = "DIRECTORY_QUERY"

INTENTS.add("DIRECTORY_QUERY")

ROUTER_SYSTEM_TOOLS = (
    "You are the intent router for the Mackertich ONE Advisor (Mackertich ONE is the wealth-management vertical of SMIFS Ltd). "
    "Read the latest user message and decide which ONE specialist function to call. "
    "If the user expresses interest in investing in a product, prefer capture_lead over answer_from_knowledge_base. "
    "If the user asks about their own portfolio without sharing a code, call lookup_client with no arguments. "
    "Do not call multiple tools. Always call exactly one."
)

ROUTER_SYSTEM_TOOLS_EMPLOYEE = ROUTER_SYSTEM_TOOLS + (
    "\n\nThe verified user is an SMIFS EMPLOYEE. You also have LIVE access to the SMIFS staff "
    "directory via the directory_* tools. For any question about colleagues, teams, departments, "
    "locations, headcount, designations, reporting structure, or 'who reports to me / who do I report to', "
    "you MUST invoke the appropriate directory_* tool. Do NOT answer from memory. Do NOT paraphrase "
    "what you already know about the user. Prefer directory_my_team / directory_my_reporting_chain "
    "when the user talks about themselves ('my team', 'my manager', 'who reports to me'). Use "
    "directory_lookup_employee when they name a specific person, directory_search_employees for "
    "filtered lists, directory_org_stats for overall headcount numbers."
)

# Legacy JSON-output classifier prompt — kept as a fallback for cases where the
# tool-capable model returns no tool_call (rare; mostly bare punctuation).
ROUTER_SYSTEM_JSON = """You are the intent classifier for the Mackertich ONE Advisor (Mackertich ONE is the wealth-management vertical of SMIFS Ltd).
Classify the LATEST user message into EXACTLY ONE of: KNOWLEDGE, MARKET_DATA, CLIENT_LOOKUP, LEAD_CAPTURE, CALLBACK_REQUEST, ESCALATION, SMALL_TALK.
Respond with ONLY a JSON object: {"intent": "...", "confidence": 0.0-1.0, "rationale": "...", "subject": "..." or null}
"""


def _safe_json_parse(raw: str) -> Dict[str, Any]:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError(f"No JSON object in router output: {raw[:200]}")
    return json.loads(m.group(0))


def _extract_subject(tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    """Pull the most meaningful argument value to surface as `subject` to the orchestrator."""
    if not args:
        return None
    for key in ("topic", "symbol", "identifier", "asset_class", "reason"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


async def _classify_via_tools(message: str, history: List[Dict[str, Any]],
                              session_context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Try each tool-capable model in ROUTER_TOOL_CHAIN until one returns a usable tool_call.
    Returns {intent, confidence, rationale, subject, model, tool_name, tool_args} or None if all candidates failed.

    When `session_context.session_type == 'employee'` and auth_state == 'verified',
    the directory_* tools are added to the palette and the system prompt is extended.
    """
    if not LLMHUB_API_KEY or not LLMHUB_BASE_URL:
        return None

    # Phase 8 — dynamic tool palette
    is_verified_employee = bool(
        session_context
        and session_context.get("session_type") == "employee"
        and session_context.get("auth_state") == "verified"
    )
    tools = list(INTENT_TOOLS)
    system_prompt = ROUTER_SYSTEM_TOOLS
    if is_verified_employee:
        tools = INTENT_TOOLS + DIRECTORY_TOOLS
        system_prompt = ROUTER_SYSTEM_TOOLS_EMPLOYEE

    trimmed = history[-8:]
    convo_lines = "\n".join(f"{m['role'].upper()}: {m['content'][:400]}" for m in trimmed)
    user_block = (
        (f"PRIOR CONVERSATION:\n{convo_lines}\n\n" if convo_lines else "")
        + f"LATEST USER MESSAGE:\n{message}\n\n"
        + "Pick exactly one tool to call."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_block},
    ]
    url = f"{LLMHUB_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {LLMHUB_API_KEY}", "Content-Type": "application/json"}

    last_err: Optional[Exception] = None
    for model in ROUTER_TOOL_CHAIN:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 200,
            "tools": tools,
            "tool_choice": "auto",
        }
        try:
            from . import llm as _llm
            sem = _llm._get_hub_semaphore()
            async with sem:
                async with httpx.AsyncClient(timeout=30.0) as http:
                    # Retry transient errors with backoff
                    resp = None
                    for attempt in range(3):
                        resp = await http.post(url, headers=headers, json=payload)
                        if resp.status_code == 200:
                            break
                        if resp.status_code in (429, 502, 503, 504) and attempt < 2:
                            await asyncio.sleep(0.4 * (2 ** attempt))
                            continue
                        break
                    if resp is None or resp.status_code != 200:
                        logger.warning("Router tools [%s] HTTP %s — %s",
                                       model, resp.status_code if resp else "?",
                                       resp.text[:200] if resp else "")
                        continue
                    data = resp.json()
        except httpx.RequestError as e:
            logger.warning("Router tools [%s] network error: %s", model, e)
            last_err = e
            continue

        # Cost-ledger record (router task).
        try:
            from . import llm as _llm  # avoid cyclic import at module load
            if _llm._db_handle is not None:
                from cost_ledger import fire_and_forget_record
                fire_and_forget_record(
                    _llm._db_handle, task="router", session_id=None, intent="ROUTER",
                    data=data, request_model=model, local_latency_ms=int(data.get("latency_ms") or 0),
                )
        except Exception:
            pass

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            # Hub returned a plain text reply — caller will fall back to JSON classifier.
            logger.debug("Router tools [%s] returned no tool_calls; will try JSON fallback.", model)
            return None
        first = tool_calls[0]
        fn = (first.get("function") or {})
        tool_name = fn.get("name", "")
        args_str = fn.get("arguments", "{}") or "{}"
        try:
            args = json.loads(args_str) if isinstance(args_str, str) else (args_str or {})
        except Exception:
            args = {}
        intent = TOOL_TO_INTENT.get(tool_name)
        if not intent:
            logger.warning("Router tools [%s] returned unknown tool '%s' — falling back.", model, tool_name)
            return None
        # Mark this model as the most-recently-successful router so /api/health surfaces it.
        try:
            from . import llm as _llm
            _llm._LAST_OK["router"] = model
        except Exception:
            pass
        return {
            "intent": intent,
            "confidence": 0.95,  # tool-call selection is implicitly high-confidence
            "rationale": f"Hub native tool_call → {tool_name}",
            "subject": _extract_subject(tool_name, args),
            "model": data.get("model") or model,
            "tool_name": tool_name,
            "tool_args": args,
        }
    if last_err:
        logger.warning("Router tools chain exhausted: %s", last_err)
    return None


async def _classify_via_json(message: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Legacy LLM-as-JSON classifier — used as fallback when no tool_call was emitted."""
    trimmed = history[-8:]
    convo_lines = "\n".join(f"{m['role'].upper()}: {m['content'][:400]}" for m in trimmed)
    user_block = (
        (f"PRIOR CONVERSATION:\n{convo_lines}\n\n" if convo_lines else "")
        + f"LATEST USER MESSAGE:\n{message}\n\n"
        + "Output the JSON object now."
    )
    messages = [
        {"role": "system", "content": ROUTER_SYSTEM_JSON},
        {"role": "user", "content": user_block},
    ]
    try:
        result = await call_with_fallback(
            messages, task="router", temperature=0.0, max_tokens=200,
            response_format={"type": "json_object"}, intent="ROUTER",
        )
        parsed = _safe_json_parse(extract_reply(result["data"]))
    except Exception as e:
        logger.warning("Router JSON fallback failed (%s); defaulting to SMALL_TALK.", e)
        return {"intent": "SMALL_TALK", "confidence": 0.4,
                "rationale": f"Fallback after parse error: {e}", "subject": None, "model": None}
    intent = str(parsed.get("intent", "")).upper().strip()
    if intent not in INTENTS:
        intent = "KNOWLEDGE"
    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "intent": intent,
        "confidence": max(0.0, min(1.0, confidence)),
        "rationale": str(parsed.get("rationale", ""))[:300],
        "subject": parsed.get("subject"),
        "model": result.get("model"),
    }


async def classify(message: str, history: List[Dict[str, Any]],
                   session_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Returns {intent, confidence, rationale, subject, model, tool_name?, tool_args?}."""
    via_tools = await _classify_via_tools(message, history, session_context=session_context)
    if via_tools is not None:
        return via_tools
    return await _classify_via_json(message, history)
