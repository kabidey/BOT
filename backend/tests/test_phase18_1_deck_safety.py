"""Phase 18.1 acceptance tests — safety asks A & B.

* A. Hard 2.5s latency budget: a slow deck client must trigger
  `asyncio.TimeoutError`, return `[]`, and log a `deck_search_timeout`
  security event.
* B. Local threshold guard: `rag_agent._retrieve` must NOT fall back to
  the deck when local has any hit ≥ 0.20 (semi-relevant). The deck must
  fire only when local is truly empty.
* Plus: enrichment merges local fields onto deck hits; suspenders gate
  drops `audience=employee_only` for non-employees.
"""
from __future__ import annotations
import asyncio
import sys
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

sys.path.insert(0, "/app/backend")

from agents import deck_search, rag_agent  # noqa: E402


# ---------- helpers ----------
class _FakeCollection:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = list(rows)
        self.inserts: List[Dict[str, Any]] = []

    def find(self, *args, **kwargs):
        rows = self._rows
        if args and isinstance(args[0], dict) and "smifs_id" in args[0]:
            ids = args[0]["smifs_id"].get("$in", [])
            rows = [r for r in self._rows if r.get("smifs_id") in ids]
        return _Cursor(rows)

    async def estimated_document_count(self):
        return 0

    async def insert_one(self, row):
        self.inserts.append(row)
        return MagicMock()

    async def delete_many(self, *args, **kwargs):
        return MagicMock()


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
    def sort(self, *_, **__): return self
    def limit(self, *_, **__): return self
    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield r
        return gen()


class _FakeDB:
    def __init__(self, doc_chunks: List[Dict[str, Any]]):
        self.doc_chunks = _FakeCollection(doc_chunks)
        self.deck_search_calls = _FakeCollection([])
        self.security_events = _FakeCollection([])


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    deck_search._reset_state()
    # Pin module-level constants so the tests don't depend on host env.
    monkeypatch.setattr(deck_search, "DECK_BASE", "https://deck.example.test")
    monkeypatch.setattr(deck_search, "DECK_KEY", "test-key")
    monkeypatch.setenv("DECK_SEARCH_FALLBACK", "true")
    monkeypatch.setenv("DECK_SEARCH_MIN_SCORE", "0.45")
    yield
    deck_search._reset_state()


# ================================================================
# Safety ask A — hard 2.5s latency budget
# ================================================================
@pytest.mark.asyncio
async def test_deck_timeout_returns_empty_and_logs_security_event(monkeypatch):
    """A 3s mocked deck call MUST be aborted by the 2.5s asyncio.wait_for
    budget, return [], and log a `deck_search_timeout` security event."""
    # Tight budget so the test is fast.
    monkeypatch.setattr(deck_search, "DECK_TIMEOUT_S", 0.20)

    async def _slow(*_a, **_kw):
        await asyncio.sleep(0.50)   # 2.5x the 0.20s budget
        return MagicMock(status_code=200, json=lambda: {"totalIndexed": 1, "results": []})

    monkeypatch.setattr(deck_search, "_post_deck", _slow)

    db = _FakeDB(doc_chunks=[])
    out = await deck_search.deck_search("anything", top_k=4, db=db,
                                         session_type="visitor", auth_state="anonymous")
    assert out == [], "timeout MUST short-circuit to empty results"

    # security_events row written
    kinds = [r.get("kind") for r in db.security_events.inserts]
    assert "deck_search_timeout" in kinds, f"expected deck_search_timeout in {kinds}"

    # status snapshot reflects the timeout
    snap = deck_search.status()
    assert snap["timeouts_today"] == 1
    assert snap["p50_latency_ms_last_50"] is not None
    # And the call log carries the timeout marker
    assert any(c.get("status") == "timeout" for c in snap["last_10_calls"])


