"""Phase 8.1 — Comprehensive Employee Q&A regression tests.

Covers:
- USER_PROFILE JSON block emitted by identity.employee_context_block() contains the
  expected keys (employee_id, hrbp, manager, CTC, notice, confirmation, etc.).
- identity._RAW_STRIP_FIELDS narrowed: email/mobile_number stay, PAN/Aadhaar/bank stripped.
- Privacy regression: sessions.identity.raw contains email/mobile but NOT PAN/bank.
- directory module has 15 tools incl. 6 new helpers; DIRECTORY_TOOL_NAMES exposed.
- Orchestrator bug-fix: a VERIFIED employee sending "What's my employee ID?" does NOT
  reset auth_state to AWAIT_IDENT (role-trigger guarded behind state==ANON).
- Self-queries are routed to KNOWLEDGE / SMALL_TALK (never directory_*).
- About-others queries are routed to the right directory_* tool.
- Cross-role guard: visitor asking "how many people on notice?" gets no directory blocks.
- /api/sessions/{sid} still returns top-level `lifecycle`.
"""
from __future__ import annotations
import os
import uuid
import asyncio
import json

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

# ---- env loading ----
try:
    from dotenv import dotenv_values
    _fe = dotenv_values("/app/frontend/.env")
    BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _fe.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
    _be = dotenv_values("/app/backend/.env")
    os.environ.setdefault("MONGO_URL", _be.get("MONGO_URL", ""))
    os.environ.setdefault("DB_NAME", _be.get("DB_NAME", "test_database"))
except Exception:
    BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL not configured"

EMP_EMAIL = "aaditya.jaiswal@smifs.com"
EMP_PAN = "BQPPJ8323M"
TURN_TIMEOUT = 150


# ---- helpers ----
def _turn(sid: str, text: str, timeout: int = TURN_TIMEOUT):
    return requests.post(
        f"{BASE_URL}/api/agent/turn",
        json={"session_id": sid, "message": text},
        timeout=timeout,
    )


def _blocks(j): return j.get("blocks") or []
def _btypes(j): return [b.get("type") for b in _blocks(j)]


def _trace_tools(j):
    out = []
    for s in (j.get("trace") or []):
        if isinstance(s, dict):
            t = s.get("tool_name") or s.get("tool")
            if t:
                out.append(t)
    return out


def _trace_intent(j):
    for s in (j.get("trace") or []):
        if isinstance(s, dict) and s.get("step") == "router":
            return s.get("intent")
    return None


def _verify_employee(sid: str):
    r1 = _turn(sid, "I am an SMIFS employee, I want to verify my identity.")
    assert r1.status_code == 200, r1.text
    r2 = _turn(sid, EMP_EMAIL)
    assert r2.status_code == 200, r2.text
    r3 = _turn(sid, EMP_PAN)
    assert r3.status_code == 200, r3.text
    return r3.json()


# ---- fixtures ----
@pytest.fixture(scope="module")
def verified_sid():
    sid = f"test-ph81-emp-{uuid.uuid4().hex[:8]}"
    out = _verify_employee(sid)
    # verification signal
    assert "employee_card" in _btypes(out) or any(
        isinstance(s, dict) and s.get("to") == "verified" for s in (out.get("trace") or [])
    ), f"not verified; blocks={_btypes(out)}"
    return sid


@pytest.fixture(scope="module")
def visitor_sid():
    return f"test-ph81-visitor-{uuid.uuid4().hex[:8]}"


