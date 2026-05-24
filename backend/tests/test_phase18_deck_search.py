"""Phase 18 — Workstream A acceptance tests for the Deck Vector Engine fallback.

Two batches:
  * Flag-gating / short-circuit — when `DECK_SEARCH_FALLBACK != "true"` the
    function MUST return `[]` without making any HTTP call. This is the
    safety guarantee that prevents the new integration from affecting
    production until ops flips the env var.
  * Audience drop — visitor / client sessions MUST never see deck hits whose
    `source` is in the employee-only subsource set (sales_pitch,
    growth_insurance, growth_revenue). The drop count is logged.

Tests use `respx` to assert HTTP behaviour without hitting deck.pesmifs.com.
"""
from __future__ import annotations
import asyncio
import os
import sys
from unittest.mock import MagicMock

import httpx
import pytest
import respx

sys.path.insert(0, "/app/backend")

from agents import deck_search  # noqa: E402


# ---------------------------------------------------------------- helpers
def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if asyncio.get_event_loop().is_running() is False else asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset deck_search singletons between tests so suspension state etc.
    doesn't leak across cases."""
    deck_search._reset_state()
    yield
    deck_search._reset_state()


@pytest.fixture
def _stub_env(monkeypatch):
    monkeypatch.setenv("SMIFS_KNOWLEDGE_BASE_URL", "https://deck.example.test")
    monkeypatch.setenv("SMIFS_KNOWLEDGE_API_KEY", "test-key")
    # Reload the module-level constants since they were captured at import time.
    deck_search.DECK_BASE = "https://deck.example.test"
    deck_search.DECK_KEY = "test-key"


# ================================================================
# Batch A — flag gating / short-circuit
# ================================================================
@pytest.mark.asyncio
@respx.mock
async def test_default_off_short_circuits_without_http(monkeypatch, _stub_env):
    """With DECK_SEARCH_FALLBACK unset/false, deck_search() returns [] and
    makes ZERO HTTP requests."""
    monkeypatch.delenv("DECK_SEARCH_FALLBACK", raising=False)
    route = respx.post("https://deck.example.test/api/knowledge/search").mock(
        return_value=httpx.Response(200, json={"totalIndexed": 1, "results": []})
    )
    out = await deck_search.deck_search("any query", top_k=4)
    assert out == []
    assert route.called is False, "deck_search MUST NOT call the network when flag is off"


@pytest.mark.asyncio
@respx.mock
async def test_explicit_false_short_circuits(monkeypatch, _stub_env):
    monkeypatch.setenv("DECK_SEARCH_FALLBACK", "false")
    route = respx.post("https://deck.example.test/api/knowledge/search").mock(
        return_value=httpx.Response(200, json={"totalIndexed": 1, "results": []})
    )
    assert await deck_search.deck_search("q", top_k=4) == []
    assert route.called is False


@pytest.mark.asyncio
@respx.mock
async def test_flag_on_makes_http_call_and_maps_hits(monkeypatch, _stub_env):
    monkeypatch.setenv("DECK_SEARCH_FALLBACK", "true")
    route = respx.post("https://deck.example.test/api/knowledge/search").mock(
        return_value=httpx.Response(200, json={
            "totalIndexed": 5,
            "results": [
                {"id": "deck-1", "title": "Mackertich ONE AIF brochure",
                 "section": "intro", "content": "Some intro text",
                 "source": "smifs_knowledge", "score": 0.42},
                {"id": "deck-2", "title": "Sales pitch deck",
                 "section": "pitch", "content": "internal pitch",
                 "source": "sales_pitch", "score": 0.55},
            ],
        })
    )
    # Verified employee → all hits pass the audience gate.
    out = await deck_search.deck_search(
        "what is an aif?", top_k=4,
        session_type="employee", auth_state="verified",
    )
    assert route.called is True
    assert len(out) == 2
    # Mapped fields the rag_agent depends on.
    assert {h["source"] for h in out} == {"smifs_knowledge"}
    assert all(h.get("source_engine") == "deck_search" for h in out)
    assert all("doc_id" in h and "doc_title" in h and "text" in h for h in out)


# ================================================================
# Batch B — strict audience drop for non-employees
# ================================================================
def test_apply_audience_drop_keeps_all_for_verified_employee():
    results = [
        {"source": "smifs_knowledge", "id": "a"},
        {"source": "sales_pitch", "id": "b"},
        {"source": "growth_revenue", "id": "c"},
    ]
    kept, dropped = deck_search.apply_audience_drop(results, "employee", "verified")
    assert len(kept) == 3 and dropped == 0


def test_apply_audience_drop_drops_employee_only_for_visitor():
    results = [
        {"source": "smifs_knowledge", "id": "a"},
        {"source": "sales_pitch", "id": "b"},
        {"source": "growth_insurance", "id": "c"},
        {"source": "growth_revenue", "id": "d"},
    ]
    kept, dropped = deck_search.apply_audience_drop(results, "visitor", "anonymous")
    assert dropped == 3
    assert [h["id"] for h in kept] == ["a"]


def test_apply_audience_drop_drops_for_unverified_client_too():
    """A client who started auth but hasn't completed it must NOT see deck-
    hosted internal sales material."""
    results = [
        {"source": "smifs_knowledge", "id": "a"},
        {"source": "sales_pitch", "id": "b"},
    ]
    kept, dropped = deck_search.apply_audience_drop(results, "client", "awaiting_pan")
    assert dropped == 1
    assert [h["id"] for h in kept] == ["a"]


@pytest.mark.asyncio
@respx.mock
async def test_visitor_session_drops_employee_only_deck_hits(monkeypatch, _stub_env):
    """Full end-to-end with the HTTP layer mocked. A visitor MUST never see
    a `sales_pitch` deck hit even if the deck returns one."""
    monkeypatch.setenv("DECK_SEARCH_FALLBACK", "true")
    respx.post("https://deck.example.test/api/knowledge/search").mock(
        return_value=httpx.Response(200, json={
            "totalIndexed": 9,
            "results": [
                {"id": "x", "title": "AIF intro", "content": "...",
                 "source": "smifs_knowledge", "score": 0.5},
                {"id": "y", "title": "Internal pitch", "content": "...",
                 "source": "sales_pitch", "score": 0.6},
            ],
        })
    )
    out = await deck_search.deck_search(
        "what is an AIF?", top_k=4,
        session_type="visitor", auth_state="anonymous",
    )
    sources = {h["source"] for h in out}
    assert sources == {"smifs_knowledge"}
    assert all(h.get("source_engine") == "deck_search" for h in out)


# ================================================================
# Batch C — status snapshot shape
# ================================================================
def test_status_snapshot_shape():
    snap = deck_search.status()
    for k in ("enabled", "suspended", "last_10_calls", "total_calls_today",
              "audience_drops_today", "backoff_seconds", "min_score"):
        assert k in snap, f"deck_search.status() missing key: {k}"
