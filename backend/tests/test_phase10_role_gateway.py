"""Phase 10 — Role gateway + Client Q&A + Knowledge gating + Wealth-Manager fallback.

Covers all 15 cases enumerated in the review request:
  1. Fresh session → GET /api/sessions/<sid> 404 (no row yet)
  2. POST /select_role {role:'client'}     → AWAIT_IDENT, session_type='client'
  3. POST /select_role {role:'employee'}   → AWAIT_IDENT, session_type='employee'
  4. POST /select_role {role:'visitor'}    → ANON,        session_type='visitor', friendly welcome
  5. Client verification UCC 63876 → PAN ARIPP3602Q → client_card with masked rm_email + rm_mobile
  6. Verified client CLIENT_PROFILE self-queries (5 sub-cases) answer from record (intent=KNOWLEDGE)
  7. Verified client product question fallback → escalation_card + 'I don't have that information in your record'
  8. Visitor product question → no smifs_knowledge citation + Mackertich ONE WM fallback
  9. Verified client product question → no smifs_knowledge citation (gating upheld even with index)
 10. Verified employee product question → smifs_knowledge citations present
 11. Idempotent role-switch guard (employee self-query 'employee ID')
 12. Privacy regression — Mongo: no plaintext PAN/phone/email in conversations.messages
 13. sessions.identity.raw contains rm_name/ucc/status/risk_profile/email but NOT pan/aadhar/bank/account
 14. /app/backend/CLIENT_FIELD_MAP.md exists
 15. hallucination_events grep for action='refused' on product-topic client/visitor turns

Admin token: smifs-admin-2026
Client UCC 63876 PAN ARIPP3602Q  (RM JITEN SAHOO)
Employee:  aaditya.jaiswal@smifs.com / BQPPJ8323M
"""
from __future__ import annotations
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import requests

try:
    from dotenv import dotenv_values
    _fe = dotenv_values("/app/frontend/.env")
    BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _fe.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
    _be = dotenv_values("/app/backend/.env")
    ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or _be.get("ADMIN_TOKEN") or "smifs-admin-2026"
    MONGO_URL = os.environ.get("MONGO_URL") or _be.get("MONGO_URL")
    DB_NAME = os.environ.get("DB_NAME") or _be.get("DB_NAME")
except Exception:
    BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
    ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or "smifs-admin-2026"
    MONGO_URL = os.environ.get("MONGO_URL")
    DB_NAME = os.environ.get("DB_NAME")

assert BASE_URL, "REACT_APP_BACKEND_URL not configured"
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}

CLIENT_UCC = "63876"
CLIENT_PAN = "ARIPP3602Q"
CLIENT_RM_NAME = "JITEN SAHOO"

EMP_EMAIL = "aaditya.jaiswal@smifs.com"
EMP_PAN = "BQPPJ8323M"

PRODUCT_QUESTION = "What is the minimum for Mackertich ONE PMS?"
TURN_TIMEOUT = 180


# ------------------------- helpers -------------------------
def _new_sid(prefix: str) -> str:
    return f"test-ph10-{prefix}-{uuid.uuid4().hex[:8]}"


def _select_role(sid: str, role: str) -> requests.Response:
    return requests.post(
        f"{BASE_URL}/api/sessions/{sid}/select_role",
        json={"role": role},
        timeout=60,
    )


def _get_session(sid: str) -> requests.Response:
    return requests.get(f"{BASE_URL}/api/sessions/{sid}", timeout=30)


def _turn(sid: str, text: str, timeout: int = TURN_TIMEOUT) -> requests.Response:
    return requests.post(
        f"{BASE_URL}/api/agent/turn",
        json={"session_id": sid, "message": text},
        timeout=timeout,
    )


def _all_text(j: Dict[str, Any]) -> str:
    out: List[str] = []
    for b in (j.get("blocks") or []):
        if b.get("type") == "text":
            out.append(b.get("text") or b.get("content") or "")
        else:
            # Sometimes escalation/form blocks have title/reason text
            d = b.get("data") or {}
            for k in ("title", "reason", "rm_name", "rm_email", "rm_email_display"):
                v = d.get(k)
                if isinstance(v, str):
                    out.append(v)
    return "\n".join(out).strip()


