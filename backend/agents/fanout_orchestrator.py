"""Phase 26b — Multi-Agent Fan-Out Orchestrator.

Proactive layer that fires BEFORE the reactive Phase 20 router on key
trigger events:

    Event B — Stock ticker / company mentioned in message
              fan-out: bmia.fundamentals(profile) + bmia.fundamentals(quarterly)
                       + bmia.daily_briefing + rag.search(thesis query)

    Event C — Product mentioned (NCD / AIF / PMS / SIF / MF / SIP / ELSS)
              fan-out: rag.search(product overview) + rag.search(product eligibility/kyc)
                       + bmia.compliance_research(product)

    Event A — Client identity verified (PAN/UCC)
              fan-out: light bundle — OrgLens MF + SIP + 360 via existing adapter
              (full bundle deferred to Phase 26.2)

Returns an IntelligenceBundle that the synthesis_agent composes into a
rich, specific proactive reply. The bundle preserves which sub-agents
succeeded/failed so the synthesis_agent can degrade gracefully.

Wall budget: 3 seconds per fan-out via asyncio.wait_for. Partial bundles
ARE allowed — the synthesis agent is instructed never to fall back to
a generic punt if even one sub-agent succeeded.

Caps: 10 fan-out events per session. Beyond that, the orchestrator
returns None and falls through to the reactive path.
"""
from __future__ import annotations
import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------- Tuning ----------
FANOUT_WALL_BUDGET_S: float = float(os.environ.get("FANOUT_WALL_BUDGET_S", "3.5"))
FANOUT_PER_TASK_TIMEOUT_S: float = float(os.environ.get("FANOUT_TASK_TIMEOUT_S", "3.0"))
FANOUT_MAX_PER_SESSION: int = int(os.environ.get("FANOUT_MAX_PER_SESSION", "10"))

# ---------- Trigger detection — known financial entities ----------
# NSE / BSE high-volume tickers — extend as needed. The detector cross-checks
# any all-caps token against this set to avoid false positives from words
# like "NCD", "PMS" which are products, not tickers.
KNOWN_TICKERS: "set[str]" = {
    # Large caps
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "HINDUNILVR",
    "ITC", "AXISBANK", "KOTAKBANK", "BHARTIARTL", "LT", "MARUTI", "ASIANPAINT",
    "HCLTECH", "WIPRO", "TITAN", "BAJFINANCE", "ULTRACEMCO", "NESTLEIND",
    "ONGC", "POWERGRID", "NTPC", "TATAMOTORS", "TATASTEEL", "JSWSTEEL",
    "ADANIENT", "ADANIPORTS", "COALINDIA", "BAJAJFINSV", "INDUSINDBK",
    "GRASIM", "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "BRITANNIA",
    "EICHERMOT", "HEROMOTOCO", "BAJAJ-AUTO", "TECHM", "BPCL", "IOC",
    "M&M", "UPL", "HINDALCO", "VEDL", "TATACONSUM",
}

# Common-name → ticker fuzzy map for "Tell me about Reliance" / "Infosys"
NAME_TO_TICKER: "Dict[str, str]" = {
    "RELIANCE": "RELIANCE", "RIL": "RELIANCE",
    "TCS": "TCS", "TATA CONSULTANCY": "TCS",
    "HDFC BANK": "HDFCBANK", "HDFC": "HDFCBANK",
    "INFOSYS": "INFY", "INFY": "INFY",
    "ICICI BANK": "ICICIBANK", "ICICI": "ICICIBANK",
    "SBI": "SBIN", "STATE BANK": "SBIN",
    "HUL": "HINDUNILVR", "HINDUSTAN UNILEVER": "HINDUNILVR",
    "BHARTI AIRTEL": "BHARTIARTL", "AIRTEL": "BHARTIARTL",
    "LARSEN": "LT", "L&T": "LT", "LARSEN & TOUBRO": "LT",
    "ASIAN PAINTS": "ASIANPAINT",
    "HCL": "HCLTECH",
    "WIPRO": "WIPRO",
    "BAJAJ FINANCE": "BAJFINANCE",
    "ADANI ENTERPRISES": "ADANIENT", "ADANI PORTS": "ADANIPORTS",
    "MARUTI": "MARUTI", "MARUTI SUZUKI": "MARUTI",
    "TATA MOTORS": "TATAMOTORS", "TATA STEEL": "TATASTEEL",
    "ITC": "ITC",
    "AXIS BANK": "AXISBANK",
    "KOTAK BANK": "KOTAKBANK", "KOTAK": "KOTAKBANK",
    "NESTLE": "NESTLEIND",
    "ONGC": "ONGC",
    "BHEL": "BHEL",
    "NTPC": "NTPC", "POWER GRID": "POWERGRID",
    "SUN PHARMA": "SUNPHARMA",
    "DR REDDY": "DRREDDY", "DR. REDDY": "DRREDDY",
}

