"""API specialist agent — mock client lookup + market data.

Phase 2 mock: data lives in MongoDB collections `mock_clients` and `mock_market`.
Phase 3 will add real auth (verification questions) before exposing client holdings.
"""
from __future__ import annotations
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---- regexes for extraction ----
_CLIENT_CODE_RE = re.compile(r"\bSMIFS\d{3,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\+?\d[\d \-]{8,}\d")
# Tickers: uppercased letters between word boundaries, length 3-10
_TICKER_RE = re.compile(r"\b([A-Z]{3,10})\b")


def extract_client_identifier(message: str) -> Optional[str]:
    m = _CLIENT_CODE_RE.search(message)
    if m:
        return m.group(0).upper()
    p = _PHONE_RE.search(message)
    if p:
        return re.sub(r"[\s\-]", "", p.group(0))
    return None


def extract_market_query(message: str, fallback_subject: Optional[str] = None) -> Optional[str]:
    """Return either a ticker (uppercased) or a free-text fund-name query."""
    if fallback_subject:
        # Use it as-is; if it looks like a ticker, uppercase it.
        s = fallback_subject.strip()
        if re.fullmatch(r"[A-Za-z]{2,10}", s):
            return s.upper()
        if s:
            return s
    # Try uppercase ticker first
    tickers = _TICKER_RE.findall(message)
    # Filter out common english words
    stop = {"THE", "AND", "FOR", "WITH", "WHAT", "PLEASE", "TELL", "ME", "PRICE", "NAV", "FUND", "SHARE", "STOCK"}
    candidates = [t for t in tickers if t not in stop]
    if candidates:
        return candidates[0]
    # Free-text — return the message itself (will be substring-matched)
    return message.strip() or None


# ---------- DB lookups ----------
async def lookup_client(db, identifier: str) -> Dict[str, Any]:
    """Find a mock client by code or phone. Returns {found, ...}."""
    ident = identifier.strip()
    code_query = {"code": ident.upper()}
    phone_clean = re.sub(r"[\s\-]", "", ident)
    phone_query = {"phone": phone_clean}
    doc = await db.mock_clients.find_one(
        {"$or": [code_query, phone_query]},
        {"_id": 0, "verify_questions": 0},  # never expose verify Qs in lookup result
    )
    if not doc:
        return {"found": False, "identifier": identifier}
    return {"found": True, **doc}


async def fetch_market_data(db, query: str) -> Optional[Dict[str, Any]]:
    """Lookup ticker exact, then case-insensitive substring on name."""
    q = query.strip()
    # Exact symbol match
    doc = await db.mock_market.find_one({"symbol": q.upper()}, {"_id": 0})
    if doc:
        return doc
    # Substring on name (case-insensitive)
    pattern = re.escape(q)
    doc = await db.mock_market.find_one(
        {"name": {"$regex": pattern, "$options": "i"}}, {"_id": 0}
    )
    return doc


async def list_available_market_symbols(db, limit: int = 6) -> List[str]:
    cursor = db.mock_market.find({}, {"_id": 0, "symbol": 1}).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [r["symbol"] for r in rows]