@pytest.mark.asyncio
async def test_deck_slow_response_logs_warning_not_timeout(monkeypatch):
    """A successful but slow (>SLOW_RESPONSE_MS) call logs a
    `deck_search_slow_response` warning. The call still returns its hits."""
    monkeypatch.setattr(deck_search, "DECK_TIMEOUT_S", 5.0)
    monkeypatch.setattr(deck_search, "SLOW_RESPONSE_MS", 50)  # tight to trigger easily

    async def _slowish(*_a, **_kw):
        await asyncio.sleep(0.10)   # 100ms > 50ms slow threshold but < 5s budget
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: {
            "totalIndexed": 9,
            "results": [
                {"id": "bedrock:abc:0", "score": 0.6, "source": "bedrock",
                 "title": "OK", "content": "x"},
            ],
        }
        return mock_resp

    monkeypatch.setattr(deck_search, "_post_deck", _slowish)

    db = _FakeDB(doc_chunks=[])
    out = await deck_search.deck_search("q", top_k=3, db=db,
                                         session_type="employee", auth_state="verified")
    assert len(out) == 1
    kinds = [r.get("kind") for r in db.security_events.inserts]
    assert "deck_search_slow_response" in kinds, f"expected slow_response in {kinds}"
    assert "deck_search_timeout" not in kinds
    snap = deck_search.status()
    assert snap["slow_responses_today"] == 1
    assert snap["timeouts_today"] == 0


# ================================================================
# Safety ask B — local threshold guard
# ================================================================
@pytest.mark.asyncio
async def test_local_threshold_guard_blocks_deck_fallback(monkeypatch):
    """When local returns ANY hit with score ≥ LOCAL_FLOOR (semi-relevant) but
    below RAG_MIN_SCORE, we MUST NOT call deck_search. The user's best
    sub-threshold local hit stays. This catches the `academy` regression
    pattern flagged in `coverage_parity.md`.

    RAG_MIN_SCORE = 0.15 and LOCAL_FLOOR = 0.10 in the current build, so a
    hit of 0.12 is "borderline semi-relevant" — not grounded, but local has
    *something*, so no deck call."""
    sub_threshold_local = [
        {"doc_id": "local-1", "doc_title": "Some academy chunk", "section": "a",
         "text": "Generic education on AIF…", "source": "smifs_knowledge",
         "subsource": "academy", "score": 0.12, "raw_score": 0.12,
         "audience": "all", "doc_type": "academy"},
    ]
    monkeypatch.setattr("rag.search_weighted", AsyncMock(return_value=sub_threshold_local))
    deck_calls = []
    async def _spy_deck(*args, **kwargs):
        deck_calls.append((args, kwargs))
        return []
    monkeypatch.setattr(deck_search, "deck_search", _spy_deck)

    hits, grounded, _ = await rag_agent._retrieve(
        "what is an aif?", session_type="visitor", auth_state="anonymous",
    )
    assert deck_calls == [], "deck_search MUST NOT fire when local has any hit ≥ LOCAL_FLOOR"
    assert grounded is False, "0.12 is below RAG_MIN_SCORE (0.15) so grounded must stay False"
    assert hits == sub_threshold_local, "local hits must be preserved"


@pytest.mark.asyncio
async def test_local_truly_empty_triggers_deck_fallback(monkeypatch):
    """When local has NOTHING above LOCAL_FLOOR (0.10), deck_search IS called."""
    barely_local = [
        {"doc_id": "local-x", "doc_title": "x", "section": "a", "text": "...",
         "source": "smifs_knowledge", "subsource": "academy", "score": 0.05,
         "raw_score": 0.05, "audience": "all", "doc_type": "academy"},
    ]
    monkeypatch.setattr("rag.search_weighted", AsyncMock(return_value=barely_local))
    deck_calls = []
    async def _spy_deck(*args, **kwargs):
        deck_calls.append(kwargs)
        return []
    monkeypatch.setattr(deck_search, "deck_search", _spy_deck)

    await rag_agent._retrieve("x", session_type="visitor", auth_state="anonymous")
    assert len(deck_calls) == 1, "deck must fire when local top score is < LOCAL_FLOOR (0.10)"


