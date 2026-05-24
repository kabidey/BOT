"""Phase 18.2 acceptance tests.

Three new safety guards:
  A. `documents_full` audience guard — drop these hits for visitor / client
     sessions, keep them for verified employees. Logged as
     `kb_documents_full_blocked_for_role` in `security_events`.
  B. `is_full_document_scan` flag — true on surviving `documents_full` hits
     (employee sessions only) so the FE renders the muted-grey accent.
  C. Timeout budget — bumped from 2.5s → 3.0s. Confirm a 2.8s call now
     succeeds (was timeout @ 2.5s) and a 3.5s call still times out.
"""
from __future__ import annotations
import asyncio
import sys
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "/app/backend")
from agents import deck_search  # noqa: E402


# ---------- shared fake DB (subset of motor) ----------
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
    async def estimated_document_count(self): return 0
    async def insert_one(self, row):
        self.inserts.append(row)
        return MagicMock()
    async def delete_many(self, *_, **__): return MagicMock()


class _FakeDB:
    def __init__(self, doc_chunks=None):
        self.doc_chunks = _FakeCollection(doc_chunks or [])
        self.deck_search_calls = _FakeCollection([])
        self.security_events = _FakeCollection([])


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    deck_search._reset_state()
    monkeypatch.setattr(deck_search, "DECK_BASE", "https://deck.example.test")
    monkeypatch.setattr(deck_search, "DECK_KEY", "test-key")
    monkeypatch.setenv("DECK_SEARCH_FALLBACK", "true")
    monkeypatch.setenv("DECK_SEARCH_MIN_SCORE", "0.45")
    yield
    deck_search._reset_state()


def _mock_post_factory(results, total_indexed=2486):
    """Build a `_post_deck` mock that returns the given results."""
    async def _ok(*_a, **_kw):
        m = MagicMock()
        m.status_code = 200
        m.json = lambda: {"totalIndexed": total_indexed, "results": results}
        return m
    return _ok


# ================================================================
# Guard A — `documents_full` blocked for visitor / client
# ================================================================
_DECK_DOCS_FULL = {
    "id": "documents_full:abc-123:0",
    "score": 0.55,
    "source": "documents_full",
    "sourceId": "abc-123",
    "title": "ASK Special Opportunities Portfolio · Presentation.pdf",
    "section": "Vehicle Document · AIF",
    "content": "AIF II strategy text…",
    "metadata": {"vehicleId": "abc-123", "vehicleName": "ASK SOP",
                 "vehicleType": "AIF", "fileType": "pdf"},
}
_DECK_BEDROCK = {
    "id": "bedrock:pub:0", "score": 0.62, "source": "bedrock",
    "sourceId": "pub", "title": "Public bedrock slide", "section": "intro",
    "content": "...", "metadata": {},
}


@pytest.mark.asyncio
async def test_documents_full_dropped_for_visitor(monkeypatch):
    monkeypatch.setattr(deck_search, "_post_deck",
                        _mock_post_factory([_DECK_DOCS_FULL, _DECK_BEDROCK]))
    db = _FakeDB()
    out = await deck_search.deck_search(
        "q", top_k=5, db=db,
        session_type="visitor", auth_state="anonymous",
    )
    sources = [h["source_raw"] for h in out]
    assert "documents_full" not in sources, f"documents_full leaked to visitor: {sources}"
    assert "bedrock" in sources, "bedrock hit must survive"
    # security_events row written
    kinds = [r.get("kind") for r in db.security_events.inserts]
    assert "kb_documents_full_blocked_for_role" in kinds
    blocked = [r for r in db.security_events.inserts
               if r.get("kind") == "kb_documents_full_blocked_for_role"][0]
    assert blocked["session_type"] == "visitor"
    assert "hit_title_redacted" in blocked
    assert blocked["hit_title_redacted"].startswith("ASK Special")
    # status counter ticked
    snap = deck_search.status()
    assert snap["documents_full_blocks_today"] == 1


@pytest.mark.asyncio
async def test_documents_full_dropped_for_client(monkeypatch):
    monkeypatch.setattr(deck_search, "_post_deck",
                        _mock_post_factory([_DECK_DOCS_FULL]))
    db = _FakeDB()
    out = await deck_search.deck_search(
        "q", top_k=5, db=db,
        session_type="client", auth_state="verified",
    )
    assert out == [], "documents_full must NOT reach a client (even a verified one)"
    snap = deck_search.status()
    assert snap["documents_full_blocks_today"] == 1
    assert any(r.get("kind") == "kb_documents_full_blocked_for_role"
               for r in db.security_events.inserts)


@pytest.mark.asyncio
async def test_documents_full_kept_for_verified_employee(monkeypatch):
    monkeypatch.setattr(deck_search, "_post_deck",
                        _mock_post_factory([_DECK_DOCS_FULL, _DECK_BEDROCK]))
    db = _FakeDB()
    out = await deck_search.deck_search(
        "q", top_k=5, db=db,
        session_type="employee", auth_state="verified",
    )
    sources = [h["source_raw"] for h in out]
    assert sources == ["documents_full", "bedrock"], (
        f"verified employee should see both sources unchanged, got {sources}")
    snap = deck_search.status()
    assert snap["documents_full_blocks_today"] == 0


