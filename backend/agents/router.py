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
from .client_agent import CLIENT_TOOLS, CLIENT_TOOL_NAMES

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
    # ---------------- Phase 24c — BMIA-backed intents ----------------
    {
        "type": "function",
        "function": {
            "name": "bmia_compliance_research",
            "description": (
                "Use this WHENEVER the user asks about Indian financial-market regulators, "
                "regulatory rules, circulars, disclosures, compliance, KYC, insider trading "
                "(PIT — Prohibition of Insider Trading), takeover code, AML, listing obligations, "
                "mutual-fund regulations, derivatives segment rules, RBI/MCA actions, etc. "
                "Examples: 'SEBI insider trading disclosure timelines', 'PIT regulations', "
                "'RBI master circular on KYC', 'IRDAI norms for ULIPs'. Prefer this OVER "
                "answer_from_knowledge_base for any regulator-named or compliance-named query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language compliance question."},
                    "sources": {"type": "array",
                                 "items": {"type": "string", "enum": ["sebi", "rbi", "mca", "nse", "bse", "irdai"]},
                                 "description": "Optional regulator filter. Omit to search all."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bmia_fundamentals_lookup",
            "description": (
                "Use this WHEN the user asks about a specific listed Indian stock (NSE-listed) — "
                "fundamentals, P/E, EPS, profit & loss, balance sheet, cash flow, ratios, or "
                "quarterly trends. Examples: 'Tell me about Reliance fundamentals', "
                "'What is HDFCBANK P/E?', 'How is TCS doing?', 'Reliance Industries financials'. "
                "Extract the NSE ticker (e.g. 'Reliance Industries' → 'RELIANCE', 'HDFC Bank' → "
                "'HDFCBANK', 'Infosys' → 'INFY', 'State Bank' → 'SBIN') and pass it as `symbol`. "
                "Prefer this OVER fetch_market_data when the user asks about fundamentals (not "
                "just price). If you can't confidently resolve the ticker, still call this tool "
                "with your best guess — the branch handler will reply asking the user to confirm."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE ticker uppercase (e.g. RELIANCE)."},
                    "slice": {"type": "string",
                              "enum": ["profile", "quarterly", "trends", "ratios", "full"],
                              "default": "profile"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bmia_daily_briefing",
            "description": (
                "Use this WHEN the user asks about today's market events: board-meeting "
                "intimations, critical regulatory filings, or insider-trading disclosures "
                "across NSE/BSE-listed companies. Examples: 'What's happening in the market "
                "today?', 'Any critical filings this morning?', 'Today's announcements', "
                "'Show me today's market briefing'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string",
                             "description": "Optional YYYY-MM-DD. Omit for today's briefing."},
                    "sections": {
                        "type": "array",
                        "items": {"type": "string",
                                   "enum": ["board_meetings", "critical_filings", "insider_activity"]},
                        "description": "Optional filter."
                    },
                },
            },
        },
    },
    # ---------------- Phase 31 — BMIA research / paper-trading umbrella ----------------
    # Single umbrella tool that catches the 5 new BMIA endpoints (fund/decisions,
    # fund/portfolio/{name}, litmus/positions, litmus/cycles, litmus/summary).
    # The Phase-20 tools pipeline picks the right sub-tool via function-calling.
    {
        "type": "function",
        "function": {
            "name": "bmia_research_pipeline",
            "description": (
                "Use this WHEN the user asks about ANY of: (a) recent BMIA multi-agent "
                "consensus calls / research recommendations / BUY-SELL-HOLD picks / analyst "
                "verdicts; (b) a named model portfolio book (long_term, swing, intraday) — "
                "'show me the long-term portfolio', 'swing book composition', 'intraday "
                "holdings'; (c) Litmus paper-trading — open positions / MTM P&L / closed "
                "trade cycles / win rate / aggregate paper-trading stats. Examples: "
                "'recent BMIA consensus calls', 'top research picks this week', 'what's "
                "in the long-term portfolio', 'show the swing book', 'open paper trades', "
                "'litmus win rate', 'paper-trading scorecard', 'closed paper cycles P&L'. "
                "Do NOT use for individual stock fundamentals (→ bmia_fundamentals_lookup) "
                "nor for today's market briefing (→ bmia_daily_briefing)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string",
                              "description": "Short freeform tag of what the user asked "
                              "for, e.g. 'recent consensus calls', 'long-term portfolio', "
                              "'litmus win rate'."},
                },
                "required": [],
            },
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
    # Phase 24c — BMIA-backed intents
    "bmia_compliance_research": "BMIA_COMPLIANCE",
    "bmia_fundamentals_lookup": "BMIA_FUNDAMENTALS",
    "bmia_daily_briefing": "BMIA_BRIEFING",
    # Phase 31 — umbrella for the 5 new BMIA endpoints; orchestrator dispatches
    # this through the Phase 20 tools pipeline.
    "bmia_research_pipeline": "BMIA_TOOLS_PIPELINE",
}
# Phase 8 — all directory_* tools map to a single intent carrying tool_name + args.
for _t in DIRECTORY_TOOLS:
    TOOL_TO_INTENT[_t["function"]["name"]] = "DIRECTORY_QUERY"