# ================================================================
# Enrichment + belt-and-suspenders audience gate
# ================================================================
@pytest.mark.asyncio
async def test_deck_enrichment_merges_local_fields(monkeypatch):
    """A deck hit whose `id` matches a local `smifs_id` must come out of
    `deck_search()` carrying the local audience / vehicle_id / version_no /
    is_focused / updated_at_iso fields."""
    async def _ok(*_a, **_kw):
        m = MagicMock()
        m.status_code = 200
        m.json = lambda: {
            "totalIndexed": 9,
            "results": [
                {"id": "vehicle:abc-123:0", "score": 0.62, "source": "vehicle",
                 "title": "Sapphire AIF", "content": "..."},
            ],
        }
        return m
    monkeypatch.setattr(deck_search, "_post_deck", _ok)

    db = _FakeDB(doc_chunks=[{
        "smifs_id": "vehicle:abc-123:0", "audience": "all",
        "vehicle_id": "abc-123", "vehicle_name": "Sapphire AIF",
        "vehicle_type": "AIF", "version_no": 8, "is_focused": True,
        "is_active": True, "updated_at_iso": "2026-03-24T00:00:00+00:00",
        "subsource": "vehicle", "doc_type": "vehicle",
    }])
    out = await deck_search.deck_search("aif", top_k=3, db=db,
                                         session_type="employee", auth_state="verified")
    assert len(out) == 1
    h = out[0]
    assert h["vehicle_id"] == "abc-123"
    assert h["vehicle_name"] == "Sapphire AIF"
    assert h["version_no"] == 8
    assert h["is_focused"] is True
    assert h["updated_at_iso"] == "2026-03-24T00:00:00+00:00"
    assert h["source_engine"] == "deck_search"
    assert h["relevance"] == 0.62


@pytest.mark.asyncio
async def test_belt_and_suspenders_drops_employee_only_audience_for_visitor(monkeypatch):
    """Even if the deck returns a chunk whose deck `source` looks innocuous
    (e.g. `bedrock`), if our LOCAL enrichment marks it `audience=employee_only`,
    the visitor MUST NOT see it."""
    async def _ok(*_a, **_kw):
        m = MagicMock()
        m.status_code = 200
        m.json = lambda: {
            "totalIndexed": 9,
            "results": [
                {"id": "bedrock:emp:0", "score": 0.6, "source": "bedrock",
                 "title": "Internal-only bedrock slide", "content": "..."},
                {"id": "bedrock:pub:0", "score": 0.55, "source": "bedrock",
                 "title": "Public slide", "content": "..."},
            ],
        }
        return m
    monkeypatch.setattr(deck_search, "_post_deck", _ok)

    db = _FakeDB(doc_chunks=[
        {"smifs_id": "bedrock:emp:0", "audience": "employee_only",
         "subsource": "bedrock", "doc_type": "bedrock"},
        {"smifs_id": "bedrock:pub:0", "audience": "all",
         "subsource": "bedrock", "doc_type": "bedrock"},
    ])
    out = await deck_search.deck_search("q", top_k=5, db=db,
                                         session_type="visitor", auth_state="anonymous")
    titles = [h["doc_title"] for h in out]
    assert titles == ["Public slide"], f"employee-only chunk leaked: {titles}"


def test_sources_filter_for_visitor_excludes_growth():
    """The visitor pre-filter MUST omit growth_* subsources but include
    bedrock / vehicle / academy / sales_pitch / document."""
    src = deck_search._sources_for("visitor", "anonymous")
    assert src is not None
    assert "growth_insurance" not in src
    assert "growth_revenue" not in src
    assert "bedrock" in src and "vehicle" in src and "academy" in src
    assert "sales_pitch" in src and "document" in src


def test_sources_filter_for_verified_employee_is_unrestricted():
    """Verified employees see everything (None == no filter sent)."""
    assert deck_search._sources_for("employee", "verified") is None