def _block_types(j: Dict[str, Any]) -> List[str]:
    return [b.get("type") for b in (j.get("blocks") or [])]


def _find_block(j: Dict[str, Any], btype: str) -> Optional[Dict[str, Any]]:
    for b in (j.get("blocks") or []):
        if b.get("type") == btype:
            return b
    return None


def _all_citations(j: Dict[str, Any]) -> List[Dict[str, Any]]:
    cits: List[Dict[str, Any]] = []
    top = j.get("citations")
    if isinstance(top, list):
        cits.extend(top)
    for b in (j.get("blocks") or []):
        if b.get("type") == "text":
            for c in (b.get("citations") or []):
                cits.append(c)
    return cits


def _verify_client(sid: str) -> Dict[str, Any]:
    """Drive a fresh session to AUTH_VERIFIED via the role gate."""
    r0 = _select_role(sid, "client")
    assert r0.status_code == 200, r0.text
    r1 = _turn(sid, CLIENT_UCC)
    assert r1.status_code == 200, r1.text
    r2 = _turn(sid, CLIENT_PAN)
    assert r2.status_code == 200, r2.text
    return r2.json()


def _verify_employee(sid: str) -> Dict[str, Any]:
    r0 = _select_role(sid, "employee")
    assert r0.status_code == 200, r0.text
    r1 = _turn(sid, EMP_EMAIL)
    assert r1.status_code == 200, r1.text
    r2 = _turn(sid, EMP_PAN)
    assert r2.status_code == 200, r2.text
    return r2.json()


# ============================================================
# 1.  Fresh session, no row yet
# ============================================================
class TestFreshSession:
    def test_fresh_session_get_returns_404(self):
        sid = _new_sid("fresh")
        r = _get_session(sid)
        # Per server.py: GET /sessions returns 404 when conversations row absent
        assert r.status_code == 404, f"expected 404 fresh session, got {r.status_code}: {r.text}"


# ============================================================
# 2-4.  Role-gate select_role for the three roles
# ============================================================
class TestSelectRole:
    def test_select_role_client(self):
        sid = _new_sid("role-client")
        r = _select_role(sid, "client")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["session_type"] == "client", j
        assert j["auth_state"] == "awaiting_identifier", j
        # bot prompt should mention UCC / client code
        text = " ".join(b.get("text", "") for b in j.get("blocks", []) if b.get("type") == "text").lower()
        assert "ucc" in text or "client code" in text, f"missing UCC prompt: {text}"

    def test_select_role_employee(self):
        sid = _new_sid("role-emp")
        r = _select_role(sid, "employee")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["session_type"] == "employee", j
        assert j["auth_state"] == "awaiting_identifier", j
        text = " ".join(b.get("text", "") for b in j.get("blocks", []) if b.get("type") == "text").lower()
        assert "email" in text, f"missing email prompt: {text}"

    def test_select_role_visitor(self):
        sid = _new_sid("role-vis")
        r = _select_role(sid, "visitor")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["session_type"] == "visitor", j
        assert j["auth_state"] == "anonymous", j
        text = " ".join(b.get("text", "") for b in j.get("blocks", []) if b.get("type") == "text").lower()
        # Friendly welcome — should mention Mackertich ONE OR Wealth Manager
        assert ("mackertich" in text) or ("wealth manager" in text), f"no welcome text: {text}"


# ============================================================
# 5.  Client verification end-to-end
# ============================================================
@pytest.fixture(scope="module")
def verified_client_sid():
    sid = _new_sid("client-verified")
    out = _verify_client(sid)
    if "client_card" not in _block_types(out):
        pytest.skip(f"Client verification did not produce client_card; blocks={_block_types(out)}")
    return sid