# Phase 12 — all client_* tools map to CLIENT_QUERY (back-office + MF stacks).
for _t in CLIENT_TOOLS:
    TOOL_TO_INTENT[_t["function"]["name"]] = "CLIENT_QUERY"

INTENTS.add("DIRECTORY_QUERY")
INTENTS.add("CLIENT_QUERY")
# Phase 31 — BMIA tools-pipeline umbrella intent (fund decisions, fund portfolio,
# litmus positions / cycles / summary). Dispatched through the Phase-20 pipeline.
INTENTS.add("BMIA_TOOLS_PIPELINE")

ROUTER_SYSTEM_TOOLS = (
    "You are the intent router for the Mackertich ONE Advisor (Mackertich ONE is the wealth-management vertical of SMIFS Ltd). "
    "Read the latest user message and decide which ONE specialist function to call. "
    "If the user expresses interest in investing in a product, prefer capture_lead over answer_from_knowledge_base. "
    "If the user asks about their own portfolio without sharing a code, call lookup_client with no arguments. "
    "Do not call multiple tools. Always call exactly one."
)

ROUTER_SYSTEM_TOOLS_CLIENT = ROUTER_SYSTEM_TOOLS + (
    "\n\nThe verified user is an SMIFS CLIENT. The chat specialist has CLIENT_PROFILE "
    "in its system prompt containing ALL of the client's account fields (ucc, status, "
    "rm_name, rm_email, rm_mobile, risk_profile, segments, branch, city/state, occupation, "
    "income_range, POA, sub-broker, active_date, etc.).\n"
    "\n"
    "ROUTING RULES for CLIENT:\n"
    "1. If the question is about the CLIENT THEMSELVES ('my risk profile', 'who is my RM', "
    "'what segments am I active in', 'when did my account open', 'what is my branch', 'am I "
    "POA', etc.) → DO NOT call lookup_client. Route to answer_from_knowledge_base; the chat "
    "specialist will answer directly from CLIENT_PROFILE.\n"
    "2. lookup_client is ONLY for the FIRST turn after verification (to render the account "
    "summary card) or when the user EXPLICITLY asks 'show my account summary' / 'show my card'.\n"
    "3. For LIVE BACK-OFFICE / MF DATA questions, call the matching client_* tool — do NOT "
    "answer from CLIENT_PROFILE (which has identity fields only, no portfolio):\n"
    "   • 'my holdings' / 'portfolio' / 'shares I own' → client_portfolio\n"
    "   • 'account balance' / 'cash balance' / 'ledger' → client_ledger_balance\n"
    "   • 'recent trades' / 'last trade' / 'trade book' → client_recent_trades\n"
    "   • 'deposits' / 'withdrawals' / 'fund movement' → client_deposits_withdrawals\n"
    "   • 'mutual fund holdings' / 'MF folios' → client_mf_holdings\n"
    "   • 'my SIPs' → client_mf_sips\n"
    "4. For any PRODUCT / MARKET / RESEARCH question ('what's the minimum for X', 'NAV of Y', "
    "'returns on Z'), route to answer_from_knowledge_base — the chat specialist will emit "
    "the Wealth Manager fallback with the client's RM contact.\n"
    "5. capture_lead / request_callback are still valid if the client explicitly asks to "
    "be contacted about a NEW product.\n"
)

