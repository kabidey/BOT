"""Phase 24c — Live BMIA client + 4 LLM tool wrappers.

BMIA (`https://bmia.in/api/public/v1`) provides:
  * compliance search/research over SEBI/RBI/MCA/NSE/BSE/IRDAI corpus
  * NSE fundamentals lookup
  * daily market briefing (board meetings, critical filings, insider activity)

Defensive controls (BMIA returns NO `X-RateLimit-*` headers — we self-throttle):
  * 30 calls/min per process (asyncio semaphore + sliding window)
  * 60s LRU cache keyed by full request signature
  * Exponential backoff on 5xx (3 retries: 1s → 2s → 4s)
  * Per-endpoint timeouts (10s search / 60s research / 8s fundamentals + briefing)
  * Errors logged to `errors` collection with `tool="bmia.<endpoint>"`
  * Telemetry summary at `bmia.summary()` for the Admin Diagnostics tile.
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

import httpx

logger = logging.getLogger(__name__)

BMIA_API_KEY = os.environ.get("BMIA_API_KEY", "")
BMIA_API_BASE = os.environ.get("BMIA_API_BASE", "https://bmia.in/api/public/v1").rstrip("/")

# ----- Rate limit + cache state -----
_RATE_WINDOW_SEC = 60
_RATE_MAX = int(os.environ.get("BMIA_RATE_PER_MIN", "30"))
_recent_call_ts: collections.deque = collections.deque(maxlen=_RATE_MAX * 4)
_rate_lock = asyncio.Lock()

_CACHE_TTL = int(os.environ.get("BMIA_CACHE_TTL_SEC", "60"))
_CACHE_MAX = 256
_cache: "collections.OrderedDict[str, tuple[float, Any]]" = collections.OrderedDict()

# ----- Telemetry counters for Admin tile -----
_counters: Dict[str, Dict[str, int]] = collections.defaultdict(lambda: {
    "calls": 0, "ok": 0, "err": 0, "cache_hit": 0,
})
_last_errors: collections.deque = collections.deque(maxlen=5)

# Optional Mongo handle for error persistence — bound by server.py.
_db_handle = None


def bind_db(db) -> None:
    global _db_handle
    _db_handle = db


def _sig(endpoint: str, payload: Any) -> str:
    raw = endpoint + "::" + json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _cache_get(key: str) -> Optional[Any]:
    row = _cache.get(key)
    if not row:
        return None
    exp, val = row
    if exp < time.time():
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return val


def _cache_put(key: str, val: Any) -> None:
    _cache[key] = (time.time() + _CACHE_TTL, val)
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


async def _rate_gate() -> None:
    """Sliding-window: at most _RATE_MAX calls per _RATE_WINDOW_SEC seconds."""
    async with _rate_lock:
        now = time.monotonic()
        while _recent_call_ts and _recent_call_ts[0] < now - _RATE_WINDOW_SEC:
            _recent_call_ts.popleft()
        if len(_recent_call_ts) >= _RATE_MAX:
            wait = _RATE_WINDOW_SEC - (now - _recent_call_ts[0]) + 0.05
            logger.warning("bmia rate-cap hit; sleeping %.2fs", wait)
            await asyncio.sleep(max(0.05, wait))
        _recent_call_ts.append(time.monotonic())


async def _log_error(endpoint: str, payload: Any, err: str) -> None:
    _last_errors.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint, "error": err[:240],
    })
    if _db_handle is None:
        return
    try:
        await _db_handle.errors.insert_one({
            "tool": f"bmia.{endpoint.strip('/').split('/')[0]}",
            "endpoint": endpoint,
            "query": payload,
            "error": err[:500],
            "ts": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass


async def _call(method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None,
                query_params: Optional[Dict[str, Any]] = None,
                timeout: float = 10.0) -> Dict[str, Any]:
    """Single HTTP call with retry, cache, and rate-limiting."""
    endpoint = path.lstrip("/")
    sig_payload: Dict[str, Any] = {"method": method, "path": path}
    if json_body:
        sig_payload["body"] = json_body
    if query_params:
        sig_payload["query"] = query_params
    key = _sig(endpoint, sig_payload)
    cached = _cache_get(key)
    if cached is not None:
        _counters[endpoint]["calls"] += 1
        _counters[endpoint]["cache_hit"] += 1
        return cached

    if not BMIA_API_KEY:
        raise RuntimeError("BMIA_API_KEY not set")

    await _rate_gate()
    _counters[endpoint]["calls"] += 1
    headers = {"Authorization": f"Bearer {BMIA_API_KEY}",
               "Content-Type": "application/json"}
    url = f"{BMIA_API_BASE}{path}"

    last_status = None
    last_body: Optional[str] = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                if method.upper() == "POST":
                    resp = await http.post(url, headers=headers, json=json_body)
                else:
                    resp = await http.get(url, headers=headers, params=query_params)
            if resp.status_code == 200:
                data = resp.json()
                _cache_put(key, data)
                _counters[endpoint]["ok"] += 1
                return data
            last_status = resp.status_code
            last_body = resp.text[:240]
            if resp.status_code in (500, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(1.0 * (2 ** attempt))
                continue
            break
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            last_status = -1
            last_body = f"network/timeout: {e}"
            if attempt < 2:
                await asyncio.sleep(1.0 * (2 ** attempt))
                continue
            break
    _counters[endpoint]["err"] += 1
    err = f"HTTP {last_status}: {last_body}"
    await _log_error(endpoint, sig_payload, err)
    raise RuntimeError(f"bmia {endpoint} failed: {err}")


# ============================================================
# Public API — exactly the 4 surfaces the LLM tool wrappers use
# ============================================================
async def compliance_search(query: str, sources: Optional[List[str]] = None,
                              top_k: int = 5) -> Dict[str, Any]:
    body: Dict[str, Any] = {"query": query, "top_k": top_k}
    if sources:
        body["sources"] = sources
    return await _call("POST", "/compliance/search", json_body=body, timeout=10.0)


async def compliance_research(query: str, sources: Optional[List[str]] = None,
                                top_k: int = 5) -> Dict[str, Any]:
    body: Dict[str, Any] = {"query": query, "top_k": top_k}
    if sources:
        body["sources"] = sources
    return await _call("POST", "/compliance/research", json_body=body, timeout=60.0)


async def fundamentals(symbol: str,
                        slice: Literal["profile", "quarterly", "trends", "ratios", "full"] = "profile",
                        ) -> Dict[str, Any]:
    sym = (symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol required")
    raw = await _call("GET", f"/fundamentals/{sym}", timeout=8.0)
    return _slice_fundamentals(raw, slice)


def _slice_fundamentals(raw: Dict[str, Any], slice_kind: str) -> Dict[str, Any]:
    """Trim the (very large) fundamentals payload to just the slice the LLM
    asked for, so context-window usage stays sane."""
    if slice_kind == "full":
        return raw
    base = {
        "symbol": raw.get("symbol"),
        "about": raw.get("about"),
        "last_fetched": raw.get("last_fetched"),
        "pros": raw.get("pros") or [],
        "cons": raw.get("cons") or [],
    }
    if slice_kind == "profile":
        # Just the headline: about + pros/cons + a 3-year P&L slice.
        pl = raw.get("profit_loss_table") or {}
        periods = (pl.get("periods") or [])[-3:]
        rows = pl.get("rows") or {}
        base["profit_loss_3y"] = {
            "periods": periods,
            "rows": {k: (v[-3:] if isinstance(v, list) and len(v) >= 3 else v)
                     for k, v in rows.items()},
        }
        return base
    if slice_kind == "quarterly":
        q = raw.get("quarterly_table") or {}
        periods = (q.get("periods") or [])[-4:]
        rows = q.get("rows") or {}
        base["quarterly_last_4"] = {
            "periods": periods,
            "rows": {k: (v[-4:] if isinstance(v, list) and len(v) >= 4 else v)
                     for k, v in rows.items()},
        }
        return base
    if slice_kind == "trends":
        pl = raw.get("profit_loss_table") or {}
        cf = raw.get("cash_flow_table") or {}
        base["profit_loss"] = pl
        base["cash_flow"] = cf
        return base
    if slice_kind == "ratios":
        base["ratios"] = raw.get("ratios_table") or {}
        return base
    return raw


async def daily_briefing(date: Optional[str] = None,
                          sections: Optional[List[str]] = None) -> Dict[str, Any]:
    params: Dict[str, Any] = {"limit": 1}
    if date:
        params["date"] = date
    raw = await _call("GET", "/guidance/briefings", query_params=params, timeout=8.0)
    if not sections:
        return raw
    briefs = raw.get("briefings") or []
    out_briefs = []
    for b in briefs:
        slim = {"date": b.get("date"), "generated_at": b.get("generated_at")}
        for s in sections:
            if s in b:
                slim[s] = b[s]
        out_briefs.append(slim)
    return {"count": len(out_briefs), "briefings": out_briefs}


# ============================================================
# Tool registry — function-call schemas for the orchestrator
# ============================================================
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "bmia_compliance_research",
            "description": (
                "Search Indian financial-market regulatory corpus (SEBI, RBI, MCA, NSE, BSE, IRDAI) "
                "for circulars, regulations, disclosures, KYC rules, insider-trading rules, listing "
                "requirements, takeover codes, AML/CFT, mutual-fund regulations, derivative segment "
                "rules, etc. ALWAYS call this for ANY regulator-named question OR any question about "
                "compliance / disclosure / SEBI/RBI/MCA action. Returns ranked citations with "
                "source title, URL, date, and the most relevant chunk of body text. Use the result's "
                "`text_chunk` to compose your answer and cite the `url` + `title`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language compliance question. "
                              "Be specific: 'SEBI insider trading disclosure timelines' not just 'insider trading'."},
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["sebi", "rbi", "mca", "nse", "bse", "irdai"]},
                        "description": "Optional regulator filter. Omit to search ALL sources."
                    },
                    "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 15,
                              "description": "Number of ranked citations to return."}
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
                "Look up FUNDAMENTALS for an NSE-listed Indian stock: profile, last 12 years P&L, "
                "balance sheet, cash flow, last 13 quarters, ratios, pros/cons. Use ONLY when the "
                "user mentions a specific stock by name OR ticker. The `symbol` MUST be a valid NSE "
                "ticker in UPPERCASE (e.g. RELIANCE, HDFCBANK, TCS, INFY). If the user gives only a "
                "company name and you're not certain of the ticker, DO NOT GUESS — reply asking them "
                "to confirm the ticker. Pick `slice='profile'` for general intros, 'quarterly' for "
                "recent performance, 'trends' for multi-year tables, 'ratios' for valuation metrics, "
                "'full' only if user asked for everything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE ticker, uppercase, no exchange suffix. e.g. RELIANCE"},
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
            "name": "bmia_quarterly",
            "description": (
                "Convenience tool: returns ONLY the last 4 quarters of P&L for an NSE stock. "
                "Use when user asks about 'recent quarter', 'last 4 quarters', 'QoQ growth', "
                "'most recent results'. `symbol` MUST be a confirmed NSE ticker — see "
                "`bmia_fundamentals_lookup` for the same naming rule."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE ticker uppercase."},
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
                "Get TODAY's (or a specific date's) Indian-market briefing: board-meeting "
                "intimations, critical regulatory filings, and insider-trading disclosures across "
                "all NSE/BSE-listed companies. Use when user asks 'what's happening today / in the "
                "market', 'any critical filings this morning', 'today's announcements', 'any "
                "company updates today'. Pick `sections` to keep the response focused if the user "
                "asked only about e.g. insider activity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string",
                             "description": "Optional YYYY-MM-DD. Omit for today's briefing."},
                    "sections": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["board_meetings", "critical_filings", "insider_activity"]},
                        "description": "Optional section filter. Omit to get all."
                    },
                },
            },
        },
    },
]


# ============================================================
# Dispatcher — called by the orchestrator when a tool_call name
# starts with `bmia_`.
# ============================================================
async def execute(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Returns the same `{ok, value|error, ...}` envelope as OrgLens adapter."""
    t0 = time.monotonic()
    try:
        if tool_name == "bmia_compliance_research":
            data = await compliance_search(
                query=params.get("query") or "",
                sources=params.get("sources"),
                top_k=int(params.get("top_k") or 5),
            )
            value = _shape_compliance_results(data)
        elif tool_name == "bmia_fundamentals_lookup":
            data = await fundamentals(
                symbol=params.get("symbol") or "",
                slice=params.get("slice") or "profile",
            )
            value = data
        elif tool_name == "bmia_quarterly":
            data = await fundamentals(
                symbol=params.get("symbol") or "",
                slice="quarterly",
            )
            value = data
        elif tool_name == "bmia_daily_briefing":
            data = await daily_briefing(
                date=params.get("date"),
                sections=params.get("sections"),
            )
            value = data
        else:
            return {"ok": False, "tool_name": tool_name, "error": "unknown_bmia_tool"}
        return {"ok": True, "tool_name": tool_name, "value": value,
                "cache_hit": False, "latency_ms": int((time.monotonic() - t0) * 1000)}
    except ValueError as ve:
        return {"ok": False, "tool_name": tool_name, "error": "invalid_params",
                "detail": str(ve)[:200]}
    except Exception as e:
        logger.exception("bmia tool %s failed", tool_name)
        return {"ok": False, "tool_name": tool_name, "error": "bmia_unavailable",
                "detail": str(e)[:200]}