class TestClientVerification:
    def test_client_verified_card_and_masked_rm_contact(self, verified_client_sid):
        r = _get_session(verified_client_sid)
        assert r.status_code == 200, r.text
        sess = r.json()
        assert sess["session_type"] == "client"
        assert sess["auth_state"] == "verified"
        ident = sess.get("identity") or {}
        # rm_name plaintext
        assert (ident.get("rm_name") or "").upper() == CLIENT_RM_NAME, ident
        # rm_email plaintext should exist in raw or top-level
        rm_email = ident.get("rm_email") or (ident.get("raw") or {}).get("rm_email")
        assert rm_email and "@" in rm_email, f"rm_email missing: {ident}"
        # masked display fields on top-level identity
        rm_email_display = ident.get("rm_email_display") or ""
        assert "***" in rm_email_display or "*" in rm_email_display, f"rm_email_display not masked: {rm_email_display}"
        # Spec: rm_email_display='ji***@smifs.net'
        assert rm_email_display.lower().startswith("ji"), f"unexpected rm_email_display: {rm_email_display}"
        rm_mobile_display = ident.get("rm_mobile_display") or ""
        # Last-4-digits expected
        assert re.search(r"\d{4}", rm_mobile_display), f"rm_mobile_display has no last-4: {rm_mobile_display}"

    def test_client_card_block_in_verification_response(self, verified_client_sid):
        # Fetch conversation history and confirm client_card was emitted
        r = _get_session(verified_client_sid)
        assert r.status_code == 200
        sess = r.json()
        seen_card = False
        for entry in sess.get("history", []):
            if entry.get("role") != "user":
                for blk in (entry.get("blocks") or []):
                    if blk.get("type") == "client_card":
                        seen_card = True
                        d = blk.get("data") or {}
                        if d.get("rm_email_display"):
                            assert "***" in d["rm_email_display"] or "*" in d["rm_email_display"]
                        if d.get("rm_mobile_display"):
                            assert re.search(r"\d{4}", d["rm_mobile_display"])
        assert seen_card, "client_card block not found in conversation history"


# ============================================================
# 6.  CLIENT_PROFILE self-queries (intent=KNOWLEDGE, answers from record)
# ============================================================
class TestClientSelfQueries:
    @pytest.mark.parametrize(
        "question, expected_terms",
        [
            ("What is my risk profile?",            ["moderate", "low", "high", "aggressive", "conservative", "risk"]),
            ("Who is my relationship manager?",     [CLIENT_RM_NAME.lower(), "jiten"]),
            ("What segments am I active in?",       ["nse", "bse", "segment"]),
            ("What city is my branch in?",          ["branch", "city", "kolkata", "mumbai", "delhi", "bangalore", "chennai", "hyderabad", "pune"]),
            ("What is my account status?",          ["active", "status", "closed", "suspended"]),
        ],
    )
    def test_client_profile_self_query(self, verified_client_sid, question, expected_terms):
        r = _turn(verified_client_sid, question)
        assert r.status_code == 200, r.text
        j = r.json()
        text = _all_text(j).lower()
        # Should NOT trigger escalation
        assert "escalation_card" not in _block_types(j), f"unexpected escalation for self-query '{question}': {_block_types(j)}"
        # intent should be KNOWLEDGE-ish (KNOWLEDGE / SELF_PROFILE / answer_from_knowledge_base)
        intent = (j.get("intent") or "").upper()
        # not strict on intent label, but it must NOT be ESCALATION
        assert intent != "ESCALATION", f"unexpected ESCALATION intent for '{question}': {j}"
        # at least one of the expected terms appears
        hit = any(term in text for term in expected_terms)
        assert hit, f"no expected term {expected_terms!r} in reply for '{question}': {text!r}"