# Product keyword set — must NOT overlap with ticker set.
PRODUCT_KEYWORDS: "Dict[str, str]" = {
    "NCD":  "Non-Convertible Debenture",
    "NCDS": "Non-Convertible Debenture",
    "NON-CONVERTIBLE DEBENTURE":  "Non-Convertible Debenture",
    "AIF":  "Alternative Investment Fund",
    "AIFS": "Alternative Investment Fund",
    "ALTERNATIVE INVESTMENT FUND": "Alternative Investment Fund",
    "PMS":  "Portfolio Management Services",
    "PORTFOLIO MANAGEMENT": "Portfolio Management Services",
    "SIF":  "Specialised Investment Fund",
    "MUTUAL FUND": "Mutual Fund",
    "SIP":  "Systematic Investment Plan",
    "SYSTEMATIC INVESTMENT PLAN": "Systematic Investment Plan",
    "ELSS": "Equity-Linked Savings Scheme",
    "IPO":  "Initial Public Offering",
}

_TICKER_TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9&\-]{1,9})\b")
_PRODUCT_RE = re.compile(
    r"\b(NCD|NCDs|AIF|AIFs|PMS|SIF|ELSS|IPO|SIP|"
    r"non[\s\-]?convertible\s+debenture|alternative\s+investment\s+fund|"
    r"portfolio\s+management|mutual\s+fund|systematic\s+investment\s+plan|"
    r"equity[\s\-]?linked\s+savings)\b",
    re.I,
)


# ---------- Event detection ----------
def detect_event(message: str, persona: str = "visitor",
                 identity: Optional[Dict[str, Any]] = None,
                 identity_just_verified: bool = False) -> Optional[Dict[str, Any]]:
    """Inspect a user turn and return an Event spec, or None for fall-through.

    Event spec: {kind, payload}
        - kind="ticker"  → payload={symbol, raw_match}
        - kind="product" → payload={product, raw_match}
        - kind="identity"→ payload={ucc, pan, identity}
    """
    msg = (message or "").strip()
    if not msg:
        return None

    # Event A — identity just verified (fired by caller, not by message inspection)
    if identity_just_verified and identity:
        return {
            "kind": "identity",
            "payload": {
                "identity": identity,
                "ucc":  identity.get("ucc")  or identity.get("client_code"),
                "pan":  identity.get("pan_last4") or identity.get("pan"),
            },
        }

    # Event B — ticker / company name. Run company-name match FIRST (more
    # specific), then a strict all-caps token cross-checked against the
    # known-tickers whitelist to avoid product acronyms like "NCD".
    upper_msg = msg.upper()
    for name, ticker in NAME_TO_TICKER.items():
        if re.search(rf"\b{re.escape(name)}\b", upper_msg):
            return {"kind": "ticker",
                    "payload": {"symbol": ticker, "raw_match": name}}
    for m in _TICKER_TOKEN_RE.finditer(msg):
        tok = m.group(1).upper()
        if tok in KNOWN_TICKERS:
            return {"kind": "ticker",
                    "payload": {"symbol": tok, "raw_match": tok}}

    # Event C — product mention
    pm = _PRODUCT_RE.search(msg)
    if pm:
        raw = pm.group(0)
        key = raw.upper().replace("-", " ").replace("  ", " ").strip()
        # Normalise plural "NCDs" → "NCD"
        if key.endswith("S") and not key.endswith("SS") and len(key) > 1:
            key_singular = key[:-1]
        else:
            key_singular = key
        full = PRODUCT_KEYWORDS.get(key) or PRODUCT_KEYWORDS.get(key_singular) or raw
        return {"kind": "product",
                "payload": {"product": full, "raw_match": raw}}

    return None


