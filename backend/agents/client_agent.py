"""Phase 12 — Client tool dispatcher.

Mirrors `directory_agent` for the verified-CLIENT palette: portfolio,
ledger balance, recent trades, deposits/withdrawals, MF folios, SIPs.

Privacy invariants
==================
* Every tool ALWAYS uses the verified `ucc` (or `pan`) from
  `identity_obj` — the LLM cannot supply these. We re-assert this in every
  tool wrapper.
* Tool output → human-friendly text + a structured render block. Sensitive
  fields (PAN, Aadhaar, full bank a/c #) are stripped at `client_api`.
"""
from __future__ import annotations
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import client_api

logger = logging.getLogger(__name__)

TTL_SECONDS = 300  # 5-min cache, same as directory_agent
_cache: Dict[Tuple[str, str, str], Tuple[float, Dict[str, Any]]] = {}


def _key(session_id: str, tool: str, args: Dict[str, Any]) -> Tuple[str, str, str]:
    h = hashlib.sha1(json.dumps(args or {}, sort_keys=True).encode()).hexdigest()[:12]
    return (session_id, tool, h)


def _cache_get(k):
    e = _cache.get(k)
    if not e:
        return None
    ts, v = e
    if time.time() - ts > TTL_SECONDS:
        _cache.pop(k, None)
        return None
    return v


def _cache_put(k, v):
    _cache[k] = (time.time(), v)


# ---------- formatters ----------
def _inr(n: float | int | None) -> str:
    if n is None:
        return "—"
    try:
        v = float(n)
    except Exception:
        return str(n)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e7:
        return f"{sign}₹{v / 1e7:.2f} Cr"
    if v >= 1e5:
        return f"{sign}₹{v / 1e5:.2f} L"
    if v >= 1000:
        return f"{sign}₹{v:,.0f}"
    return f"{sign}₹{v:.2f}"


# ---------- tool handlers ----------
async def _t_portfolio(ucc: str, _args: Dict[str, Any]) -> Dict[str, Any]:
    data = await client_api.client_portfolio(ucc)
    holdings = data.get("holdings") or []
    if not holdings:
        return {
            "blocks": [{"type": "text", "text":
                "I checked your back-office holdings and currently see no open equity positions "
                "in your demat with us. If you've recently traded, settled positions may take a "
                "day to appear — happy to refresh again later."}],
            "citations": [], "model": None,
        }
    rows = []
    for h in holdings[:25]:
        rows.append({
            "symbol": h.get("symbol") or h.get("scrip") or h.get("isin"),
            "qty": h.get("quantity") or h.get("qty"),
            "avg_price": h.get("avg_price") or h.get("buy_price"),
            "ltp": h.get("ltp") or h.get("market_price"),
            "value": h.get("market_value") or h.get("value"),
            "pl": h.get("unrealised_pl") or h.get("pnl"),
        })
    text = f"You currently hold {len(holdings)} equity scrip{'' if len(holdings)==1 else 's'} in your demat with us."
    return {
        "blocks": [
            {"type": "text", "text": text},
            {"type": "holdings_table", "data": {"ucc": ucc, "rows": rows, "total": len(holdings)}},
        ],
        "citations": [], "model": None,
    }


async def _t_ledger_balance(ucc: str, _args: Dict[str, Any]) -> Dict[str, Any]:
    data = await client_api.client_ledger_balance(ucc)
    bal = data.get("balance") or 0.0
    bits = [f"Your trading-account ledger balance is **{_inr(bal)}**."]
    if data.get("entries"):
        bits.append(
            f"Lifetime credits: {_inr(data['total_credits'])} · "
            f"debits: {_inr(data['total_debits'])} across {data['entries']} entries."
        )
    if data.get("as_of"):
        bits.append(f"As of {data['as_of']}.")
    return {
        "blocks": [
            {"type": "text", "text": " ".join(bits)},
            {"type": "ledger_balance_card", "data": data},
        ],
        "citations": [], "model": None,
    }


async def _t_recent_trades(ucc: str, args: Dict[str, Any]) -> Dict[str, Any]:
    limit = int(args.get("limit") or 10)
    data = await client_api.client_recent_trades(ucc, limit=limit)
    trades = data.get("trades") or []
    if not trades:
        return {
            "blocks": [{"type": "text", "text":
                f"No trades on file for UCC {ucc} in the back-office trade-book. "
                "If you trade through another channel, it may not be reflected here."}],
            "citations": [], "model": None,
        }
    return {
        "blocks": [
            {"type": "text", "text": f"Here are your last {min(len(trades), limit)} trades on record."},
            {"type": "transactions_list", "data": {"ucc": ucc, "trades": trades, "total": data.get("total", 0)}},
        ],
        "citations": [], "model": None,
    }


async def _t_deposits_withdrawals(ucc: str, args: Dict[str, Any]) -> Dict[str, Any]:
    limit = int(args.get("limit") or 5)
    data = await client_api.client_deposits_withdrawals(ucc, limit=limit)
    dep = data.get("deposits") or []
    wdr = data.get("withdrawals") or []
    if not dep and not wdr:
        return {
            "blocks": [{"type": "text", "text":
                "I don't see any deposits or withdrawals on file in the back-office for this account."}],
            "citations": [], "model": None,
        }
    return {
        "blocks": [
            {"type": "text", "text":
                f"Showing your latest {len(dep)} deposit{'' if len(dep)==1 else 's'} and "
                f"{len(wdr)} withdrawal{'' if len(wdr)==1 else 's'}."},
            {"type": "money_flow", "data": {"ucc": ucc, "deposits": dep, "withdrawals": wdr}},
        ],
        "citations": [], "model": None,
    }