ROUTER_SYSTEM_TOOLS_EMPLOYEE = ROUTER_SYSTEM_TOOLS + (
    "\n\nThe verified user is an SMIFS EMPLOYEE. The chat specialist already has a USER_PROFILE "
    "object in its system prompt containing ALL of the user's own employment fields "
    "(employee_id, designation, department, manager, HRBP, HOD, location, office, tenure, "
    "date_of_joining, confirmation_status, on_notice, employment_status, employee_type, "
    "business_unit, company, CTC, cost centres, shift, weekly off, email on record, phone "
    "on record, etc.).\n"
    "\n"
    "ROUTING RULES:\n"
    "1. If the question is about the USER THEMSELVES (any 'my ...', 'when did I ...', 'am I ...', "
    "'where's my office', 'what's my HRBP', 'who is my manager', 'what's my CTC', 'who do I report to', "
    "'what's my designation / department / business unit / email / phone / shift', etc.), DO NOT call "
    "a directory_* tool — the answer is already in USER_PROFILE. Route to answer_from_knowledge_base "
    "(KNOWLEDGE) or chitchat (SMALL_TALK); the chat specialist will answer directly. "
    "CRITICAL: 'my HRBP', 'my manager', 'who do I report to' — these are SELF-queries; do NOT use "
    "directory_field_value with the user's own name — route to answer_from_knowledge_base.\n"
    "2. If the question is about a SPECIFIC OTHER person, call directory_lookup_employee "
    "(whole profile) or directory_field_value (one field).\n"
    "3. If the question is about 'my team' / 'who reports to me' → directory_my_team.\n"
    "4. If the question mentions 'reporting chain', 'who do I report to and who does my manager "
    "report to', 'two levels up', 'my chain', 'skip-level manager', 'all the way up' → ALWAYS "
    "directory_my_reporting_chain. Never use directory_field_value for multi-level reporting "
    "queries about the user.\n"
    "5. For a list of people by filters (department, location, status, notice, absconding, tenure, "
    "confirmation), use directory_search_employees or the more specific directory_filter_by_status / "
    "directory_recent_joins / directory_upcoming_confirmations / directory_by_tenure. "
    "For tenure queries like 'longest-tenured', 'who's been here longest', 'newest hires by tenure', "
    "'top N by tenure', ALWAYS use directory_by_tenure.\n"
    "6. For counts grouped by a field ('gender breakdown', 'headcount by department'), use "
    "directory_aggregate.\n"
    "7. For overall SMIFS stats (total employees, total departments/locations) use directory_org_stats.\n"
    "8. For enumeration of all departments / locations / designations at SMIFS ('what departments "
    "exist', 'list the offices', 'which designations are there'), ALWAYS use the respective list "
    "tools (directory_departments / directory_locations / directory_designations) — never route to "
    "KNOWLEDGE or answer from memory.\n"
    "9. directory_field_value is ONLY for a SPECIFIC field of a SPECIFIC named OTHER person. "
    "Never use it for the verified user's own fields (those come from USER_PROFILE) and never use "
    "it for multi-level reporting chain questions.\n"
    "10. NEVER fabricate names, designations, departments, dates, compensation, or reporting lines.\n"
    "11. NEVER say 'I don't know' without either consulting USER_PROFILE (for self-queries) or "
    "calling the most specific tool (for about-others).\n"
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
    is_verified_client = bool(
        session_context
        and session_context.get("session_type") == "client"
        and session_context.get("auth_state") == "verified"
    )
    tools = list(INTENT_TOOLS)
    system_prompt = ROUTER_SYSTEM_TOOLS
    if is_verified_employee:
        tools = INTENT_TOOLS + DIRECTORY_TOOLS
        system_prompt = ROUTER_SYSTEM_TOOLS_EMPLOYEE
    elif is_verified_client:
        tools = INTENT_TOOLS + CLIENT_TOOLS
        system_prompt = ROUTER_SYSTEM_TOOLS_CLIENT

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