# ============================================================
# 1. Unit tests on identity module (no network)
# ============================================================
class TestIdentityModule:
    def test_raw_strip_fields_narrowed(self):
        import identity as id_mod
        strip = id_mod._RAW_STRIP_FIELDS
        # SHOULD strip
        for f in ("pan", "pan_number", "aadhar_no", "bank_details", "account", "bank_account"):
            assert f in strip, f"'{f}' should be in _RAW_STRIP_FIELDS but isn't"
        # SHOULD NOT strip (kept in raw for USER_PROFILE)
        for f in ("email", "mobile_number", "phone", "hrbp_email", "date_of_birth",
                  "reports_to_email", "date_of_joining", "fixed_ctc", "total_ctc"):
            assert f not in strip, f"'{f}' must NOT be in _RAW_STRIP_FIELDS (needed in USER_PROFILE)"

    def test_strip_sensitive_for_raw(self):
        import identity as id_mod
        rec = {
            "email": "x@smifs.com", "mobile_number": "9876543210",
            "pan_number": "ABCDE1234F", "pan": "ABCDE1234F",
            "aadhar_no": "123456789012", "bank_details": {"acc": "1234"},
            "account": "ACC-1", "hrbp_email": "hrbp@smifs.com",
            "fixed_ctc": 1000000, "total_ctc": 1200000, "employee_id": "E001",
        }
        cleaned = id_mod._strip_sensitive_for_raw(rec)
        assert "pan_number" not in cleaned
        assert "pan" not in cleaned
        assert "aadhar_no" not in cleaned
        assert "bank_details" not in cleaned
        assert "account" not in cleaned
        assert cleaned.get("email") == "x@smifs.com"
        assert cleaned.get("mobile_number") == "9876543210"
        assert cleaned.get("hrbp_email") == "hrbp@smifs.com"
        assert cleaned.get("fixed_ctc") == 1000000
        assert cleaned.get("employee_id") == "E001"

    def test_employee_context_block_emits_user_profile_json(self):
        import identity as id_mod
        identity_obj = {
            "type": "employee",
            "employee_id": "E123", "first_name": "Test",
            "designation": "Engineer", "department": "TECH",
            "business_unit": "BU1", "reports_to_name": "Manager Name",
            "hrbp_name": "HRBP Name", "date_of_joining": "2020-01-01",
            "confirmation_status": "Confirmed", "on_notice": False,
            "raw": {
                "email": "test@smifs.com", "mobile_number": "9999999999",
                "fixed_ctc": 1000000, "total_ctc": 1200000,
                "current_experience": "5 years 3 months",
                "hrbp_email": "hrbp@smifs.com",
            },
        }
        block = id_mod.employee_context_block(identity_obj)
        assert "USER_PROFILE =" in block
        # extract JSON
        start = block.index("USER_PROFILE =") + len("USER_PROFILE =")
        end = block.index("\n--- END USER_PROFILE")
        payload = json.loads(block[start:end].strip())
        # required keys
        for k in ("employee_id", "designation", "department", "business_unit",
                  "reports_to_name", "hrbp_name", "date_of_joining",
                  "confirmation_status", "on_notice", "email", "mobile_number",
                  "fixed_ctc", "total_ctc"):
            assert k in payload, f"USER_PROFILE missing '{k}'"
        # derived tenure
        assert "current_experience_years" in payload
        assert payload["current_experience_years"] == pytest.approx(5.25, abs=0.02)
        # instructions present
        assert "USER_PROFILE" in block and "directly" in block.lower()

    def test_directory_tools_count_and_names(self):
        from agents.directory_agent import DIRECTORY_TOOLS, DIRECTORY_TOOL_NAMES
        assert len(DIRECTORY_TOOLS) == 15, f"expected 15 tools, got {len(DIRECTORY_TOOLS)}"
        for t in ("directory_filter_by_status", "directory_recent_joins",
                  "directory_upcoming_confirmations", "directory_by_tenure",
                  "directory_aggregate", "directory_field_value"):
            assert t in DIRECTORY_TOOL_NAMES, f"new tool '{t}' missing"

    def test_directory_search_employees_new_params(self):
        """Confirm widened search_employees supports new filter params.

        Checks both the tool schema (what the LLM router sees) AND the
        underlying function signature (what client-side filtering supports).
        """
        import inspect
        from agents.directory_agent import DIRECTORY_TOOLS
        import directory as _dir

        search_tool = next(t for t in DIRECTORY_TOOLS
                           if t["function"]["name"] == "directory_search_employees")
        schema_props = set(search_tool["function"]["parameters"]["properties"].keys())
        func_params = set(inspect.signature(_dir.search_employees).parameters.keys())

        # Must be exposed to LLM via schema
        schema_must_have = {
            "employee_type", "confirmation_status", "business_unit", "company",
            "gender", "on_notice", "is_absconding", "reports_to_name", "hrbp_name",
        }
        missing_schema = schema_must_have - schema_props
        assert not missing_schema, f"tool schema missing: {missing_schema}"

        # Must be supported by client-side filter (function signature)
        func_must_have = schema_must_have | {"reports_to_email", "reports_to_user_id"}
        missing_func = func_must_have - func_params
        assert not missing_func, f"search_employees() function missing params: {missing_func}"


# ============================================================
# 2. Self-queries must route to KNOWLEDGE / SMALL_TALK, NOT directory_*
# ============================================================
SELF_QUERIES = [
    "What is my employee ID?",
    "Who is my HRBP?",
    "Who is my manager?",
    "When did I join SMIFS?",
    "Am I on notice?",
]


@pytest.mark.parametrize("q", SELF_QUERIES)
class TestSelfQueriesNoDirectory:
    def test_self_query_no_directory_tool(self, verified_sid, q):
        r = _turn(verified_sid, q)
        assert r.status_code == 200, r.text
        j = r.json()
        tools = _trace_tools(j)
        btypes = _btypes(j)
        intent = _trace_intent(j)
        # must NOT fire any directory_* tool
        assert not any(t.startswith("directory_") for t in tools), (
            f"self-query '{q}' fired directory tool(s) {tools}; intent={intent}"
        )
        # must NOT render directory blocks
        for bt in ("directory_card", "directory_list", "org_stats_card", "reporting_chain_card"):
            assert bt not in btypes, f"self-query '{q}' rendered block '{bt}'"
        # intent expected to be KNOWLEDGE or SMALL_TALK
        assert intent in ("KNOWLEDGE", "SMALL_TALK", None), (
            f"self-query '{q}' got unexpected intent={intent}"
        )


