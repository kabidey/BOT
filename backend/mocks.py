"""Mock seed data for Phase 2 — clients + market quotes."""
from __future__ import annotations
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MOCK_CLIENTS = [
    {
        "code": "SMIFS001",
        "name": "Aarav Mehta",
        "phone": "+919900112233",
        "city": "Mumbai",
        "holdings_summary": "₹1.2 Cr across NCDs (40%), AIF Cat II (35%) and equity mutual funds (25%).",
        "verify_questions": [
            {"q": "Year of birth", "a": "1978"},
            {"q": "City of registration", "a": "Mumbai"},
        ],
    },
    {
        "code": "SMIFS002",
        "name": "Priya Iyer",
        "phone": "+919811223344",
        "city": "Bengaluru",
        "holdings_summary": "₹2.6 Cr across PMS discretionary (60%), liquid funds (15%) and direct equity (25%).",
        "verify_questions": [
            {"q": "Year of birth", "a": "1982"},
            {"q": "City of registration", "a": "Bengaluru"},
        ],
    },
    {
        "code": "SMIFS003",
        "name": "Rohan Sharma",
        "phone": "+919812345678",
        "city": "Delhi",
        "holdings_summary": "₹85 L across hybrid mutual funds (55%) and short-duration debt funds (45%).",
        "verify_questions": [
            {"q": "Year of birth", "a": "1990"},
            {"q": "City of registration", "a": "Delhi"},
        ],
    },
    {
        "code": "SMIFS004",
        "name": "Anaya Reddy",
        "phone": "+919833445566",
        "city": "Hyderabad",
        "holdings_summary": "₹4.5 Cr across AIF Cat III (30%), PMS (40%) and unlisted equity (30%).",
        "verify_questions": [
            {"q": "Year of birth", "a": "1975"},
            {"q": "City of registration", "a": "Hyderabad"},
        ],
    },
    {
        "code": "SMIFS005",
        "name": "Vikram Joshi",
        "phone": "+919844556677",
        "city": "Pune",
        "holdings_summary": "₹65 L across NCDs (70%) and equity mutual funds (30%).",
        "verify_questions": [
            {"q": "Year of birth", "a": "1985"},
            {"q": "City of registration", "a": "Pune"},
        ],
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _market_seed() -> list:
    ts = _now_iso()
    return [
        {"symbol": "RELIANCE", "name": "Reliance Industries Ltd.", "kind": "equity", "last_price": 2842.55, "change_pct": 1.24, "currency": "INR", "as_of": ts, "exchange": "NSE"},
        {"symbol": "HDFCBANK", "name": "HDFC Bank Ltd.", "kind": "equity", "last_price": 1675.10, "change_pct": -0.42, "currency": "INR", "as_of": ts, "exchange": "NSE"},
        {"symbol": "TCS", "name": "Tata Consultancy Services", "kind": "equity", "last_price": 3915.80, "change_pct": 0.68, "currency": "INR", "as_of": ts, "exchange": "NSE"},
        {"symbol": "INFY", "name": "Infosys Ltd.", "kind": "equity", "last_price": 1684.25, "change_pct": -1.15, "currency": "INR", "as_of": ts, "exchange": "NSE"},
        {"symbol": "ITC", "name": "ITC Ltd.", "kind": "equity", "last_price": 442.70, "change_pct": 0.34, "currency": "INR", "as_of": ts, "exchange": "NSE"},
        {"symbol": "SBIBLUECHIP", "name": "SBI Bluechip Fund (Direct, Growth)", "kind": "fund", "last_price": 88.43, "change_pct": 0.21, "currency": "INR", "as_of": ts, "exchange": "AMFI"},
        {"symbol": "ICICIPRUBLUECHIP", "name": "ICICI Prudential Bluechip Fund (Direct, Growth)", "kind": "fund", "last_price": 102.78, "change_pct": 0.18, "currency": "INR", "as_of": ts, "exchange": "AMFI"},
        {"symbol": "AXISLTE", "name": "Axis Long Term Equity Fund (Direct, Growth)", "kind": "fund", "last_price": 96.12, "change_pct": -0.27, "currency": "INR", "as_of": ts, "exchange": "AMFI"},
        {"symbol": "HDFCMIDCAP", "name": "HDFC Mid-Cap Opportunities (Direct, Growth)", "kind": "fund", "last_price": 154.85, "change_pct": 0.91, "currency": "INR", "as_of": ts, "exchange": "AMFI"},
        {"symbol": "MIRAELARGECAP", "name": "Mirae Asset Large Cap Fund (Direct, Growth)", "kind": "fund", "last_price": 110.42, "change_pct": 0.08, "currency": "INR", "as_of": ts, "exchange": "AMFI"},
    ]


async def seed_if_empty(db) -> None:
    """Idempotent — only seeds collections if they are empty."""
    if await db.mock_clients.count_documents({}) == 0:
        await db.mock_clients.insert_many([dict(c) for c in MOCK_CLIENTS])
        logger.info("Seeded mock_clients: %d", len(MOCK_CLIENTS))
    if await db.mock_market.count_documents({}) == 0:
        seed = _market_seed()
        await db.mock_market.insert_many([dict(m) for m in seed])
        logger.info("Seeded mock_market: %d", len(seed))