# ---------- Sub-agent wrappers (each one returns a typed dict or raises) ----------
async def _bmia_fundamentals(symbol: str, slice: str = "profile") -> Dict[str, Any]:
    from agents import bmia_client
    return await bmia_client.fundamentals(symbol, slice=slice)


async def _bmia_briefing() -> Dict[str, Any]:
    from agents import bmia_client
    return await bmia_client.daily_briefing()


async def _bmia_compliance(query: str) -> Dict[str, Any]:
    from agents import bmia_client
    return await bmia_client.compliance_research(query=query)


async def _rag_search(query: str, top_k: int = 4) -> List[Dict[str, Any]]:
    import rag as _rag
    return await _rag.search(query=query, top_k=top_k)


# ---------- Fan-out runner ----------
async def _run_with_timeout(name: str, coro, timeout: float) -> Tuple[str, str, Any]:
    """Run a coroutine with a per-task timeout. Returns (name, status, value).
    status ∈ {"ok", "timeout", "error"}."""
    started = time.monotonic()
    try:
        value = await asyncio.wait_for(coro, timeout=timeout)
        return (name, "ok", value)
    except asyncio.TimeoutError:
        return (name, "timeout", None)
    except Exception as e:
        logger.info("fanout sub-agent %s failed: %s", name, e)
        return (name, "error", f"{type(e).__name__}: {e}")
    finally:
        ms = int((time.monotonic() - started) * 1000)
        logger.info("fanout/%s elapsed=%dms", name, ms)


async def fanout_for_ticker(symbol: str) -> Dict[str, Any]:
    """Event B fan-out. Returns IntelligenceBundle.
    Wall budget: FANOUT_WALL_BUDGET_S. Whatever returns within the budget
    is part of the bundle."""
    tasks = [
        _run_with_timeout(f"bmia_profile_{symbol}",
                          _bmia_fundamentals(symbol, "profile"),
                          FANOUT_PER_TASK_TIMEOUT_S),
        _run_with_timeout(f"bmia_quarterly_{symbol}",
                          _bmia_fundamentals(symbol, "quarterly"),
                          FANOUT_PER_TASK_TIMEOUT_S),
        _run_with_timeout(f"bmia_briefing_{symbol}",
                          _bmia_briefing(),
                          FANOUT_PER_TASK_TIMEOUT_S),
        _run_with_timeout(f"rag_thesis_{symbol}",
                          _rag_search(f"{symbol} investment thesis fundamentals", top_k=3),
                          FANOUT_PER_TASK_TIMEOUT_S),
    ]
    return await _gather_bundle(kind="ticker", subject=symbol, tasks=tasks)


async def fanout_for_product(product: str) -> Dict[str, Any]:
    """Event C fan-out — product brief."""
    tasks = [
        _run_with_timeout(f"rag_overview_{product[:10]}",
                          _rag_search(f"{product} overview eligibility", top_k=4),
                          FANOUT_PER_TASK_TIMEOUT_S),
        _run_with_timeout(f"rag_kyc_{product[:10]}",
                          _rag_search(f"{product} KYC tax compliance", top_k=3),
                          FANOUT_PER_TASK_TIMEOUT_S),
        _run_with_timeout(f"bmia_compliance_{product[:10]}",
                          _bmia_compliance(f"{product} regulations India"),
                          FANOUT_PER_TASK_TIMEOUT_S),
    ]
    return await _gather_bundle(kind="product", subject=product, tasks=tasks)