# ============================================================
# 3. About-others must route to the right directory_* tool
# ============================================================
ABOUT_OTHERS = [
    ("Tell me about Awanish Chandra",          "directory_lookup_employee"),
    ("How many employees does SMIFS have?",    "directory_org_stats"),
    ("Who is in the COMPLIANCE department?",   "directory_search_employees"),
    ("What departments exist at SMIFS?",       "directory_departments"),
    ("Who is my manager, and who does my manager report to?", "directory_my_reporting_chain"),
]


@pytest.mark.parametrize("q,expected_tool", ABOUT_OTHERS)
class TestAboutOthersDirectory:
    def test_about_others_fires_correct_tool(self, verified_sid, q, expected_tool):
        r = _turn(verified_sid, q)
        assert r.status_code == 200, r.text
        j = r.json()
        tools = _trace_tools(j)
        assert expected_tool in tools, (
            f"'{q}' should fire {expected_tool}; got tools={tools}; "
            f"intent={_trace_intent(j)}; btypes={_btypes(j)}"
        )


# ============================================================
# 4. Orchestrator bug-fix — verified user saying "employee" keeps auth_state
# ============================================================
class TestOrchestratorRoleTriggerGuard:
    def test_verified_user_employee_word_does_not_reset_auth(self, verified_sid):
        """Before Phase 8.1 fix: the word 'employee' in a verified user's msg
        re-triggered role detection → reset state to AWAIT_IDENT. After fix:
        state must remain VERIFIED."""
        # confirm currently verified
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ.get("DB_NAME", "test_database")]

        async def _get_state():
            row = await db.sessions.find_one({"_id": verified_sid}, {"_id": 0})
            return (row or {}).get("auth_state")

        before = asyncio.get_event_loop().run_until_complete(_get_state())
        assert before == "verified", f"expected verified before; got {before}"

        # Send a message containing the word 'employee' — self-query
        r = _turn(verified_sid, "What is my employee ID and employee type?")
        assert r.status_code == 200, r.text

        after = asyncio.get_event_loop().run_until_complete(_get_state())
        mongo.close()
        assert after == "verified", (
            f"auth_state regressed from verified→{after} after message "
            f"containing 'employee' — role-trigger guard missing"
        )


# ============================================================
# 5. Privacy regression — sessions.identity.raw
# ============================================================
class TestPrivacyRawSnapshot:
    def test_identity_raw_keeps_email_mobile_but_not_pan_bank(self, verified_sid):
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ.get("DB_NAME", "test_database")]

        async def _get_identity():
            row = await db.sessions.find_one({"_id": verified_sid}, {"_id": 0})
            return (row or {}).get("identity") or {}

        ident = asyncio.get_event_loop().run_until_complete(_get_identity())
        mongo.close()
        raw = ident.get("raw") or {}
        # Should have
        assert raw.get("email"), f"raw.email missing; raw keys={list(raw.keys())[:20]}"
        # mobile_number is the canonical OrgLens field; allow 'phone' as fallback
        assert raw.get("mobile_number") or raw.get("phone"), "raw.mobile_number missing"
        # Must NOT have
        for bad in ("pan", "pan_number", "aadhar_no", "aadhaar", "bank_details",
                    "bank_account", "account"):
            assert bad not in raw, f"privacy leak: raw.{bad} present"


# ============================================================
# 6. Cross-role guard — visitor gets no directory blocks
# ============================================================
class TestVisitorCrossRoleGuard:
    def test_visitor_on_notice_query_blocked(self, visitor_sid):
        r = _turn(visitor_sid, "How many people are on notice at SMIFS?")
        assert r.status_code == 200
        j = r.json()
        btypes = _btypes(j)
        tools = _trace_tools(j)
        assert not any(t.startswith("directory_") for t in tools), (
            f"visitor leaked directory tool(s): {tools}"
        )
        for bt in ("directory_card", "directory_list", "org_stats_card", "reporting_chain_card"):
            assert bt not in btypes


# ============================================================
# 7. /api/sessions/{sid} lifecycle field
# ============================================================
class TestLifecycleFieldRegression:
    def test_lifecycle_present(self, verified_sid):
        r = requests.get(f"{BASE_URL}/api/sessions/{verified_sid}", timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert "lifecycle" in j
        assert j["lifecycle"] in ("active", "expired", "ended", "locked")
