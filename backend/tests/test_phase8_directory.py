"""Phase 8 — Directory tool-calling + PII masking + lifecycle field tests.

Covers:
- Verified employee directory_* tool dispatch for 6 natural-language prompts.
- Visitor / client sessions MUST NOT receive directory_* blocks (no leakage).
- PII scrub: conversations.messages[].content must NOT contain plaintext email
  or plaintext PAN after employee verification.
- GET /api/sessions/{sid} returns top-level `lifecycle` field.
- Cost ledger still records router task; no gpt-4o-mini model calls on directory turns.

Live OrgLens — slow. Timeouts tuned to 90s per turn.
"""
from __future__ import annotations
import os
import re
import uuid
import time

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

# Load frontend/.env to get public URL
try:
    from dotenv import dotenv_values
    _fe = dotenv_values("/app/frontend/.env")
    BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _fe.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
except Exception:
    BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL not configured"
# Load backend MONGO_URL for mongo-direct assertions
try:
    from dotenv import dotenv_values as _dv
    _be = _dv("/app/backend/.env")
    os.environ.setdefault("MONGO_URL", _be.get("MONGO_URL", ""))
    os.environ.setdefault("DB_NAME", _be.get("DB_NAME", "test_database"))
except Exception:
    pass
EMP_EMAIL = "aaditya.jaiswal@smifs.com"
EMP_PAN = "BQPPJ8323M"
TURN_TIMEOUT = 120


# ---- helpers ----
def _turn(sid: str, text: str, timeout: int = TURN_TIMEOUT):
    r = requests.post(
        f"{BASE_URL}/api/agent/turn",
        json={"session_id": sid, "message": text},
        timeout=timeout,
    )
    return r


def _blocks(resp_json):
    return resp_json.get("blocks") or []


def _block_types(resp_json):
    return [b.get("type") for b in _blocks(resp_json)]


def _trace_tools(resp_json):
    trace = resp_json.get("trace") or []
    # trace is a list of step dicts
    tools = []
    if isinstance(trace, list):
        for step in trace:
            if not isinstance(step, dict):
                continue
            if step.get("tool_name"):
                tools.append(step["tool_name"])
            elif step.get("tool"):
                tools.append(step["tool"])
    elif isinstance(trace, dict):
        for step in trace.get("steps") or []:
            if step.get("tool_name"):
                tools.append(step["tool_name"])
    return tools


def _verify_employee(sid: str):
    """Run the 2-step SMIFS employee verification flow."""
    r1 = _turn(sid, "I am an SMIFS employee, I want to verify my identity.")
    assert r1.status_code == 200, r1.text
    # expect an auth challenge
    r2 = _turn(sid, EMP_EMAIL)
    assert r2.status_code == 200, r2.text
    r3 = _turn(sid, EMP_PAN)
    assert r3.status_code == 200, r3.text
    return r3.json()


def _is_employee_verified(resp_json) -> bool:
    """Verification signal: employee_card block + intent VERIFIED or trace shows verified."""
    btypes = [b.get("type") for b in _blocks(resp_json)]
    if "employee_card" in btypes:
        return True
    trace = resp_json.get("trace") or []
    if isinstance(trace, list):
        for s in trace:
            if isinstance(s, dict) and s.get("to") == "verified":
                return True
    return False


# ---- fixtures ----
@pytest.fixture(scope="module")
def verified_emp_sid():
    sid = f"test-ph8-emp-{uuid.uuid4().hex[:8]}"
    out = _verify_employee(sid)
    assert _is_employee_verified(out), (
        f"verification did not complete; blocks={_block_types(out)}; intent={out.get('intent')}"
    )
    return sid


@pytest.fixture(scope="module")
def visitor_sid():
    return f"test-ph8-visitor-{uuid.uuid4().hex[:8]}"


# ============================================================
# 1. Verified employee directory tool tests
# ============================================================
class TestDirectoryEmployee:
    def test_org_stats_707(self, verified_emp_sid):
        r = _turn(verified_emp_sid, "How many employees does SMIFS have?")
        assert r.status_code == 200
        j = r.json()
        tools = _trace_tools(j)
        btypes = _block_types(j)
        assert "directory_org_stats" in tools, f"tools={tools} btypes={btypes}"
        assert "org_stats_card" in btypes, f"btypes={btypes}"
        card = next(b for b in _blocks(j) if b.get("type") == "org_stats_card")
        total = (card.get("data") or {}).get("total_employees")
        assert total and int(total) >= 500, f"unexpected total_employees={total}"

    def test_lookup_awanish(self, verified_emp_sid):
        r = _turn(verified_emp_sid, "Tell me about Awanish Chandra")
        assert r.status_code == 200
        j = r.json()
        assert "directory_lookup_employee" in _trace_tools(j)
        assert "directory_card" in _block_types(j)
        card = next(b for b in _blocks(j) if b.get("type") == "directory_card")
        data = card.get("data") or {}
        assert "Awanish" in (data.get("name") or ""), f"name={data.get('name')}"
        assert data.get("designation"), "designation should be populated from live OrgLens"

    def test_search_compliance(self, verified_emp_sid):
        r = _turn(verified_emp_sid, "Who is in the COMPLIANCE department?")
        assert r.status_code == 200
        j = r.json()
        assert "directory_search_employees" in _trace_tools(j)
        assert "directory_list" in _block_types(j)
        lst = next(b for b in _blocks(j) if b.get("type") == "directory_list")
        data = lst.get("data") or {}
        assert (data.get("total") or 0) > 0
        assert len(data.get("items") or []) > 0

    def test_my_team_zero_reports(self, verified_emp_sid):
        r = _turn(verified_emp_sid, "Who reports to me?")
        assert r.status_code == 200
        j = r.json()
        assert "directory_my_team" in _trace_tools(j)
        # Aaditya has 0 reports → should be an honest text reply, NO directory_list with fabricated entries
        btypes = _block_types(j)
        if "directory_list" in btypes:
            lst = next(b for b in _blocks(j) if b.get("type") == "directory_list")
            items = (lst.get("data") or {}).get("items") or []
            assert len(items) == 0, f"should be empty list; got {len(items)} items (possible fabrication)"

    def test_reporting_chain(self, verified_emp_sid):
        r = _turn(verified_emp_sid, "Who is my manager, and who does my manager report to?")
        assert r.status_code == 200
        j = r.json()
        assert "directory_my_reporting_chain" in _trace_tools(j)
        assert "reporting_chain_card" in _block_types(j)
        card = next(b for b in _blocks(j) if b.get("type") == "reporting_chain_card")
        chain = (card.get("data") or {}).get("chain") or []
        assert len(chain) >= 3, f"expected >=3 levels; got {len(chain)}: {[c.get('name') for c in chain]}"

    def test_departments_includes_wealth(self, verified_emp_sid):
        r = _turn(verified_emp_sid, "What departments exist at SMIFS?")
        assert r.status_code == 200
        j = r.json()
        assert "directory_departments" in _trace_tools(j)
        assert "directory_list" in _block_types(j)
        lst = next(b for b in _blocks(j) if b.get("type") == "directory_list")
        items = (lst.get("data") or {}).get("items") or []
        names = [(i.get("name") or "").upper() for i in items]
        assert any("WEALTH" in n for n in names), f"names={names[:15]}"


