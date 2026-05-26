"""Phase 24c — BMIA intent branches.

The legacy intent router (`agents/router.py`) selects ONE tool from the
6-tool intent toolbelt and dispatches to a single branch handler in
`agents/orchestrator.py`. Phase 24 Wave 1's first pass added BMIA tools to
the multi-tool `function_schemas` BUT the legacy router never let those
tools surface — every SEBI/RBI/stock question got pre-routed to
`answer_from_knowledge_base`.

This module adds the 3 missing branch handlers that the orchestrator
dispatches when the router resolves to one of the new intents:

  BMIA_COMPLIANCE      ← `bmia_compliance_research` tool
  BMIA_FUNDAMENTALS    ← `bmia_fundamentals_lookup` tool
  BMIA_BRIEFING        ← `bmia_daily_briefing` tool

Each branch:
  1. Calls the BMIA client.
  2. Composes a SHORT, GROUNDED narrative reply via gpt-4o-mini with a
     strict "do not paraphrase outside the tool result" prompt.
  3. Returns `{blocks, citations, model}` matching the orchestrator
     contract.

The reply NEVER quotes anything the tool didn't return — when the tool
result is empty/null we say "I don't have a confident answer" instead of
falling back to the LLM's pretraining.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from agents import bmia_client as bmia

logger = logging.getLogger(__name__)

LLMHUB_API_KEY = os.environ.get("LLMHUB_API_KEY", "")
LLMHUB_BASE_URL = os.environ.get("LLMHUB_BASE_URL", "").rstrip("/")
_COMPOSE_MODEL = os.environ.get("BMIA_COMPOSE_MODEL", "gpt-4o-mini")

_COMPLIANCE_SYSTEM = (
    "You are SMIFS Wealth Advisor. The user asked a regulatory/compliance question "
    "and a tool just returned ranked citations from the official Indian regulator "
    "corpus (SEBI / RBI / MCA / NSE / BSE / IRDAI). Your reply MUST be GROUNDED in "
    "those citations. Rules:\n"
    "  1. Use ONLY facts present in the tool's `text_chunk` fields. Do NOT add facts "
    "from your pretraining.\n"
    "  2. When you reference a fact, the citation chip strip below the reply will "
    "show the source — DO NOT inline the URL in prose. Refer to citations by their "
    "short title or regulator name (e.g. 'per the SEBI circular dated ...').\n"
    "  3. If the citations don't actually answer the question, say so plainly and "
    "suggest the user contact compliance@smifs.com.\n"
    "  4. Keep it tight: 4-7 short sentences. Indian wealth-manager tone. No emojis.\n"
    "  5. NEVER invent acronym expansions. PIT in SEBI context = 'Prohibition of "
    "Insider Trading'. If the user uses an acronym and you're not certain, say "
    "'I'm assuming PIT refers to … — please confirm if I'm wrong.'"
)

_FUNDAMENTALS_SYSTEM = (
    "You are SMIFS Wealth Advisor. A tool returned NSE fundamentals for the stock "
    "the user asked about. Reply with a 3-4 sentence summary: company overview, the "
    "single most striking pro AND the single most striking con (from the tool data), "
    "and one concrete number to anchor on (e.g. last reported EPS or Sales). The "
    "frontend renders a separate fundamentals card with the full breakdown — do NOT "
    "duplicate that in prose. End with: 'Want me to drill into a specific metric?'"
)

_BRIEFING_SYSTEM = (
    "You are SMIFS Wealth Advisor. A tool returned today's pre-summarized Indian "
    "market briefing. Compose a tight reply with: 1 paragraph headline + bullet "
    "list (max 6 bullets total across the sections). Use only facts present in the "
    "tool result. No pretraining."
)


async def _compose(system: str, user: str, *, max_tokens: int = 600) -> Dict[str, Any]:
    payload = {
        "model": _COMPOSE_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {LLMHUB_API_KEY}",
               "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=25.0) as http:
        r = await http.post(f"{LLMHUB_BASE_URL}/chat/completions", headers=headers,
                              json=payload)
    r.raise_for_status()
    data = r.json()
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return {"text": text.strip(), "model": data.get("model") or _COMPOSE_MODEL}


def _empty_envelope(text: str, *, model: Optional[str] = None) -> Dict[str, Any]:
    return {
        "blocks": [{"type": "text", "text": text}],
        "citations": [],
        "model": model,
    }


# ---------------------------------------------------------------
# 1) Compliance
# ---------------------------------------------------------------
async def branch_compliance(message: str, *, sources: Optional[List[str]] = None,
                              top_k: int = 5) -> Dict[str, Any]:
    try:
        result = await bmia.execute("bmia_compliance_research", {
            "query": message, "sources": sources, "top_k": top_k,
        })
    except Exception:
        logger.exception("bmia compliance failed")
        return _empty_envelope("I couldn't reach the regulatory corpus just now. Please try again in a moment, or write to compliance@smifs.com.")

    if not result.get("ok"):
        return _empty_envelope("I don't have a confident answer on that compliance question right now. The official regulator corpus didn't return useful matches.")

    value = result.get("value") or {}
    chips = value.get("citations") or []
    if not chips:
        return _empty_envelope("I searched the SEBI / RBI / MCA corpus but didn't find a citation that directly answers your question. Please share more context, or contact compliance@smifs.com for a definitive view.")

    # Prepare a compact context for the LLM — trim each chunk hard.
    ctx_lines: List[str] = [f"USER QUESTION: {message}", "", "CITATIONS:"]
    for i, c in enumerate(chips[:top_k]):
        body = (c.get("expand_text") or "")[:800].replace("\n", " ")
        ctx_lines.append(f"[{i + 1}] {c.get('badge')} · {c.get('doc_title')} · {c.get('date_pill') or ''}\n{body}")
    composed = await _compose(_COMPLIANCE_SYSTEM, "\n\n".join(ctx_lines))

    return {
        "blocks": [{"type": "text", "text": composed["text"]}],
        # Use the Phase 16 chip schema directly — Chat.jsx already renders this.
        "citations": [
            {
                "doc_title": c.get("doc_title"),
                "section": c.get("section"),
                "url": c.get("url"),
                "badge": c.get("badge"),
                "date_pill": c.get("date_pill"),
                "expand_text": c.get("expand_text"),
                "score": c.get("score"),
                "source": c.get("source"),
            }
            for c in chips
        ],
        "model": composed["model"],
    }


# ---------------------------------------------------------------
# 2) Fundamentals
# ---------------------------------------------------------------
# Best-effort company-name → NSE ticker map. Resolved BEFORE calling BMIA.
_NSE_TICKER_HINTS: Dict[str, str] = {
    "reliance": "RELIANCE", "reliance industries": "RELIANCE", "ril": "RELIANCE",
    "hdfc bank": "HDFCBANK", "hdfcbank": "HDFCBANK",
    "icici bank": "ICICIBANK", "icicibank": "ICICIBANK",
    "tcs": "TCS", "tata consultancy": "TCS", "tata consultancy services": "TCS",
    "infosys": "INFY", "infy": "INFY",
    "sbi": "SBIN", "state bank of india": "SBIN", "sbin": "SBIN",
    "wipro": "WIPRO", "hcl": "HCLTECH", "hcl technologies": "HCLTECH",
    "axis bank": "AXISBANK", "axisbank": "AXISBANK",
    "kotak bank": "KOTAKBANK", "kotakbank": "KOTAKBANK",
    "itc": "ITC", "hindustan unilever": "HINDUNILVR", "hul": "HINDUNILVR",
    "bajaj finance": "BAJFINANCE", "bajaj finserv": "BAJAJFINSV",
    "asian paints": "ASIANPAINT", "asian paint": "ASIANPAINT",
    "maruti suzuki": "MARUTI", "maruti": "MARUTI",
    "ltimindtree": "LTIM", "lt mindtree": "LTIM",
    "ongc": "ONGC", "ntpc": "NTPC", "power grid": "POWERGRID", "powergrid": "POWERGRID",
    "adani enterprises": "ADANIENT", "adani ports": "ADANIPORTS",
    "bharti airtel": "BHARTIARTL", "airtel": "BHARTIARTL",
    "coal india": "COALINDIA", "tata steel": "TATASTEEL", "tata motors": "TATAMOTORS",
    "mahindra": "M&M", "m&m": "M&M",
    "nestle": "NESTLEIND", "nestle india": "NESTLEIND",
    "sun pharma": "SUNPHARMA", "dr reddy": "DRREDDY",
    "ultratech": "ULTRACEMCO", "ultratech cement": "ULTRACEMCO",
    "titan": "TITAN", "grasim": "GRASIM",
    "jio financial": "JIOFIN", "jiofin": "JIOFIN",
}


def _resolve_symbol(text: str, explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        s = explicit.strip().upper()
        # Strip exchange suffixes if any (e.g. RELIANCE.NS)
        s = s.split(".")[0]
        if re.fullmatch(r"[A-Z][A-Z0-9&]{1,15}", s or ""):
            return s
    low = (text or "").lower()
    # Heuristic: try exact phrase match against the hint map, longest key first.
    for key in sorted(_NSE_TICKER_HINTS.keys(), key=len, reverse=True):
        if key in low:
            return _NSE_TICKER_HINTS[key]
    # Last resort: an UPPERCASE token of 3-12 letters that looks like a ticker.
    m = re.search(r"\b([A-Z]{3,12})\b", text or "")
    if m:
        return m.group(1)
    return None


async def branch_fundamentals(message: str, *, symbol_hint: Optional[str] = None,
                                 slice_kind: str = "profile") -> Dict[str, Any]:
    symbol = _resolve_symbol(message, symbol_hint)
    if not symbol:
        return _empty_envelope("I want to make sure I look up the right ticker — which NSE symbol do you mean? For example: RELIANCE, HDFCBANK, TCS, INFY, SBIN.")

    try:
        result = await bmia.execute("bmia_fundamentals_lookup",
                                      {"symbol": symbol, "slice": slice_kind})
    except Exception:
        logger.exception("bmia fundamentals failed")
        return _empty_envelope("Couldn't reach the fundamentals source right now. Please try again in a moment.")

    if not result.get("ok"):
        detail = (result.get("detail") or "").lower()
        if "404" in detail or "no fundamentals snapshot" in detail:
            return _empty_envelope(f"I couldn't find an NSE fundamentals snapshot for **{symbol}**. Double-check the ticker — would you like me to try another, e.g. one of: RELIANCE, HDFCBANK, TCS?")
        return _empty_envelope("The fundamentals source returned an error. Please try again in a moment.")

    data = result.get("value") or {}
    # Build the LLM grounding context — trim everything that isn't headline.
    pl3y = data.get("profit_loss_3y") or {}
    headline = {
        "symbol": data.get("symbol"),
        "about": (data.get("about") or "")[:600],
        "pros": (data.get("pros") or [])[:3],
        "cons": (data.get("cons") or [])[:3],
        "profit_loss_3y": {
            "periods": pl3y.get("periods"),
            "rows": {k: v for k, v in (pl3y.get("rows") or {}).items()
                       if k in ("Sales +", "Net Profit +", "EPS in Rs", "OPM %")},
        },
    }
    composed = await _compose(
        _FUNDAMENTALS_SYSTEM,
        f"USER QUESTION: {message}\n\nFUNDAMENTALS:\n{json.dumps(headline, indent=2)}",
        max_tokens=400,
    )
    return {
        "blocks": [
            {"type": "text", "text": composed["text"]},
            {"type": "bmia_fundamentals_card", "data": data},
        ],
        "citations": [],  # the card itself shows "Source: BMIA"
        "model": composed["model"],
    }


# ---------------------------------------------------------------
# 3) Daily briefing
# ---------------------------------------------------------------
def _summarize_briefing(raw: Dict[str, Any], top_per_section: int = 3) -> Dict[str, Any]:
    """Pre-summarize the briefing payload — full briefings are 8KB+ which
    blows up the LLM context. We send only the top N items per section,
    keeping the headline + stock symbol + critical flag."""
    briefs = raw.get("briefings") or []
    if not briefs:
        return {"date": None, "sections": {}}
    b = briefs[0]
    out: Dict[str, Any] = {"date": b.get("date"), "generated_at": b.get("generated_at"),
                            "sections": {}}
    for section in ("board_meetings", "critical_filings", "insider_activity"):
        items = b.get(section) or []
        # Prioritize critical=True, then take first N.
        items_sorted = sorted(items, key=lambda x: 0 if x.get("critical") else 1)
        out["sections"][section] = [
            {
                "stock_symbol": it.get("stock_symbol"),
                "stock_name": it.get("stock_name"),
                "headline": (it.get("headline") or "")[:240],
                "category": it.get("category"),
                "critical": bool(it.get("critical")),
            }
            for it in items_sorted[:top_per_section]
        ]
    return out


async def branch_briefing(message: str, *, date: Optional[str] = None,
                            sections: Optional[List[str]] = None) -> Dict[str, Any]:
    try:
        result = await bmia.execute("bmia_daily_briefing",
                                      {"date": date, "sections": sections})
    except Exception:
        logger.exception("bmia briefing failed")
        return _empty_envelope("Couldn't reach the daily-briefing source just now. Please try again in a moment.")

    if not result.get("ok"):
        return _empty_envelope("The market-briefing source returned an error. Please try again in a moment.")

    summary = _summarize_briefing(result.get("value") or {})
    if not summary.get("sections"):
        return _empty_envelope("No briefing entries available for that date.")

    composed = await _compose(
        _BRIEFING_SYSTEM,
        f"USER QUESTION: {message}\n\nBRIEFING (top 3 per section):\n{json.dumps(summary, indent=2)}",
        max_tokens=500,
    )
    return {
        "blocks": [{"type": "text", "text": composed["text"]}],
        "citations": [],
        "model": composed["model"],
    }
