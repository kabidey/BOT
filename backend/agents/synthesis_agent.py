"""Phase 26b — Synthesis Agent.

Takes an IntelligenceBundle from fanout_orchestrator and composes a
SPECIFIC reply that uses actual data from the sub-agents. NOT a paraphrase.

The synthesis agent is gpt-4o-mini (fast + cheap) by default; switchable
via PHASE_26B_SYNTHESIS_MODEL env var if we want gpt-4o.

System prompt is tightly framed:
    - Use the bundle as primary evidence
    - Quote actual numbers, names, dates verbatim where possible
    - Suggest 2-3 concrete next actions
    - Stay brief (≤ 6 sentences in the main answer)
    - If the bundle is wholly empty, return a soft punt instead of bluffing
"""
from __future__ import annotations
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from agents.llm import chat_with_fallback as _llm_chat
from agents.llm import stream_chat_with_fallback as _llm_stream

logger = logging.getLogger(__name__)

_MODEL = os.environ.get("PHASE_26B_SYNTHESIS_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


# Phase 26.3.B — Per-model USD rates (per 1k tokens). Values mirror current
# OpenAI list pricing as of 2025; Hub AI charges pass-through.
MODEL_RATES: "Dict[str, Tuple[float, float]]" = {
    # model              (input_per_1k, output_per_1k)
    "gpt-4o-mini":       (0.00015,  0.0006),
    "gpt-4o":            (0.0025,   0.01),
    "gpt-4o-2024-08-06": (0.0025,   0.01),
    "claude-haiku-4-5-20251001":  (0.0008, 0.004),
    "claude-sonnet-4-5-20251001": (0.003,  0.015),
    "llama-3.3-70b-versatile":    (0.0,    0.0),   # Hub-hosted, no per-token fee
    "gemma-4-E4B":                (0.0,    0.0),
    "auto":              (0.00015, 0.0006),         # Hub typically routes to mini
}


def _estimate_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Compute USD cost for a given model + token counts. Falls back to the
    gpt-4o-mini rate when the model is unknown."""
    rate_in, rate_out = MODEL_RATES.get(model or "", MODEL_RATES["gpt-4o-mini"])
    return round((prompt_tokens / 1000.0) * rate_in
                 + (completion_tokens / 1000.0) * rate_out, 6)


async def _stream_chat(messages, temperature=0.3, max_tokens=600):
    """Wrapper around _llm_stream that pins a specific model for reliable
    streaming. Hub AI's 'auto' chain sometimes picks non-streaming models."""
    # The library's stream_chat_with_fallback iterates through CHAT_CHAIN —
    # we can't directly force the model, but we can ensure the first model
    # tried is one that supports SSE streaming reliably (gpt-4o-mini).
    async for ev in _llm_stream(messages=messages, temperature=temperature,
                                max_tokens=max_tokens):
        yield ev


# ---- System prompts per event kind ----
_BASE_INSTR = (
    "You are the synthesis layer of the Mackertich ONE Wealth-Engagement Agent "
    "(the wealth-management vertical of SMIFS Ltd). A parallel fan-out has just "
    "returned a research bundle from multiple sub-agents (BMIA, RAG knowledge base, "
    "OrgLens directory). Your job is to compose a SPECIFIC, useful answer that "
    "uses the ACTUAL data in the bundle — quote real numbers, names, dates verbatim.\n\n"
    "HARD RULES (these override every persona instruction):\n"
    "1. THE EVIDENCE BUNDLE IS YOUR PRIMARY SOURCE. The bundle below contains real "
    "data fetched seconds ago. You MUST quote at least 3 specific data points from "
    "it (e.g. ticker symbol, EPS, P/E, market cap, sales growth %, return-on-equity, "
    "specific scheme names, top holdings, RM names, recent quarterly figures). Do "
    "NOT generalise to 'Reliance is a prominent conglomerate' — give the user the "
    "actual numbers from the bundle.\n"
    "2. NEVER invent numbers, dates, ticker prices, P/E ratios, or product specifics "
    "that are NOT in the bundle. If a field is missing from the bundle, omit it; do "
    "not estimate or guess.\n"
    "3. If at least ONE sub-agent succeeded (status='ok'), USE that data — do NOT "
    "punt to a generic 'how can I help' reply.\n"
    "4. Open with the user's subject (the ticker / product / their name), then 2-4 "
    "short sentences of CONCRETE data, then a single follow-up question offering "
    "2-3 next-step options.\n"
    "5. Plain prose. No markdown bullets in the main paragraph. ≤ 6 sentences total.\n"
    "6. NEVER pitch Mackertich ONE's services in the opening — the user asked a "
    "concrete question, answer it concretely first.\n"
)


def _persona_voice(persona: str) -> str:
    p = (persona or "visitor").lower()
    if p == "client":
        return (
            "\nVOICE — client concierge: thoughtful private-banker tone. Use the "
            "client's first name if it appears in the bundle. Quote held positions, "
            "SIP mandates, RM names from the bundle by their actual values.\n"
        )
    if p == "employee":
        return (
            "\nVOICE — internal platform peer: peer-to-peer to a SMIFS Ltd employee. "
            "Technically precise, assume jargon fluency.\n"
        )
    # VISITOR — explicit anti-pivot guidance. The brand is already in the "
    # signature; the body must be the user's actual question, answered.
    return (
        "\nVOICE — courteous host: warm, brief, factual. Do NOT pivot to "
        "Mackertich ONE's value proposition until AFTER you have answered the "
        "user's specific question with concrete data from the bundle.\n"
    )


def _identity_event_addendum(persona: str) -> str:
    """Phase 26.2.A — extra instructions for identity-event syntheses."""
    if persona == "employee":
        return (
            "\nEVENT — IDENTITY VERIFIED (employee): The user just authenticated "
            "as a SMIFS Ltd employee. Compose a SHORT proactive opener (≤ 4 "
            "sentences) referencing their employee record (name, designation, "
            "team, manager — whatever the bundle exposes). End with exactly TWO "
            "concrete next-step options framed for an internal teammate "
            "(e.g. 'pull up a client by UCC?', 'see this week's BMIA briefing?'). "
            "Tone: peer, not concierge.\n"
        )
    return (
        "\nEVENT — IDENTITY VERIFIED (client): The user just authenticated as a "
        "verified SMIFS Ltd client. Compose a SHORT proactive opener (≤ 4 "
        "sentences) referencing at least 3 specific data points from the bundle "
        "(e.g. AUM, top fund holding name + amount, active SIP scheme + monthly "
        "amount, assigned RM name, recent transaction). End with exactly TWO "
        "concrete next-step options (e.g. 'review your demat KYC refresh', "
        "'discuss a new NCD that fits your risk profile', 'walk through last "
        "month's transactions'). Tone: thoughtful private banker.\n"
    )


def _bundle_to_evidence(bundle: Dict[str, Any]) -> str:
    """Render the bundle as a compact evidence block the LLM can use. We keep
    the raw JSON for sub-agents that returned structured data, but trim verbose
    fields (full P&L tables, RAG full text) to a sensible cap."""
    lines: List[str] = []
    lines.append(f"# Fan-out bundle — kind={bundle.get('kind')} subject={bundle.get('subject')} "
                 f"elapsed={bundle.get('elapsed_ms')}ms ok={bundle.get('ok_count')} "
                 f"timeout={bundle.get('timeout_count')} error={bundle.get('error_count')}")
    for name, blob in (bundle.get("results") or {}).items():
        status = blob.get("status")
        value = blob.get("value")
        if status != "ok":
            lines.append(f"\n## {name} — STATUS={status}")
            if blob.get("value"):
                lines.append(str(blob.get("value"))[:200])
            continue
        lines.append(f"\n## {name} — OK")
        # Compact JSON, but trim long arrays/strings.
        compacted = (_compact_identity(value) if bundle.get("kind") == "identity"
                     else _compact(value))
        try:
            lines.append(json.dumps(compacted, indent=2, default=str)[:2500])
        except Exception:
            lines.append(str(compacted)[:2500])
    return "\n".join(lines)


def _compact(obj: Any, max_str: int = 360, max_list: int = 8) -> Any:
    """Recursively shrink a JSON-like blob so the LLM context stays small."""
    if isinstance(obj, str):
        return obj if len(obj) <= max_str else obj[:max_str] + "…"
    if isinstance(obj, list):
        return [_compact(x, max_str, max_list) for x in obj[:max_list]]
    if isinstance(obj, dict):
        return {k: _compact(v, max_str, max_list) for k, v in obj.items()}
    return obj


def _compact_identity(obj: Any) -> Any:
    """Tighter compaction for identity-event bundles where MF/BO book payloads
    can be hundreds of clients deep. Keeps the first 3 of each list."""
    if isinstance(obj, str):
        return obj if len(obj) <= 180 else obj[:180] + "…"
    if isinstance(obj, list):
        return [_compact_identity(x) for x in obj[:3]]
    if isinstance(obj, dict):
        return {k: _compact_identity(v) for k, v in obj.items()}
    return obj


# ---------- Public compose ----------
async def compose(bundle: Dict[str, Any], user_message: str, persona: str = "visitor") -> Dict[str, Any]:
    """Produce a final reply from a fan-out bundle.
    Returns: {text, model, blocks_extra}
        blocks_extra — optional list of structured blocks to surface
        alongside the text (e.g. a bmia_fundamentals_card for ticker events).
    """
    # If the bundle is empty / wholly failed, return a soft punt — caller can
    # decide to fall through to the reactive path.
    if int(bundle.get("ok_count") or 0) == 0:
        return {"text": "", "model": _MODEL, "blocks_extra": [], "empty": True}

    system = _BASE_INSTR + _persona_voice(persona)
    if bundle.get("kind") == "identity":
        system += _identity_event_addendum(persona)
    evidence = _bundle_to_evidence(bundle)
    user_block = (
        f"USER QUESTION:\n{user_message}\n\n"
        f"EVIDENCE BUNDLE (use ONLY this data; do not invent):\n{evidence}\n\n"
        f"REPLY RULES:\n"
        f"- Open with the subject the user asked about (the ticker / product / their name).\n"
        f"- Cite at least 3 specific data points from the bundle (e.g. exact P/E, "
        f"market cap, EPS, sales growth %, scheme name, RM name, quarterly figures, "
        f"NAV, AUM, founding/management details).\n"
        f"- Use plain prose. No bullet lists. ≤ 6 sentences total.\n"
        f"- End with ONE follow-up question offering 2–3 concrete next-step options.\n"
        f"- Do NOT pivot to marketing copy about Mackertich ONE before answering.\n"
        f"Compose the reply now."
    )
    messages = [
        {"role": "system",  "content": system},
        {"role": "user",    "content": user_block},
    ]

    try:
        resp = await _llm_chat(messages=messages, temperature=0.3, max_tokens=600)
    except Exception:
        logger.exception("synthesis chat_with_fallback failed")
        return {"text": "", "model": _MODEL, "blocks_extra": [], "empty": True}

    # chat_with_fallback returns {"data": <openai-shape>, "model": "..."}
    data = (resp or {}).get("data") or {}
    choices = data.get("choices") or []
    text = ""
    if choices:
        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
    model_used = (resp or {}).get("model") or _MODEL
    blocks_extra = _structured_extras(bundle)
    logger.info("synthesis ok: model=%s reply_chars=%d extras=%d kind=%s",
                model_used, len(text), len(blocks_extra), bundle.get("kind"))
    return {"text": text.strip(), "model": model_used, "blocks_extra": blocks_extra, "empty": False}


# ---------- Public streaming variant ----------
async def compose_streaming(bundle: Dict[str, Any], user_message: str,
                            persona: str = "visitor",
                            emit_token=None) -> Dict[str, Any]:
    """Phase 26.2.B — Stream synthesis tokens to the client as they arrive.

    Same evidence-bundle pipeline as `compose()`, but uses the streaming
    Hub AI endpoint. Tokens are surfaced via `emit_token(chunk)` so the chat
    surface paints them progressively. Returns the same shape as compose():
    `{text, model, blocks_extra, empty}` after the stream completes.

    The structured `blocks_extra` (e.g. BmiaFundamentalsCard) are computed
    AFTER the text finishes so the caller appends them as a follow-up block.
    """
    if int(bundle.get("ok_count") or 0) == 0:
        return {"text": "", "model": _MODEL, "blocks_extra": [], "empty": True}

    system = _BASE_INSTR + _persona_voice(persona)
    if bundle.get("kind") == "identity":
        system += _identity_event_addendum(persona)
    evidence = _bundle_to_evidence(bundle)
    user_block = (
        f"USER QUESTION:\n{user_message}\n\n"
        f"EVIDENCE BUNDLE (use ONLY this data; do not invent):\n{evidence}\n\n"
        f"REPLY RULES:\n"
        f"- Open with the subject the user asked about (the ticker / product / their name).\n"
        f"- Cite at least 3 specific data points from the bundle (e.g. exact P/E, "
        f"market cap, EPS, sales growth %, scheme name, RM name, quarterly figures, "
        f"NAV, AUM, founding/management details).\n"
        f"- Use plain prose. No bullet lists. ≤ 6 sentences total.\n"
        f"- End with ONE follow-up question offering 2–3 concrete next-step options.\n"
        f"- Do NOT pivot to marketing copy about Mackertich ONE before answering.\n"
        f"Compose the reply now."
    )
    messages = [
        {"role": "system",  "content": system},
        {"role": "user",    "content": user_block},
    ]

    text_parts: List[str] = []
    model_used = _MODEL
    usage_obj: Dict[str, Any] = {}
    try:
        async for ev_type, payload in _llm_stream(messages=messages, temperature=0.3, max_tokens=600):
            if ev_type == "token":
                text_parts.append(payload)
                if emit_token is not None:
                    try:
                        await emit_token(payload)
                    except Exception:
                        logger.debug("synthesis emit_token failed", exc_info=True)
            elif ev_type == "done":
                model_used = (payload or {}).get("model") or _MODEL
                usage_obj = ((payload or {}).get("data") or {}).get("usage") or {}
    except Exception:
        logger.exception("synthesis streaming failed; falling back to buffered compose")
        return await compose(bundle, user_message, persona)

    text = "".join(text_parts).strip()
    blocks_extra = _structured_extras(bundle)

    # Phase 26.3.B — compute USD cost from usage tokens.
    prompt_tokens = int(usage_obj.get("prompt_tokens") or 0)
    completion_tokens = int(usage_obj.get("completion_tokens") or 0)
    total_tokens = prompt_tokens + completion_tokens
    usd = _estimate_usd(model_used, prompt_tokens, completion_tokens)

    logger.info("synthesis (stream) ok: model=%s reply_chars=%d extras=%d kind=%s "
                "prompt_tok=%d completion_tok=%d usd=%.5f",
                model_used, len(text), len(blocks_extra), bundle.get("kind"),
                prompt_tokens, completion_tokens, usd)
    return {"text": text, "model": model_used, "blocks_extra": blocks_extra, "empty": False,
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "total_tokens": total_tokens, "usd": usd}


def _structured_extras(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    """If the bundle contains a clean BMIA fundamentals profile, surface it
    as a bmia_fundamentals_card so the user gets the rich UI alongside the
    text summary."""
    out: List[Dict[str, Any]] = []
    if bundle.get("kind") != "ticker":
        return out
    results = bundle.get("results") or {}
    # The profile sub-agent's key starts with "bmia_profile_"
    for name, blob in results.items():
        if name.startswith("bmia_profile_") and blob.get("status") == "ok":
            data = blob.get("value")
            if isinstance(data, dict) and (data.get("symbol") or data.get("about")):
                # Optionally merge quarterly into the same card
                for n2, b2 in results.items():
                    if n2.startswith("bmia_quarterly_") and b2.get("status") == "ok":
                        q = (b2.get("value") or {}).get("quarterly_last_4")
                        if q:
                            data = {**data, "quarterly_last_4": q}
                        break
                out.append({"type": "bmia_fundamentals_card", "data": data})
            break
    return out