# ============================================================
# 2. Visitor must NOT receive directory blocks
# ============================================================
class TestVisitorNoDirectoryLeak:
    def test_visitor_no_directory_blocks(self, visitor_sid):
        r = _turn(visitor_sid, "Who is in the Compliance department?")
        assert r.status_code == 200
        j = r.json()
        btypes = _block_types(j)
        tools = _trace_tools(j)
        assert "directory_card" not in btypes
        assert "directory_list" not in btypes
        assert "org_stats_card" not in btypes
        assert "reporting_chain_card" not in btypes
        assert not any(t.startswith("directory_") for t in tools), f"leaked tools: {tools}"


# ============================================================
# 3. PII masking in persisted conversation history
# ============================================================
class TestPIIScrub:
    def test_history_masks_email_and_pan(self, verified_emp_sid):
        # Fetch session; then inspect persisted messages via admin backdoor or direct mongo
        import asyncio
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ.get("DB_NAME", "test_database")]

        async def _grep():
            msgs = await db.conversations.find_one({"session_id": verified_emp_sid})
            return msgs

        doc = asyncio.get_event_loop().run_until_complete(_grep())
        mongo.close()
        assert doc is not None, "conversations row missing"
        all_text = ""
        for m in doc.get("messages") or []:
            c = m.get("content") or m.get("text") or ""
            if isinstance(c, list):
                c = " ".join(str(x) for x in c)
            all_text += "\n" + str(c)

        # Must NOT contain plaintext email
        assert EMP_EMAIL not in all_text, "plaintext email leaked in conversations.messages"
        # Must contain masked form
        assert "aa***@smifs.com" in all_text or re.search(r"aa\*+@smifs\.com", all_text), (
            "masked email form not found — scrub may not be running"
        )
        # Must NOT contain plaintext PAN
        assert EMP_PAN not in all_text, "plaintext PAN leaked"
        # No 10-digit phone sequences (heuristic)
        assert not re.search(r"\b[6-9]\d{9}\b", all_text), "raw 10-digit phone leaked"


# ============================================================
# 4. GET /api/sessions/{sid} exposes lifecycle field
# ============================================================
class TestLifecycleField:
    def test_lifecycle_in_sessions_response(self, verified_emp_sid):
        r = requests.get(f"{BASE_URL}/api/sessions/{verified_emp_sid}", timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert "lifecycle" in j, f"lifecycle missing; keys={list(j.keys())}"
        assert j["lifecycle"] in ("active", "expired", "ended", "locked"), j["lifecycle"]


# ============================================================
# 5. Regression: health + turn still respond
# ============================================================
class TestRegression:
    def test_health(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=15)
        assert r.status_code == 200
        j = r.json()
        assert j.get("status") == "ok"
        assert j.get("orglens_reachable") is True

    def test_signout_endpoint_exists(self, verified_emp_sid):
        # Create a fresh session, sign it out
        sid = f"test-ph8-signout-{uuid.uuid4().hex[:8]}"
        _turn(sid, "hello")
        r = requests.post(f"{BASE_URL}/api/sessions/{sid}/signout", timeout=30)
        assert r.status_code in (200, 204)

    def test_router_in_cost_ledger_not_gpt4o_mini(self, verified_emp_sid):
        import asyncio
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ.get("DB_NAME", "test_database")]

        async def _fetch():
            # session-scoped first
            rows = await db.llm_calls.find(
                {"session_id": verified_emp_sid}
            ).to_list(length=500)
            if not rows:
                # fallback: last 50 rows globally (fire-and-forget can lag)
                rows = await db.llm_calls.find({}).sort("created_at_dt", -1).to_list(length=50)
            return rows

        rows = asyncio.get_event_loop().run_until_complete(_fetch())
        mongo.close()
        if not rows:
            pytest.skip("cost_ledger not populated (may be named differently)")
        tasks = {r.get("task") for r in rows}
        models = set()
        for r in rows:
            models.add(r.get("model_requested") or "")
            models.add(r.get("model_resolved") or "")
        assert "router" in tasks, f"router task missing from ledger; tasks={tasks}"
        assert not any("gpt-4o-mini" in (m or "") for m in models), f"gpt-4o-mini unexpectedly present; models={models}"
