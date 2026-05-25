"""Phase 20 — Tool-aware turn orchestrator.

Runs ONLY when `PHASE_20_TOOLS_ENABLED=true`. Lifecycle:

    user message
        ↓
    Question Analyzer (gpt-4o-mini)  →  classification envelope
        ↓
    Registry.select(role, tool_hints)  →  3-8 candidate tools
        ↓
    Hub AI chat/completions with `tools=[...]`  (function-calling)
        ↓ (zero-or-more tool_calls; up to 5 sequential rounds)
    adapter.execute(...) in parallel for each round
        ↓
    Hub AI second pass with tool outputs + forced output-format
        ↓
    blocks[] handed back to the parent orchestrator
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents import llm as _llm

from . import adapter as _adapter
from . import question_analyzer as _qa
from . import registry as _reg
from . import response_builder as _rb

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 4  # hard cap on sequential tool-call rounds per turn
import os as _os
_MAIN_MODEL = _os.environ.get("PHASE_20_SYNTHESIS_MODEL", "gpt-4o").strip() or "gpt-4o"
_FINAL_JSON_FALLBACK_MODEL = "gpt-4o-mini"  # cheaper for the post-tool JSON synthesis


def _system_prompt(role: str, output_hint: str, language: str) -> str:
    lang_clause = {
        "hi": "Reply in Hindi (Devanagari).",
        "bn": "Reply in Bengali (or Romanised Bengali if asked).",
        "ta": "Reply in Tamil.",
        "te": "Reply in Telugu.",
        "mr": "Reply in Marathi.",
        "en": "",
    }.get(language, "")
    format_clause = {
        "single_fact": "Provide a single concise fact in a `text` block.",
        "card":        "If you got an employee profile, emit `{type:'employee_card',data:{...}}`. If a client profile, emit `{type:'client_card',data:{...}}`. Wrap any extra context in one `text` block AFTER the card. Do NOT use ONLY a text block for a card-shaped answer — the FE needs the structured card.",
        "table":       "MANDATORY: emit `{type:'table', title, columns:[{key,label,type}], rows:[{...}]}`. Multiple rows from tools MUST become a table. Do NOT summarise them as prose. A single `text` block leading into the table is OK but the table block IS REQUIRED.",
        "chart":       "MANDATORY: emit `{type:'chart', kind:'line|bar|pie|donut|area', title, x_key, y_keys:[...], data:[{x_key:'2025-Q3', y_key_1:1234}, ...]}`. Tool data MUST be reshaped into a `data` array of objects. Do NOT write a paragraph and skip the chart block. A short `text` lead-in is OK but the chart block IS REQUIRED.",
        "image":       "Acknowledge the chart will be generated in a short `text` block (the renderer appends the actual ImageBlock).",
        "download":    "Wrap in a `download` block; the dataset is too large for inline display.",
        "narrative":   "Wrap a 2-4 sentence answer in a `text` block.",
        "refusal":     "Politely refuse with a `text` block explaining the role can't see this data.",
    }.get(output_hint, "")
    role_clause = {
        "visitor":  "Caller is unverified — aggregates only, never PII.",
        "client":   "Caller is a VERIFIED client. They are entitled to see THEIR OWN data. The adapter clamps every UCC/PAN param to their verified value, so you can safely call client-data tools and SHOW the results.",
        "employee": "Caller is a VERIFIED SMIFS employee. They can see clients in their RM book — the adapter enforces this. You are EXPECTED to share their own profile, their book of clients, and firm-wide directory + stats. Do NOT refuse to share their own designation, manager, or the names of clients in their book — that data is theirs to see.",
        "admin":    "Caller is an admin. Full surface.",
    }.get(role, "")
    return (
        "You are SMIFS Lead Wealth-Engagement Agent. Use the provided OrgLens tools to answer.\n\n"
        "═══════════════════════════════════════════════════════════════════\n"
        "HARD RULES (non-negotiable; violation = malformed response, will be rejected and re-prompted)\n"
        "═══════════════════════════════════════════════════════════════════\n"
        f"• The Question Analyzer classified this turn's `output_hint` as: **{output_hint.upper()}**.\n"
        " • If `output_hint=table` AND any tool result's `value` contains a list/array field with >=1 entries, you MUST emit a `table` block in `blocks[]`. A text block ALONE is FORBIDDEN in this case.\n"
        " • If `output_hint=chart` AND tool data contains a time-series OR comparable-categories field, you MUST emit a `chart` block. A text block ALONE is FORBIDDEN in this case.\n"
        " • If `output_hint=card` AND a tool returned a single employee or client record, you MUST emit `employee_card` or `client_card`. A text block ALONE is FORBIDDEN.\n"
        " • There is NO EXCEPTION to the above three rules. If you genuinely have no data, say so in text — but if you have list/series/entity data, the structured block is REQUIRED.\n"
        "═══════════════════════════════════════════════════════════════════\n\n"
        "STRICT DATA RULES (non-negotiable):\n"
        " • NEVER fabricate rows, names, UCCs, PANs, amounts, scrips, or any field values. Every cell in your reply must come from a tool result you can cite. If you didn't get data, say so.\n"
        " • If a tool returns ok=false (forbidden_role / not_in_rm_book / session_binding_missing / orglens_unavailable / execution_failed), DO NOT call it again with the same params and DO NOT pretend you got data. Compose a polite refusal/explanation in a `text` block.\n"
        " • **CLAMP RULE**: If a tool result has `\"clamped\": true`, the caller asked about a DIFFERENT identifier than they're verified for. The `value` belongs to the CALLER, not to the identifier they asked about. You MUST refuse politely: explain you can only share their own account data, and offer to help with that instead. NEVER present the clamped data as if it were the requested party's record.\n"
        " • If a tool returned ok=true but the `value` is empty or sparse, say so clearly (e.g. 'No matching records'). Do not invent placeholder rows.\n\n"
        "After tool calls finish, emit a SINGLE JSON object as your final reply with shape:\n"
        '{\"blocks\": [...], \"summary\": \"<one-line>\"}\n'
        "Each block is one of:\n"
        " - {type:'text', text:'...'}\n"
        " - {type:'table', title, columns:[{key,label,type:'text|inr|num|date_relative',sortable:true?,frozen:true?,default_sort:'desc'?}], rows:[{...}], row_total}\n"
        " - {type:'chart', kind:'line|bar|pie|donut|area|sparkline', title, x_key, y_keys:[...], data:[{...}], max_slices:7?}\n"
        " - {type:'image', src:'/api/charts/<id>.png', alt:'...', width, height}  (only if you've been told an image will be generated)\n"
        " - {type:'download', title, format:'csv|json', url, row_count, size_bytes}\n"
        " - {type:'employee_card', data:{employee_id, name, designation, department, email, manager, location, employment_status, verified}}\n"
        " - {type:'client_card', data:{ucc, client_name, pan, branch, state, rm_name, verified}}\n\n"
        "FORMAT-PICKING DECISION TREE (apply IN ORDER, take the FIRST that matches):\n"
        " 1. Tool list response with >=2 rows OR user asked for 'list / all / show / top N / which': USE `table` block.\n"
        " 2. User compared >=2 named items (compare X vs Y / side-by-side / X versus Y): USE `table` with X & Y as rows (2-row table).\n"
        " 3. Tool returned bucketed time-series data (months / quarters / dates): USE `chart` block (`line` for trend, `bar` for discrete buckets).\n"
        " 4. Tool returned proportional breakdown (sectors / states / categories with shares): USE `chart` block (`pie` or `donut`).\n"
        " 5. Tool returned a single employee profile and user wanted 'profile / details / who is / show me X': USE `employee_card`.\n"
        " 6. Tool returned a single client master + ledger snapshot and user wanted '360 / full snapshot / give me everything about UCC': USE `client_card`.\n"
        " 7. Trigger phrase 'org chart / reporting structure / hierarchy / team tree': USE `text` saying 'rendering chart' (the renderer appends the image).\n"
        " 8. Trigger phrase 'portfolio split / asset allocation / equity vs debt': USE `text` saying 'rendering chart' (the renderer appends the image).\n"
        " 9. Otherwise (single fact, narrative, refusal): USE `text` block only.\n\n"
        "FEW-SHOT EXAMPLES:\n\n"
        "Example A — list → table (user: 'List my running SIPs'):\n"
        '{\"blocks\":[\n'
        '  {\"type\":\"text\",\"text\":\"You have 3 SIPs running.\"},\n'
        '  {\"type\":\"table\",\"title\":\"Your Running SIPs\",\"columns\":[{\"key\":\"scheme\",\"label\":\"Scheme\"},{\"key\":\"amount\",\"label\":\"Amount\",\"type\":\"inr\"},{\"key\":\"next_debit\",\"label\":\"Next Debit\",\"type\":\"date_relative\"}],\"rows\":[{\"scheme\":\"HDFC Top 100\",\"amount\":25000,\"next_debit\":\"2026-06-05\"},{\"scheme\":\"Axis Bluechip\",\"amount\":10000,\"next_debit\":\"2026-06-10\"},{\"scheme\":\"SBI Small Cap\",\"amount\":15000,\"next_debit\":\"2026-06-15\"}],\"row_total\":3}\n'
        '],\"summary\":\"3 SIPs totalling Rs. 50,000/month\"}\n\n'
        "Example B — comparison → 2-row table (user: 'Compare Finance vs Wealth Mgmt'):\n"
        '{\"blocks\":[\n'
        '  {\"type\":\"text\",\"text\":\"Here is the side-by-side.\"},\n'
        '  {\"type\":\"table\",\"title\":\"Finance vs Wealth Management\",\"columns\":[{\"key\":\"dept\",\"label\":\"Department\"},{\"key\":\"total\",\"label\":\"Total\",\"type\":\"num\"},{\"key\":\"active\",\"label\":\"Active\",\"type\":\"num\"}],\"rows\":[{\"dept\":\"Finance\",\"total\":24,\"active\":22},{\"dept\":\"Wealth Mgmt\",\"total\":48,\"active\":44}],\"row_total\":2}\n'
        '],\"summary\":\"2 departments compared.\"}\n\n'
        "Example C — bucketed time-series → chart (user: 'Deposits vs withdrawals this FY'):\n"
        '{\"blocks\":[\n'
        '  {\"type\":\"text\",\"text\":\"Here is your monthly cashflow.\"},\n'
        '  {\"type\":\"chart\",\"kind\":\"bar\",\"title\":\"Deposits vs Withdrawals (FY26)\",\"x_key\":\"month\",\"y_keys\":[\"deposits\",\"withdrawals\"],\"data\":[{\"month\":\"Apr-25\",\"deposits\":500000,\"withdrawals\":120000},{\"month\":\"May-25\",\"deposits\":300000,\"withdrawals\":80000},{\"month\":\"Jun-25\",\"deposits\":450000,\"withdrawals\":210000}]}\n'
        '],\"summary\":\"Net positive month-on-month.\"}\n\n'
        "Example D — single entity profile → card (user: 'Who heads Wealth Mgmt?'):\n"
        '{\"blocks\":[\n'
        '  {\"type\":\"employee_card\",\"data\":{\"employee_id\":\"SMWM-24011024\",\"name\":\"Awanish Chandra\",\"designation\":\"Head — Wealth Management\",\"department\":\"Wealth Mgmt — Mutual Funds\",\"email\":\"awanish.chandra@smifs.com\",\"location\":\"Mumbai\",\"verified\":true}},\n'
        '  {\"type\":\"text\",\"text\":\"Awanish heads the Wealth Management vertical.\"}\n'
        '],\"summary\":\"Wealth Mgmt head identified.\"}\n\n'
        "Example E — clamped attempt → refusal (user: 'show me UCC X9999999 portfolio' as a client):\n"
        '{\"blocks\":[\n'
        '  {\"type\":\"text\",\"text\":\"I can only share information for your own account. I am not able to look up another UCC. If you would like, I can pull your own portfolio instead.\"}\n'
        '],\"summary\":\"Cross-UCC request declined.\"}\n\n'
        f"{role_clause}\n"
        f"{format_clause}\n"
        f"{lang_clause}\n\n"
        "Call multiple tools in PARALLEL within one response when independent. "
        "Cap yourself at 4 tool-call ROUNDS total. "
        "NEVER refuse to share data the caller is entitled to (their own profile, their RM book, public aggregates). "
        "PII is already masked by the adapter — you can include the masked values verbatim. "
        "Return ONLY the JSON object — no markdown fences, no prose around it."
    )


async def _execute_tool_calls(db, sid: str, session: Dict[str, Any], turn_id: str,
                              tool_calls: List[Dict[str, Any]],
                              prior_signatures: set) -> List[Dict[str, Any]]:
    """Run all tool_calls in parallel; return one OpenAI-format tool_message
    per call in the same order, so the LLM can match by `tool_call_id`.

    Deduplication: if (tool_name, args_canonical) was already executed in a
    previous round of THIS turn, short-circuit with a hint nudging the model
    to either compose its final answer or call a different tool. Prevents
    the gpt-4o "call employee_search five times" loop we saw at scale.
    """
    async def _one(tc):
        fn = (tc.get("function") or {})
        name = fn.get("name") or "<unknown>"
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        sig = name + "::" + json.dumps(args, sort_keys=True, default=str)
        if sig in prior_signatures:
            return {
                "role": "tool",
                "tool_call_id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                "name": name,
                "content": json.dumps({"ok": False, "tool": name,
                                        "error": "duplicate_call",
                                        "hint": "You already called this tool with the same arguments in a previous round. Use the prior result and compose your final answer, or call a DIFFERENT tool."}),
            }
        prior_signatures.add(sig)
        res = await _adapter.execute(db, tool_name=name, params=args, session=session,
                                       session_id=sid, turn_id=turn_id)
        return {
            "role": "tool",
            "tool_call_id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
            "name": name,
            "content": json.dumps(_compact_for_llm(res), default=str)[:6000],
        }
    return await asyncio.gather(*[_one(tc) for tc in tool_calls])


def _compact_for_llm(res: Dict[str, Any]) -> Dict[str, Any]:
    """Trim a tool result before sending back to the LLM. We never need the
    raw 102-field MF investor blob for the LLM's context — pick the keys
    the LLM needs, drop the rest."""
    if not res.get("ok"):
        return {"ok": False, "tool": res.get("tool_name"), "error": res.get("error")}
    v = res.get("value")
    out_meta: Dict[str, Any] = {"ok": True, "tool": res.get("tool_name"),
                                 "cache_hit": res.get("cache_hit", False)}
    if res.get("clamped"):
        # Critical safety signal — surface clamp to the LLM so it refuses
        # rather than presenting the caller's data as the requested party's.
        out_meta["clamped"] = True
        out_meta["clamped_from"] = res.get("clamped_from")
        out_meta["clamped_to"] = res.get("clamped_to")
        out_meta["clamp_note"] = res.get("clamp_note")
    # Keep small payloads verbatim; trim large lists to first 50 items.
    if isinstance(v, dict):
        out = {}
        for k, vv in v.items():
            if isinstance(vv, list) and len(vv) > 50:
                out[k] = vv[:50]
                out[f"_{k}_truncated"] = {"total": len(vv), "shown": 50}
            else:
                out[k] = vv
        out_meta["value"] = out
        return out_meta
    out_meta["value"] = v
    return out_meta


async def run(db, session_id: str, user_message: str,
              session: Dict[str, Any], identity_obj: Optional[Dict[str, Any]],
              session_context: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a parent-orchestrator-compatible dict:
        {"ok", "blocks", "model", "intent", "trace": [...]}
    """
    turn_id = str(uuid.uuid4())
    role = _adapter._role_of({
        "auth_state": session_context.get("auth_state"),
        "session_type": session_context.get("session_type"),
        "identity": identity_obj or {},
    })

    # 1. Question Analyzer
    classification = await _qa.classify(user_message, role=role,
                                          session_id=session_id, db=db)
    trace = [{"step": "question_analyzer", "envelope": classification}]

    if classification.get("output_hint") == "refusal":
        return {
            "ok": True,
            "blocks": [{"type": "text",
                         "text": "I can't share that information for this role. "
                                  "If you think this is wrong, please contact your relationship manager."}],
            "model": classification.get("model"),
            "intent": "TOOLS_REFUSAL",
            "trace": trace,
            "classification": classification,
        }

    # 2. Registry selection
    tools = _reg.select(
        role=role,
        tool_hints=classification.get("tool_hint") or None,
        max_tools=8,
    )
    if not tools:
        # Fall back to the legacy orchestrator by returning ok=False.
        trace.append({"step": "registry_select", "tools": [], "reason": "no_visible_tools"})
        return {"ok": False, "reason": "no_visible_tools", "trace": trace,
                "classification": classification}

    function_schemas = _reg.function_schemas(tools)
    trace.append({"step": "registry_select", "tools": [t["name"] for t in tools]})

    # 3. Multi-round function-calling loop
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(role,
                                                       classification.get("output_hint", "narrative"),
                                                       classification.get("language", "en"))},
        {"role": "user", "content": user_message},
    ]

    final_text = ""
    model_used = None
    saw_any_tool_calls = False
    prior_signatures: set = set()
    accumulated_tool_payloads: List[Dict[str, Any]] = []  # parsed _compact_for_llm outputs (for hard gates)
    for round_idx in range(_MAX_TOOL_ROUNDS):
        # On the FIRST round, we let the model decide if it needs tools (tool_choice=auto).
        # Once we have at least one tool result, we still allow more tool calls but also
        # nudge the model with response_format=json_object so its final answer parses.
        wants_json_format = saw_any_tool_calls  # only after tools have run
        try:
            res = await _llm.call_with_tools(
                messages=messages, tools=function_schemas,
                temperature=0.2, max_tokens=1400, model=_MAIN_MODEL,
                session_id=session_id, intent="tools_orchestrator",
                response_format=({"type": "json_object"} if wants_json_format else None),
                timeout=90.0,
            )
        except Exception as e:
            logger.exception("LLM tool-calling round %d failed", round_idx)
            return {"ok": False, "reason": f"llm_error:{type(e).__name__}:{str(e)[:80]}",
                    "trace": trace, "classification": classification}
        model_used = res.get("model") or model_used
        choice = (res.get("data") or {}).get("choices", [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        assistant_msg = {"role": "assistant", "content": msg.get("content") or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        if not tool_calls:
            final_text = msg.get("content") or ""
            trace.append({"step": "llm_round", "round": round_idx, "tool_calls": 0,
                           "finalised": True})
            break

        saw_any_tool_calls = True
        trace.append({"step": "llm_round", "round": round_idx,
                       "tool_calls": [tc.get("function", {}).get("name") for tc in tool_calls]})
        tool_results = await _execute_tool_calls(db, session_id,
                                                   {"auth_state": session_context.get("auth_state"),
                                                    "session_type": session_context.get("session_type"),
                                                    "identity": identity_obj or {}},
                                                   turn_id, tool_calls, prior_signatures)
        # Capture parsed payloads for response_builder hard gates (table-shape + clamp).
        for tm in tool_results:
            try:
                accumulated_tool_payloads.append(json.loads(tm.get("content") or "{}"))
            except Exception:
                pass
        messages.extend(tool_results)
    else:
        # Hit the cap — force a final JSON synthesis pass without tools.
        messages.append({"role": "system",
                          "content": "Tool budget exhausted (5 rounds). Produce a final JSON answer NOW with whatever data you have. Do NOT call any more tools."})
        try:
            res = await _llm.call_with_tools(
                messages=messages, tools=function_schemas,
                temperature=0.2, max_tokens=1200, model=_MAIN_MODEL,
                session_id=session_id, intent="tools_orchestrator_final",
                response_format={"type": "json_object"}, tool_choice="none",
                timeout=90.0,
            )
            choice = (res.get("data") or {}).get("choices", [{}])[0]
            final_text = (choice.get("message") or {}).get("content") or ""
            model_used = res.get("model") or model_used
        except Exception as e:
            return {"ok": False, "reason": f"llm_finalise_error:{type(e).__name__}",
                    "trace": trace, "classification": classification}

    # If the LLM returned tool-free text that isn't JSON, do ONE more pass forcing JSON synthesis.
    if final_text and not _rb._extract_json(final_text):
        messages.append({"role": "system",
                          "content": "Reformat your last reply as a JSON object: {\"blocks\":[...],\"summary\":\"...\"} per the schema. Return ONLY the JSON."})
        try:
            res2 = await _llm.call_with_tools(
                messages=messages, tools=function_schemas,
                temperature=0.0, max_tokens=900, model=_FINAL_JSON_FALLBACK_MODEL,
                session_id=session_id, intent="tools_orchestrator_reformat",
                response_format={"type": "json_object"}, tool_choice="none",
                timeout=60.0,
            )
            choice2 = (res2.get("data") or {}).get("choices", [{}])[0]
            reformatted = (choice2.get("message") or {}).get("content") or ""
            if _rb._extract_json(reformatted):
                final_text = reformatted
                trace.append({"step": "reformat", "model": res2.get("model")})
        except Exception:
            logger.exception("reformat pass failed (non-fatal)")

    # 4. Parse + post-process the LLM's final JSON answer into renderer blocks.
    blocks = _rb.build_blocks(final_text,
                                output_hint=classification.get("output_hint", "narrative"),
                                language=classification.get("language", "en"))

    # 4a. HARD GATES (response_builder layer) — table-shape + clamp-shape.
    # Returns possibly-rewritten blocks + a flag if the LLM needs a reprompt.
    output_hint = classification.get("output_hint", "narrative")
    language = classification.get("language", "en")
    blocks, needs_reprompt, gate_reason = _rb.enforce_hard_gates(
        blocks,
        output_hint=output_hint,
        tool_payloads=accumulated_tool_payloads,
        language=language,
    )
    if needs_reprompt:
        trace.append({"step": "hard_gate_reprompt", "reason": gate_reason})
        # ONE retry: re-prompt the LLM with the explicit shape instruction.
        gate_instr = (
            "Your previous reply was rejected by the response gate: it was missing the required "
            f"`{gate_reason}` block. Re-emit your answer with that structured block included. "
            "Use the tool data you already retrieved this turn. Return ONLY the JSON object."
        )
        messages.append({"role": "system", "content": gate_instr})
        try:
            res3 = await _llm.call_with_tools(
                messages=messages, tools=function_schemas,
                temperature=0.0, max_tokens=1200, model=_MAIN_MODEL,
                session_id=session_id, intent="tools_orchestrator_gate_retry",
                response_format={"type": "json_object"}, tool_choice="none",
                timeout=75.0,
            )
            choice3 = (res3.get("data") or {}).get("choices", [{}])[0]
            retry_text = (choice3.get("message") or {}).get("content") or ""
            retry_blocks = _rb.build_blocks(retry_text, output_hint=output_hint, language=language)
            retry_blocks, still_needs, _r2 = _rb.enforce_hard_gates(
                retry_blocks, output_hint=output_hint,
                tool_payloads=accumulated_tool_payloads, language=language,
            )
            if not still_needs:
                blocks = retry_blocks
                trace.append({"step": "hard_gate_retry_ok"})
            else:
                # Final fallback: synthesise the structured block programmatically.
                blocks = _rb.programmatic_fallback(
                    retry_blocks, output_hint=output_hint,
                    tool_payloads=accumulated_tool_payloads, language=language,
                )
                trace.append({"step": "hard_gate_programmatic_fallback",
                              "reason": gate_reason})
                try:
                    import resilience as _r
                    await _r.log_security_event(
                        db, kind="composition_format_failure", session_id=session_id,
                        role_state_value=role,
                        user_message=f"output_hint={output_hint} reason={gate_reason} retry_still_missing=True",
                        action="programmatic_fallback_used",
                    )
                except Exception:
                    pass
        except Exception:
            logger.exception("hard-gate retry failed; using programmatic fallback")
            blocks = _rb.programmatic_fallback(
                blocks, output_hint=output_hint,
                tool_payloads=accumulated_tool_payloads, language=language,
            )

    # 5. Image hook — generate a PNG for the two approved use cases.
    blocks = await _rb.maybe_generate_image_blocks(
        db, session=session, identity=identity_obj or {},
        blocks=blocks, user_message=user_message, classification=classification,
    )

    return {
        "ok": True,
        "blocks": blocks,
        "model": model_used,
        "intent": "TOOLS_PIPELINE",
        "trace": trace,
        "classification": classification,
    }