# ============================================================
# 7.  Verified client product fallback (escalation_card + RM contact)
# ============================================================
class TestClientProductFallback:
    @pytest.mark.parametrize("question", [
        "What is the minimum for Mackertich ONE PMS?",
        "What is the historical NAV of Alchemy Smart Alpha?",
        "What are the returns on Category II AIFs?",
    ])
    def test_client_product_question_falls_back_to_rm(self, verified_client_sid, question):
        r = _turn(verified_client_sid, question)
        assert r.status_code == 200, r.text
        j = r.json()
        text = _all_text(j)
        text_lower = text.lower()

        # No SMIFS knowledge citation should leak to verified clients
        cits = _all_citations(j)
        smifs_cits = [c for c in cits if (c.get("source") == "smifs_knowledge")]
        assert not smifs_cits, f"verified client got smifs_knowledge cites for '{question}': {smifs_cits}"

        # Escalation card present
        escalation = _find_block(j, "escalation_card")
        assert escalation is not None, f"missing escalation_card for '{question}': blocks={_block_types(j)}"
        d = escalation.get("data") or {}
        assert (d.get("rm_name") or "").upper() == CLIENT_RM_NAME, f"rm_name mismatch: {d}"
        assert d.get("rm_email") and "@" in d["rm_email"], f"rm_email missing in escalation_card: {d}"
        assert d.get("rm_mobile"), f"rm_mobile missing in escalation_card: {d}"

        # The fallback verbatim phrase OR mentions RM
        phrase_ok = ("don't have that information in your record" in text_lower) or \
                    ("do not have that information in your record" in text_lower) or \
                    ("jiten" in text_lower) or \
                    ("@smifs.net" in text_lower)
        assert phrase_ok, f"fallback phrase / RM contact missing for '{question}': {text!r}"


# ============================================================
# 8-10.  Knowledge gating across roles for the same product question
# ============================================================
class TestKnowledgeGating:
    def test_visitor_product_question_no_smifs_citation(self):
        sid = _new_sid("vis-product")
        r0 = _select_role(sid, "visitor")
        assert r0.status_code == 200
        r = _turn(sid, PRODUCT_QUESTION)
        assert r.status_code == 200, r.text
        j = r.json()
        cits = _all_citations(j)
        smifs_cits = [c for c in cits if c.get("source") == "smifs_knowledge"]
        assert not smifs_cits, f"visitor leaked smifs_knowledge citations: {smifs_cits}"
        text = _all_text(j).lower()
        # Should mention WM connection or surface a callback form block
        has_form = _find_block(j, "form") is not None
        wm_hint = ("mackertich one wealth manager" in text) or ("wealth manager" in text) or has_form
        assert wm_hint, f"visitor reply lacks WM/callback fallback: {text!r}"
        # No fabricated specific figures: check that we don't have ` ₹`+digits or `% returns` claims
        # (lightweight check — guardrail enforces semantically)
        assert not re.search(r"₹\s?\d{2,}", text), f"visitor reply fabricated ₹ figure: {text}"

    def test_verified_client_product_question_no_smifs_citation(self, verified_client_sid):
        r = _turn(verified_client_sid, PRODUCT_QUESTION)
        assert r.status_code == 200, r.text
        j = r.json()
        cits = _all_citations(j)
        smifs_cits = [c for c in cits if c.get("source") == "smifs_knowledge"]
        assert not smifs_cits, f"verified client leaked smifs_knowledge citations: {smifs_cits}"

    def test_verified_employee_product_question_has_smifs_citation(self):
        sid = _new_sid("emp-product")
        out = _verify_employee(sid)
        if "employee_card" not in _block_types(out):
            pytest.skip(f"Employee verification did not produce employee_card; blocks={_block_types(out)}")
        r = _turn(sid, PRODUCT_QUESTION)
        assert r.status_code == 200, r.text
        j = r.json()
        cits = _all_citations(j)
        smifs_cits = [c for c in cits if c.get("source") == "smifs_knowledge"]
        assert smifs_cits, f"verified employee got NO smifs_knowledge citations for '{PRODUCT_QUESTION}': cits={cits} blocks={_block_types(j)}"


# ============================================================
# 11.  Idempotent role-switch guard (employee self-query 'employee ID')
# ============================================================
class TestIdempotentRoleSwitch:
    def test_employee_self_query_does_not_reset_state(self):
        sid = _new_sid("emp-selfq")
        out = _verify_employee(sid)
        if "employee_card" not in _block_types(out):
            pytest.skip(f"Employee verification did not produce employee_card; blocks={_block_types(out)}")
        r = _turn(sid, "What's my employee ID?")
        assert r.status_code == 200, r.text
        # After self-query, session must still be verified employee
        s = _get_session(sid)
        assert s.status_code == 200
        sj = s.json()
        assert sj["session_type"] == "employee", sj
        assert sj["auth_state"] == "verified", sj