async def _t_mf_holdings(ucc: str, _args: Dict[str, Any]) -> Dict[str, Any]:
    data = await client_api.client_mf_holdings(ucc=ucc)
    folios = data.get("folios") or []
    if not folios:
        return {
            "blocks": [{"type": "text", "text":
                data.get("note") or "I don't see any mutual-fund folios on file for you."}],
            "citations": [], "model": None,
        }
    return {
        "blocks": [
            {"type": "text", "text": f"You hold {len(folios)} mutual-fund folio{'' if len(folios)==1 else 's'} on record."},
            {"type": "mf_folios_list", "data": {"folios": folios}},
        ],
        "citations": [], "model": None,
    }


async def _t_mf_sips(ucc: str, _args: Dict[str, Any]) -> Dict[str, Any]:
    data = await client_api.client_mf_sips(ucc=ucc)
    sips = data.get("sips") or []
    if not sips:
        return {
            "blocks": [{"type": "text", "text":
                data.get("note") or "I don't see any active SIPs on file for you."}],
            "citations": [], "model": None,
        }
    return {
        "blocks": [
            {"type": "text", "text": f"You have {len(sips)} active SIP{'' if len(sips)==1 else 's'} registered."},
            {"type": "sip_list", "data": {"sips": sips}},
        ],
        "citations": [], "model": None,
    }


# ---------- dispatch ----------
async def execute(tool_name: str, args: Dict[str, Any],
                  session_id: str, identity_obj: Dict[str, Any]) -> Dict[str, Any]:
    """Run a client_* tool; return {blocks, citations, model}.

    `identity_obj` is the verified-client identity. We pull the UCC + PAN
    from it — the LLM cannot influence which account is queried.
    """
    ucc = identity_obj.get("ucc")
    if not ucc:
        return {
            "blocks": [{"type": "text",
                        "text": "I couldn't resolve your account on file. Please re-verify so I can pull your details."}],
            "citations": [], "model": None,
        }
    ck = _key(session_id, tool_name, args)
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    try:
        if tool_name == "client_portfolio":
            out = await _t_portfolio(ucc, args)
        elif tool_name == "client_ledger_balance":
            out = await _t_ledger_balance(ucc, args)
        elif tool_name == "client_recent_trades":
            out = await _t_recent_trades(ucc, args)
        elif tool_name == "client_deposits_withdrawals":
            out = await _t_deposits_withdrawals(ucc, args)
        elif tool_name == "client_mf_holdings":
            out = await _t_mf_holdings(ucc, args)
        elif tool_name == "client_mf_sips":
            out = await _t_mf_sips(ucc, args)
        else:
            return {"blocks": [{"type": "text", "text": f"Unsupported client tool: {tool_name}."}],
                    "citations": [], "model": None}
    except Exception as e:
        logger.exception("client tool %s failed: %s", tool_name, e)
        return {"blocks": [{"type": "text",
                            "text": "I'm having trouble reading your back-office snapshot right now. Please try again in a moment."}],
                "citations": [], "model": None}

    _cache_put(ck, out)
    return out


# ---------- Router-facing tool catalogue ----------
CLIENT_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "client_portfolio",
            "description": (
                "Return the verified client's current cash-market equity holdings (symbol, "
                "qty, avg price, LTP, P&L) from the SMIFS back-office. "
                "WHEN TO USE: 'what are my holdings', 'show my portfolio', 'how many shares of X do I own'. "
                "WHEN NOT TO USE: questions about MF folios (use client_mf_holdings) or about generic market data."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "client_ledger_balance",
            "description": (
                "Return the verified client's running ledger balance (credits, debits, current cash) "
                "from the back-office. "
                "WHEN TO USE: 'what's my account balance', 'how much cash do I have', 'show my ledger balance'. "
                "WHEN NOT TO USE: questions about MF AUM or holdings — those are different stacks."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "client_recent_trades",
            "description": (
                "Return the verified client's most recent trades (date, scrip, side, qty, price) "
                "from contract notes. "
                "WHEN TO USE: 'recent trades', 'last trade I did', 'show my trade book'. "
                "WHEN NOT TO USE: questions about current holdings (use client_portfolio) or P&L summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Number of recent trades to return (default 10)."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "client_deposits_withdrawals",
            "description": (
                "Return the verified client's recent fund movements (deposits + withdrawals) "
                "from the back-office. "
                "WHEN TO USE: 'recent deposits', 'when did I withdraw money', 'fund inflow/outflow'. "
                "WHEN NOT TO USE: questions about brokerage charges or P&L."
            ),
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 25, "description": "Rows per side (default 5)."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "client_mf_holdings",
            "description": (
                "Return the verified client's mutual-fund folios — schemes held, units, AUM. "
                "WHEN TO USE: 'mutual fund holdings', 'my MF folios', 'what MFs do I own'. "
                "WHEN NOT TO USE: questions about equity portfolio (use client_portfolio)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "client_mf_sips",
            "description": (
                "Return the verified client's active SIPs (scheme, amount, frequency, next debit). "
                "WHEN TO USE: 'my SIPs', 'show my SIPs', 'what SIPs am I running'. "
                "WHEN NOT TO USE: questions about one-time MF transactions or equity trades."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

CLIENT_TOOL_NAMES = {t["function"]["name"] for t in CLIENT_TOOLS}
