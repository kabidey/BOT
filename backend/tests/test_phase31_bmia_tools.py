"""Phase 31 — BMIA tools pipeline backend acceptance tests.

Covers the 5 new BMIA endpoints (fund/decisions, fund/portfolio/{name},
litmus/positions, litmus/cycles, litmus/summary), the umbrella router
intent `BMIA_TOOLS_PIPELINE`, the augmentation injector, the suggestion
catalog, and basic backend health/openapi reachability.

Per the review request, we call bmia_client.execute() and router.classify()
DIRECTLY (no second-round Hub AI synthesis) — Hub AI on the preview pod
is too slow for the post-tool synthesis call.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys

import pytest
import requests

# Ensure /app/backend is importable when pytest is invoked from /app
ROOT = "/app/backend"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fall back to /app/frontend/.env so the test works in CI w/o env injection.
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Module-level fixtures (event loop reuse across async tests)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def bmia_client():
    """Import bmia_client lazily so dotenv is already loaded by server import."""
    # Make sure backend .env is loaded (BMIA_API_KEY)
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path("/app/backend/.env"))
    mod = importlib.import_module("agents.bmia_client")
    importlib.reload(mod)  # pick up env if previously imported without key
    return mod


@pytest.fixture(scope="module")
def router_mod():
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path("/app/backend/.env"))
    return importlib.import_module("agents.router")


@pytest.fixture(scope="module")
def orglens_orch():
    return importlib.import_module("orglens_tools.orchestrator")


@pytest.fixture(scope="module")
def suggestion_mod():
    return importlib.import_module("agents.suggestion_agent")


# =====================================================================
# 1) Direct BMIA tool execution  (5 tools)
# =====================================================================
class TestBmiaDirectExecution:
    def test_fund_decisions(self, bmia_client):
        res = asyncio.run(bmia_client.execute("bmia_fund_decisions", {"limit": 3}))
        assert res.get("ok") is True, f"fund_decisions failed: {res}"
        val = res.get("value") or {}
        assert "decisions" in val, f"missing 'decisions' key: {val}"
        assert isinstance(val["decisions"], list)
        assert len(val["decisions"]) <= 3
        # If non-empty, ensure rows look like decisions
        if val["decisions"]:
            row = val["decisions"][0]
            assert isinstance(row, dict)

    def test_fund_portfolio_graceful_404(self, bmia_client):
        """Per spec: unprovisioned books should return ok:True + value.available:false
        (NOT ok:False)."""
        res = asyncio.run(bmia_client.execute("bmia_fund_portfolio", {"name": "long_term"}))
        assert res.get("ok") is True, f"fund_portfolio MUST degrade to ok:True for unprovisioned books, got: {res}"
        val = res.get("value") or {}
        assert val.get("name") == "long_term"
        # Either provisioned (available:True + content) or graceful degrade
        # (available:False + reason). Both are acceptable per the wrapper spec.
        assert "available" in val, f"value must carry 'available' key: {val}"
        if val.get("available") is False:
            assert "reason" in val

    def test_litmus_positions(self, bmia_client):
        res = asyncio.run(bmia_client.execute("bmia_litmus_positions", {"limit": 5}))
        assert res.get("ok") is True, f"litmus_positions failed: {res}"
        val = res.get("value") or {}
        assert "positions" in val
        assert isinstance(val["positions"], list)

    def test_litmus_cycles(self, bmia_client):
        res = asyncio.run(bmia_client.execute("bmia_litmus_cycles", {"limit": 5}))
        assert res.get("ok") is True, f"litmus_cycles failed: {res}"
        val = res.get("value") or {}
        assert "cycles" in val
        assert isinstance(val["cycles"], list)
        assert len(val["cycles"]) <= 5

    def test_litmus_summary(self, bmia_client):
        res = asyncio.run(bmia_client.execute("bmia_litmus_summary", {}))
        assert res.get("ok") is True, f"litmus_summary failed: {res}"
        val = res.get("value") or {}
        # Per request, keys: open_positions, closed_cycles, total_pnl, win_rate
        expected = ["open_positions", "closed_cycles", "total_pnl", "win_rate"]
        missing = [k for k in expected if k not in val]
        # Soft check — record missing keys; don't fail outright since BMIA may
        # change shape. But the test_report should flag any miss.
        assert not missing, f"litmus_summary missing keys: {missing}; got: {list(val.keys())}"


# =====================================================================
# 2) Router classification → BMIA_TOOLS_PIPELINE
# =====================================================================
@pytest.mark.parametrize("query", [
    "Show me recent BMIA consensus calls",
    "long-term portfolio book",
    "litmus paper trading win rate",
])
def test_router_classifies_to_pipeline(router_mod, query):
    """All three umbrella queries must classify to BMIA_TOOLS_PIPELINE
    with tool_name=bmia_research_pipeline."""
    result = asyncio.run(router_mod.classify(query, history=[], session_context=None))
    assert result is not None
    intent = result.get("intent")
    tool_name = result.get("tool_name")
    assert intent == "BMIA_TOOLS_PIPELINE", (
        f"query={query!r} routed to intent={intent} (tool={tool_name}), "
        f"expected BMIA_TOOLS_PIPELINE. Full result: {result}"
    )
    assert tool_name == "bmia_research_pipeline", (
        f"expected tool_name=bmia_research_pipeline, got {tool_name}"
    )


# =====================================================================
# 3) Augmentation function — card auto-injection
# =====================================================================
def test_augment_with_bmia_cards_injects_all_five(orglens_orch):
    tool_payloads = [
        {"ok": True, "tool": "bmia_fund_decisions",
         "value": {"count": 1, "decisions": [{"symbol": "TCS", "decision": "BUY"}]}},
        {"ok": True, "tool": "bmia_fund_portfolio",
         "value": {"name": "long_term", "available": False, "reason": "not_provisioned"}},
        {"ok": True, "tool": "bmia_litmus_positions",
         "value": {"count": 0, "shown": 0, "only_open": True, "positions": []}},
        {"ok": True, "tool": "bmia_litmus_cycles",
         "value": {"count": 0, "cycles": []}},
        {"ok": True, "tool": "bmia_litmus_summary",
         "value": {"open_positions": 1, "closed_cycles": 2, "total_pnl": 0, "win_rate": 0.5}},
    ]
    blocks = orglens_orch._augment_with_bmia_cards([], tool_payloads)
    types = [b.get("type") for b in blocks]
    expected = {
        "bmia_fund_decisions_card",
        "bmia_fund_portfolio_card",
        "bmia_litmus_positions_card",
        "bmia_litmus_cycles_card",
        "bmia_litmus_summary_card",
    }
    missing = expected - set(types)
    assert not missing, f"augment missed cards: {missing}; got: {types}"


def test_augment_idempotent_against_existing_card(orglens_orch):
    """If a card type already exists in blocks, augment must not duplicate."""
    prior_blocks = [{"type": "bmia_litmus_summary_card", "data": {"win_rate": 0.7}}]
    payloads = [{"ok": True, "tool": "bmia_litmus_summary",
                 "value": {"open_positions": 0, "closed_cycles": 0,
                           "total_pnl": 0, "win_rate": 0.1}}]
    out = orglens_orch._augment_with_bmia_cards(prior_blocks, payloads)
    types = [b.get("type") for b in out]
    assert types.count("bmia_litmus_summary_card") == 1


def test_augment_ignores_failed_payload(orglens_orch):
    payloads = [{"ok": False, "tool": "bmia_fund_decisions", "error": "boom"}]
    out = orglens_orch._augment_with_bmia_cards([], payloads)
    assert out == []


# =====================================================================
# 4) Suggestion catalog — Phase 31 chips
# =====================================================================
def test_fallback_chips_consensus_visitor(suggestion_mod):
    chips = suggestion_mod._fallback_chips("show recent consensus calls", "visitor")
    assert isinstance(chips, list) and len(chips) == 3
    joined = " ".join(c.lower() for c in chips)
    # Must reference BUY calls / portfolio / research desk
    assert any(k in joined for k in ("buy call", "portfolio", "research desk")), (
        f"visitor chips for consensus calls look generic: {chips}"
    )


def test_fallback_chips_litmus_employee(suggestion_mod):
    chips = suggestion_mod._fallback_chips("paper trading win rate", "employee")
    assert isinstance(chips, list) and len(chips) == 3
    joined = " ".join(c.lower() for c in chips)
    assert "litmus" in joined or "paper" in joined, (
        f"employee chips for paper-trading should reference Litmus, got: {chips}"
    )


# =====================================================================
# 5) Backend health + OpenAPI
# =====================================================================
def test_health_endpoint_ok():
    assert BASE_URL, "REACT_APP_BACKEND_URL not configured"
    r = requests.get(f"{BASE_URL}/api/health", timeout=30)
    assert r.status_code == 200, f"/api/health returned {r.status_code}: {r.text[:200]}"
    data = r.json()
    # Health envelope must surface model status of some kind
    assert isinstance(data, dict)
    assert any(k in data for k in ("status", "models", "llm_reachable", "ok"))


def test_openapi_lists_routes():
    assert BASE_URL
    r = requests.get(f"{BASE_URL}/api/openapi.json", timeout=30)
    if r.status_code != 200:
        # Fallback path
        r2 = requests.get(f"{BASE_URL}/openapi.json", timeout=30)
        assert r2.status_code == 200, (
            f"openapi neither at /api/openapi.json ({r.status_code}) nor /openapi.json ({r2.status_code})"
        )
        data = r2.json()
    else:
        data = r.json()
    paths = data.get("paths") or {}
    assert isinstance(paths, dict) and len(paths) > 0, "openapi has no paths"
    # Spot check the health route shows up
    assert any("/health" in p for p in paths), f"no /health route in openapi paths"