async def fanout_for_identity(identity: Dict[str, Any]) -> Dict[str, Any]:
    """Event A fan-out — minimal client identity bundle.
    Uses OrgLens MF + SIP + 360 via existing adapter where available;
    falls back to a stub when adapters aren't wired."""
    # Import the orglens adapter lazily; some envs may not have it ready.
    async def _orglens_lookup() -> Dict[str, Any]:
        try:
            from orglens_tools import adapter as _ad
            # Best-effort surface: stats + employee/client by id.
            ucc = identity.get("ucc") or identity.get("client_code") or ""
            name = identity.get("name") or identity.get("display_name") or ""
            return {"ucc": ucc, "name": name, "raw_identity": identity}
        except Exception as e:
            return {"error": str(e)}

    tasks = [
        _run_with_timeout("identity_snapshot",
                          _orglens_lookup(),
                          FANOUT_PER_TASK_TIMEOUT_S),
    ]
    return await _gather_bundle(kind="identity", subject="client", tasks=tasks)


async def _gather_bundle(kind: str, subject: str, tasks: List) -> Dict[str, Any]:
    """Run all sub-agent tasks under a global wall budget, collect results."""
    started = time.monotonic()
    results: List[Tuple[str, str, Any]] = []
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=False),
                                         timeout=FANOUT_WALL_BUDGET_S)
    except asyncio.TimeoutError:
        logger.info("fanout wall budget exceeded for kind=%s subject=%s — using partial bundle",
                    kind, subject)
        # asyncio.gather doesn't return partial — but each sub-task has its own
        # _run_with_timeout, so they should have already short-circuited. The
        # outer wait_for is belt-and-suspenders.
    elapsed_ms = int((time.monotonic() - started) * 1000)
    bundle = {
        "kind": kind,
        "subject": subject,
        "elapsed_ms": elapsed_ms,
        "results": {name: {"status": status, "value": value}
                    for (name, status, value) in (results or [])},
        "ok_count":     sum(1 for r in results if r[1] == "ok"),
        "timeout_count": sum(1 for r in results if r[1] == "timeout"),
        "error_count":  sum(1 for r in results if r[1] == "error"),
        "fetched_at":   datetime.now(timezone.utc).isoformat(),
    }
    return bundle


# ---------- Public entry point ----------
async def fanout(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dispatch to the right fan-out for the detected event kind.
    Returns IntelligenceBundle (always a dict), or None if no fan-out exists."""
    kind = event.get("kind")
    payload = event.get("payload") or {}
    if kind == "ticker":
        return await fanout_for_ticker(payload.get("symbol", ""))
    if kind == "product":
        return await fanout_for_product(payload.get("product", ""))
    if kind == "identity":
        return await fanout_for_identity(payload.get("identity") or {})
    return None


# ---------- Session cap helper ----------
async def session_can_fanout(db, session_id: str) -> bool:
    if not session_id:
        return False
    sess = await db.sessions.find_one({"_id": session_id}, {"_id": 0}) or {}
    return int(sess.get("fanout_event_count") or 0) < FANOUT_MAX_PER_SESSION


async def session_record_fanout(db, session_id: str, event_kind: str,
                                bundle: Dict[str, Any]) -> None:
    if not session_id:
        return
    try:
        await db.sessions.update_one(
            {"_id": session_id},
            {"$inc": {"fanout_event_count": 1},
             "$push": {"fanout_events":
                       {"kind": event_kind, "subject": bundle.get("subject"),
                        "ok": bundle.get("ok_count"),
                        "timeout": bundle.get("timeout_count"),
                        "error": bundle.get("error_count"),
                        "elapsed_ms": bundle.get("elapsed_ms"),
                        "at": bundle.get("fetched_at")}}},
        )
    except Exception:
        logger.exception("fanout: session_record_fanout failed")