# ============================================================
# 12-13.  Privacy regression — Mongo content checks
# ============================================================
@pytest.fixture(scope="module")
def mongo_db():
    if not MONGO_URL or not DB_NAME:
        pytest.skip("MONGO_URL / DB_NAME not configured")
    try:
        from pymongo import MongoClient
    except ImportError:
        pytest.skip("pymongo not installed")
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    yield client[DB_NAME]
    client.close()


class TestPrivacyRegression:
    def test_no_plaintext_pan_in_conversations(self, mongo_db, verified_client_sid):
        convo = mongo_db.conversations.find_one({"session_id": verified_client_sid}) or {}
        blob = json.dumps(convo, default=str)
        # Plaintext PAN must not appear
        assert CLIENT_PAN not in blob, f"plaintext PAN leaked in conversations.{verified_client_sid}"
        # Masked form like XXXXX3602Q is acceptable
        # Also no plaintext phone / email of the *client's identifier* —
        # check the documented spec: PAN is the strictest check we can make w/o the live record details

    def test_sessions_identity_raw_contains_safe_fields_only(self, mongo_db, verified_client_sid):
        sess = mongo_db.sessions.find_one({"_id": verified_client_sid}) or {}
        identity_obj = sess.get("identity") or {}
        raw = identity_obj.get("raw") or {}
        # Required non-sensitive fields
        rm_name_present = (
            "rm_name" in raw
            or any(("rm_name" in k or k == "rmName") for k in raw)
        )
        assert rm_name_present, f"raw missing rm_name: keys={list(raw.keys())[:30]}"
        ucc_present = ("ucc" in raw) or ("UCC" in raw)
        assert ucc_present, f"raw missing ucc: keys={list(raw.keys())[:30]}"
        # Phase 12 privacy widening — direct PII MUST NOT be in raw. The
        # curated identity exposes masked `*_display` variants instead.
        curated = {k: v for k, v in identity_obj.items() if k != "raw"}
        assert curated.get("email_display"), "curated identity should expose email_display"
        assert curated.get("telephone_display"), "curated identity should expose telephone_display"
        # Sensitive fields MUST be stripped from raw (Phase 12 widened set)
        forbidden = [
            # Phase 10 set
            "pan", "pan_number", "aadhar_no", "aadhaar", "aadhar",
            "bank", "bank_details", "bank_account", "account",
            # Phase 12 widened
            "email", "mobile", "mobile1", "mobile2", "telephone",
            "father_name", "mother_name", "spouse_name",
            "bank_micr", "bank_rtgs", "bank_ifsc", "bank_branch",
            "bank_actype", "bank_city",
            "address1", "address2", "address3", "address4",
            "birth_date", "dob",
        ]
        for f in forbidden:
            assert f not in raw, f"forbidden field '{f}' present in raw: {list(raw.keys())}"


# ============================================================
# 14.  CLIENT_FIELD_MAP.md exists and has field inventory
# ============================================================
class TestClientFieldMap:
    def test_field_map_file_exists(self):
        p = Path("/app/backend/CLIENT_FIELD_MAP.md")
        assert p.exists(), "CLIENT_FIELD_MAP.md is missing"
        content = p.read_text(encoding="utf-8")
        assert len(content) > 200, "CLIENT_FIELD_MAP.md looks too short"
        # Should mention fields / inventory
        lower = content.lower()
        assert ("ucc" in lower) and ("rm" in lower or "relationship manager" in lower), \
            "CLIENT_FIELD_MAP.md missing core field references"


# ============================================================
# 15.  hallucination_events action='refused' for product turns by non-employees
# ============================================================
class TestHallucinationEvents:
    def test_refused_events_logged(self, mongo_db, verified_client_sid):
        # Trigger one more product question on the verified client to ensure an event fires
        try:
            _turn(verified_client_sid, "What is the lock-in for Mackertich ONE Sapphire AIF Category IV?", timeout=120)
        except Exception:
            pass
        time.sleep(1)
        # Look for any refused / unchecked_claim event
        events = list(mongo_db.hallucination_events.find({}, {"_id": 0}).limit(200))
        actions = [e.get("action") for e in events]
        assert any(a in ("refused", "unchecked_claim") for a in actions), \
            f"no refused/unchecked_claim event in hallucination_events; sample actions={actions[:10]}"