def _shape_compliance_results(data: Dict[str, Any]) -> Dict[str, Any]:
    """Transform BMIA compliance/search response into our internal citation
    schema (Phase 16 chip-friendly). LLM gets a slimmer payload."""
    results = data.get("results") or []
    chips: List[Dict[str, Any]] = []
    for r in results:
        chips.append({
            "doc_title": r.get("title"),
            "section": f"{r.get('category') or 'Circular'} · {r.get('date_iso') or ''}".strip(" ·"),
            "url": r.get("url"),
            "badge": (r.get("source") or "").upper(),
            "date_pill": r.get("date_iso"),
            "expand_text": (r.get("text_chunk") or "")[:1500],
            "score": r.get("score_rrf") or r.get("score") or 0.0,
            "source": r.get("source"),
            "circular_no": r.get("circular_no"),
        })
    return {"engine": data.get("engine"), "query": data.get("query"),
            "citations": chips, "result_count": len(chips)}


def summary() -> Dict[str, Any]:
    """Telemetry snapshot for Admin/Cost-Ledger tile."""
    return {
        "endpoints": {k: dict(v) for k, v in _counters.items()},
        "cache_size": len(_cache),
        "cache_ttl_sec": _CACHE_TTL,
        "rate_per_min": _RATE_MAX,
        "recent_errors": list(_last_errors),
    }
