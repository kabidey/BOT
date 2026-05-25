"""Phase 20 — Question Analyzer.

A single small-model Hub AI call BEFORE the orchestrator picks tools.
Classifies the user's question across:

    entity_type:   employee | client | vehicle | transaction | aggregate | generic
    operation:    lookup   | list   | aggregate | compare | trend | explain | refuse
    output_hint:  single_fact | card | table | chart | image | narrative | refusal
    tool_hint:    list of 3-5 likely tool names from the registry
    language:     en | hi | bn | ta | …

Cost: ~200 input + 80 output tokens at gpt-4o-mini ≈ $0.0001/turn.

If the model errors or returns malformed JSON, we return a `confidence=0`
fallback so the orchestrator can short-circuit to the legacy path.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents import llm as _llm

logger = logging.getLogger(__name__)

_ANALYZER_MODEL = "gpt-4o-mini"

_SYS = """You are the Question Analyzer for the SMIFS Wealth chatbot. Classify the user's question into a strict JSON envelope. Output ONLY the JSON object, no surrounding prose.

Schema:
{
  "entity_type":  "employee | client | vehicle | transaction | aggregate | generic",
  "operation":    "lookup | list | aggregate | compare | trend | explain | refuse",
  "output_hint":  "single_fact | card | table | chart | image | narrative | refusal",
  "tool_hint":    ["tool_name_1", "tool_name_2", "tool_name_3"],
  "language":     "en | hi | bn | ta | te | mr",
  "confidence":   0.0 .. 1.0
}

Tools available (pick from these names only):
- firm_stats, client_corpus_stats, bo_stats, mf_stats
- departments_list, locations_list, designations_list
- employee_by_code, employee_search, org_tree
- bo_client_by_ucc, bo_client_360, bo_client_portfolio
- bo_client_ledger_balance, bo_client_trade_book, bo_client_charges
- bo_client_deposits, bo_client_withdrawals, bo_clients_by_rm
- mf_client_by_pan, mf_client_by_uid, mf_client_folios
- mf_client_transactions, mf_client_sips, mf_clients_by_rm
- client_by_ucc, clients_search

Disambiguation rules (CRITICAL):
- "my clients", "show my MF clients", "list my BO clients" → tool_hint=['mf_clients_by_rm','bo_clients_by_rm'] (the RM-filtered lists, NOT by-pan)
- "my SIPs", "my MF transactions", "my portfolio" (client role) → tool_hint=['mf_client_folios','mf_client_sips','mf_client_transactions']
- "my ledger balance", "my trade book", "my deposits" (client role) → tool_hint=['bo_client_ledger_balance','bo_client_trade_book','bo_client_deposits']
- "find <client name>" or "client by UCC X" → tool_hint=['clients_search','client_by_ucc','bo_client_by_ucc']
- "compare client X and Y" → tool_hint=['bo_client_portfolio','bo_client_360','clients_search']
- "<person>'s designation/department/manager" → tool_hint=['employee_by_code','employee_search']
- "show team / org chart / reporting structure" → output_hint=image, tool_hint=['org_tree','employee_by_code']
- "asset allocation / portfolio split / debt vs equity" (client role) → output_hint=image, tool_hint=['mf_client_by_pan']
- "compare X vs Y" / "comparison" → operation=compare, output_hint=chart (or table if many fields)
- "show me a list / all / every" → output_hint=table
- "how does X break down" / "distribution / mix" → output_hint=chart
- "trend / over time / monthly" → output_hint=chart
- "salary / CTC of someone else" / "PAN/Aadhaar/bank of someone else" → output_hint=refusal
- single fact ("what's my balance", "his designation") → output_hint=single_fact

Pick at most 3 tool_hints. If the question doesn't fit any tool, return tool_hint: [].
"""


async def classify(user_message: str, *, role: str, session_id: Optional[str] = None,
                   db=None) -> Dict[str, Any]:
    """Returns the classification envelope. Never raises."""
    t0 = time.monotonic()
    fallback = {
        "entity_type": "generic", "operation": "explain",
        "output_hint": "narrative", "tool_hint": [], "language": "en",
        "confidence": 0.0,
    }
    if not user_message or not user_message.strip():
        return {**fallback, "confidence": 0.0, "error": "empty_message"}

    try:
        res = await _llm.call_with_fallback(
            messages=[
                {"role": "system", "content": _SYS},
                {"role": "user", "content": f"Role: {role}\nQuestion: {user_message.strip()[:1200]}"},
            ],
            task="router",
            temperature=0.0,
            max_tokens=180,
            response_format={"type": "json_object"},
            session_id=session_id,
            intent="question_analyzer",
        )
        body = (res.get("data") or {}).get("choices", [{}])[0].get("message", {}).get("content", "{}")
        try:
            parsed = json.loads(body)
        except Exception:
            return {**fallback, "confidence": 0.0, "error": "json_parse_failed"}
        # Normalise + cap.
        envelope = {
            "entity_type": str(parsed.get("entity_type", "generic")).lower(),
            "operation":   str(parsed.get("operation", "explain")).lower(),
            "output_hint": str(parsed.get("output_hint", "narrative")).lower(),
            "tool_hint":   [str(t) for t in (parsed.get("tool_hint") or [])][:5],
            "language":    str(parsed.get("language", "en")).lower()[:2],
            "confidence":  float(parsed.get("confidence", 0.5)),
            "latency_ms":  int((time.monotonic() - t0) * 1000),
            "model":       res.get("model"),
        }
        # Telemetry.
        if db is not None:
            try:
                await db.question_analyzer_calls.insert_one({
                    "session_id": session_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "role": role,
                    "message_len": len(user_message),
                    **{k: envelope[k] for k in
                       ("entity_type", "operation", "output_hint",
                        "tool_hint", "language", "confidence", "latency_ms", "model")},
                })
            except Exception:
                logger.exception("question_analyzer_calls insert failed (non-fatal)")
        return envelope
    except Exception as e:
        logger.exception("question_analyzer classify failed")
        return {**fallback, "confidence": 0.0, "error": f"unhandled:{type(e).__name__}"}