# ================================================================
# Guard B — `is_full_document_scan` flag
# ================================================================
@pytest.mark.asyncio
async def test_is_full_document_scan_flag_set_for_documents_full(monkeypatch):
    """Surviving `documents_full` hits MUST carry `is_full_document_scan: True`
    for the FE chip differentiator."""
    monkeypatch.setattr(deck_search, "_post_deck",
                        _mock_post_factory([_DECK_DOCS_FULL, _DECK_BEDROCK]))
    db = _FakeDB()
    out = await deck_search.deck_search(
        "q", top_k=5, db=db,
        session_type="employee", auth_state="verified",
    )
    docs_full_hit = next(h for h in out if h["source_raw"] == "documents_full")
    bedrock_hit = next(h for h in out if h["source_raw"] == "bedrock")
    assert docs_full_hit["is_full_document_scan"] is True
    assert bedrock_hit["is_full_document_scan"] is False


@pytest.mark.asyncio
async def test_is_full_document_scan_propagates_to_citation(monkeypatch):
    """The flag set on the hit MUST flow through rag_agent._build_citations
    to the FE-visible citation envelope (employee session, flag stays True)."""
    from agents import rag_agent
    hits = [{
        "doc_id": "documents_full:abc:0",
        "doc_title": "ASK SOP presentation.pdf",
        "section": "intro", "text": "...",
        "source": "smifs_knowledge", "subsource": "documents_full",
        "score": 0.55, "raw_score": 0.55, "relevance": 0.55,
        "audience": "all", "source_engine": "deck_search",
        "is_full_document_scan": True,
    }, {
        "doc_id": "bedrock:pub:0", "doc_title": "Public", "section": "intro",
        "text": "...", "source": "smifs_knowledge", "subsource": "bedrock",
        "score": 0.50, "raw_score": 0.50, "relevance": 0.50,
        "audience": "all", "source_engine": "deck_search",
        "is_full_document_scan": False,
    }]
    cits = rag_agent._build_citations(hits)
    assert len(cits) == 2
    docs_full_cit = next(c for c in cits if "ASK SOP" in c["doc_title"])
    bedrock_cit = next(c for c in cits if c["doc_title"] == "Public")
    assert docs_full_cit["is_full_document_scan"] is True
    # The local-citation should NOT carry the flag (kept clean by the None-filter).
    assert "is_full_document_scan" not in bedrock_cit


# ================================================================
# Guard C — timeout budget bumped 2.5s → 3.0s
# ================================================================
@pytest.mark.asyncio
async def test_call_under_3s_budget_succeeds(monkeypatch):
    """A 2.8s mocked deck call MUST succeed against the new 3.0s budget."""
    monkeypatch.setattr(deck_search, "DECK_TIMEOUT_S", 3.0)

    async def _slow(*_a, **_kw):
        await asyncio.sleep(2.8)
        m = MagicMock()
        m.status_code = 200
        m.json = lambda: {"totalIndexed": 9, "results": [_DECK_BEDROCK]}
        return m
    monkeypatch.setattr(deck_search, "_post_deck", _slow)

    db = _FakeDB()
    out = await deck_search.deck_search(
        "q", top_k=3, db=db,
        session_type="employee", auth_state="verified",
    )
    assert len(out) == 1, "2.8s call MUST succeed under the new 3.0s budget"
    snap = deck_search.status()
    assert snap["timeouts_today"] == 0
    # 2.8s > 2.0s slow-response threshold → warning logged
    assert any(r.get("kind") == "deck_search_slow_response"
               for r in db.security_events.inserts)


@pytest.mark.asyncio
async def test_call_over_3s_budget_still_times_out(monkeypatch):
    """A 3.5s call MUST still time out — the 3.0s budget remains a hard cap."""
    monkeypatch.setattr(deck_search, "DECK_TIMEOUT_S", 3.0)

    async def _too_slow(*_a, **_kw):
        await asyncio.sleep(3.5)
        return MagicMock()
    monkeypatch.setattr(deck_search, "_post_deck", _too_slow)

    db = _FakeDB()
    out = await deck_search.deck_search(
        "q", top_k=3, db=db,
        session_type="employee", auth_state="verified",
    )
    assert out == []
    snap = deck_search.status()
    assert snap["timeouts_today"] == 1
    assert any(r.get("kind") == "deck_search_timeout"
               for r in db.security_events.inserts)


# ================================================================
# Phase 18.2 telemetry contract
# ================================================================
def test_status_includes_documents_full_counter():
    snap = deck_search.status()
    assert "documents_full_blocks_today" in snap
    assert snap["documents_full_blocks_today"] == 0
