"""Phase 6 — Identity flow tests with mocked OrgLens responses.

Run from /app/backend:
    pytest tests/test_identity_phase6.py -v -s
"""
from __future__ import annotations
import asyncio
import os
import sys
import unittest.mock as mock
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# Force a dedicated test DB so we don't pollute production data.
os.environ["DB_NAME"] = os.environ.get("DB_NAME_TEST", "test_database_phase6")

import identity as id_mod
from agents import auth_agent, orchestrator
from motor.motor_asyncio import AsyncIOMotorClient


# Sample real responses (anonymized format from /api/v1/employees and /clients)
EMPLOYEE_REC = {
    "user_id": "1024761",
    "employee_id": "SMWM-25031054",
    "email": "test.employee@smifs.com",
    "name": "Test Aaditya Employee",
    "first_name": "Test",
    "designation": "Research Associate",
    "department": "Institutional Equities",
    "location": "Mumbai (Branch)",
    "employment_status": "Active",
    "company": "SMIFS LIMITED",
    "business_unit": "Capital Markets & Advisory",
    "reports_to_name": "Awanish Chandra",
    "pan_number": "BQPPJ8323M",  # mock PAN — will be hashed, never persisted plaintext
    "date_of_joining": "03-03-2025",
}

CLIENT_REC = {
    "ucc": "63876",
    "email": "balaram.patro143@gmail.com",
    "status": "Active",
    "dp_name": "SMIFS LIMITED",
    "rm_code": "SMIFSSR259",
    "rm_name": "JITEN SAHOO",
    "sub_broker_code": "BP299",
    "sub_broker_name": "SUDHIR KUMAR PATRA [BP299]",
    "risk_profile": "Medium Risk",
    "city": "GANJAM-760001",
    "state": "Odisha",
    "occupation": "Business",
    "income_range": "Rs. 500000 - 1000000",
    "nse": "Yes", "bse": "Yes",
    "pan": "ARIPP3602Q",
    "aadhar_no": "850907534236",
    "address1": "REDACTED",
    "bank": "REDACTED",
}


@pytest.fixture
async def db():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    d = cli[os.environ["DB_NAME"]]
    yield d
    await d.sessions.delete_many({})
    await d.conversations.delete_many({})
    await d.session_archives.delete_many({})
    cli.close()


# ---------------- Privacy unit tests ----------------
def test_pan_redaction_text():
    txt = "My PAN is BQPPJ8323M and another one bqppj8323m"
    redacted = id_mod.redact_pan_in_text(txt)
    assert "BQPPJ8323M" not in redacted.upper().replace("XXXXX8323X", "")
    assert "XXXXX8323X" in redacted

def test_pan_log_scrub():
    s = id_mod.sanitize_for_log("Lookup for ARIPP3602Q completed")
    assert "ARIPP3602Q" not in s
    assert "XXXXX####X" in s

def test_pan_hash_is_hmac():
    h1 = id_mod.pan_hash("BQPPJ8323M")
    h2 = id_mod.pan_hash("bqppj8323m  ")
    assert h1 == h2  # normalization
    assert len(h1) == 64
    assert h1 != "BQPPJ8323M"

def test_extractors():
    assert id_mod.extract_smifs_email("hi, my email is jane.doe@smifs.com").endswith("@smifs.com")
    assert id_mod.extract_pan("My pan is bqppj8323m") == "BQPPJ8323M"
    assert id_mod.extract_ucc("my ucc is 63876", require_client_context=True) == "63876"
    assert id_mod.detect_role_intent("I am an employee") == "employee"
    assert id_mod.detect_role_intent("I am a client") == "client"
    assert id_mod.detect_role_intent("verify me please") == "ambiguous_verify"


# ---------------- Flow tests with mocked OrgLens ----------------
@pytest.mark.asyncio
async def test_visitor_flow(db):
    sid = "test-visitor-001"
    await db.sessions.delete_many({"_id": sid})
    await db.conversations.delete_many({"session_id": sid})
    out = await orchestrator.run_turn(db, sid, "What is an AIF?")
    assert out["session_id"] == sid
    sess = await db.sessions.find_one({"_id": sid})
    assert sess["session_type"] == "visitor"
    assert sess["auth_state"] == "anonymous"


@pytest.mark.asyncio
async def test_employee_full_verification(db):
    sid = "test-employee-001"
    await db.sessions.delete_many({"_id": sid})
    await db.conversations.delete_many({"session_id": sid})

    with mock.patch("identity.lookup_employee_by_email", return_value=EMPLOYEE_REC):
        # Step 1: user sends SMIFS email
        out = await orchestrator.run_turn(db, sid, "I am an employee, my email is test.employee@smifs.com")
        assert out["intent"] == "AUTH_PAN_REQUEST"
        sess = await db.sessions.find_one({"_id": sid})
        assert sess["auth_state"] == "awaiting_pan"
        assert sess["session_type"] == "employee"
        assert sess["expected_pan_hash"] is not None
        assert sess["pending_record"]["first_name"] == "Test"

        # Step 2: provide correct PAN
        out2 = await orchestrator.run_turn(db, sid, "My PAN is BQPPJ8323M")
        assert out2["intent"] == "AUTH_VERIFIED"
        sess2 = await db.sessions.find_one({"_id": sid})
        assert sess2["auth_state"] == "verified"
        assert sess2["session_type"] == "employee"
        assert sess2["identity"]["designation"] == "Research Associate"
        assert sess2["consent_to_ingest"] is True
        # PAN never plaintext anywhere
        for col in [db.sessions, db.conversations]:
            cursor = col.find({})
            async for doc in cursor:
                doc_str = str(doc)
                assert "BQPPJ8323M" not in doc_str, f"PAN leaked into {col.name}: {doc_str[:200]}"

        # Verify employee_card emitted
        cards = [b for b in out2["blocks"] if b.get("type") == "employee_card"]
        assert len(cards) == 1
        assert cards[0]["data"]["designation"] == "Research Associate"

        # Verify archive snapshot
        arc = await db.session_archives.find_one({"_id": sid})
        assert arc is not None
        assert arc["session_type"] == "employee"
        assert arc["consent_to_ingest"] is True


@pytest.mark.asyncio
async def test_employee_wrong_pan_locks_after_3(db):
    sid = "test-employee-lock"
    await db.sessions.delete_many({"_id": sid})
    await db.conversations.delete_many({"session_id": sid})

    with mock.patch("identity.lookup_employee_by_email", return_value=EMPLOYEE_REC):
        await orchestrator.run_turn(db, sid, "test.employee@smifs.com")
        for i in range(3):
            r = await orchestrator.run_turn(db, sid, "WRONG1234X")
            if i < 2:
                assert "doesn't match" in r["blocks"][0]["text"].lower()
        sess = await db.sessions.find_one({"_id": sid})
        assert sess["auth_state"] == "locked"


@pytest.mark.asyncio
async def test_employee_404_no_lock(db):
    sid = "test-employee-404"
    await db.sessions.delete_many({"_id": sid})
    await db.conversations.delete_many({"session_id": sid})

    with mock.patch("identity.lookup_employee_by_email", return_value=None):
        out = await orchestrator.run_turn(db, sid, "fake.user@smifs.com")
        sess = await db.sessions.find_one({"_id": sid})
        assert sess["auth_state"] == "anonymous"
        assert "couldn't find" in out["blocks"][0]["text"].lower()
        assert sess["failed_attempts"] == 0  # no lock


@pytest.mark.asyncio
async def test_client_full_verification(db):
    sid = "test-client-001"
    await db.sessions.delete_many({"_id": sid})
    await db.conversations.delete_many({"session_id": sid})

    with mock.patch("identity.lookup_client_by_ucc", return_value=CLIENT_REC):
        out = await orchestrator.run_turn(db, sid, "I am a client, my UCC is 63876")
        assert out["intent"] == "AUTH_PAN_REQUEST"

        out2 = await orchestrator.run_turn(db, sid, "My PAN is ARIPP3602Q")
        assert out2["intent"] == "AUTH_VERIFIED"
        sess = await db.sessions.find_one({"_id": sid})
        assert sess["session_type"] == "client"
        assert sess["identity"]["ucc"] == "63876"
        assert sess["identity"]["rm_name"] == "JITEN SAHOO"
        assert sess["consent_to_ingest"] is False  # client default is False

        # PAN must be redacted in conversations
        convo = await db.conversations.find_one({"session_id": sid})
        for m in convo["messages"]:
            assert "ARIPP3602Q" not in (m.get("content") or "")
            assert "ARIPP3602Q" not in str(m.get("blocks") or [])

        cards = [b for b in out2["blocks"] if b.get("type") == "client_card"]
        assert len(cards) == 1
        assert cards[0]["data"]["rm_name"] == "JITEN SAHOO"


@pytest.mark.asyncio
async def test_role_inquiry_when_ambiguous(db):
    sid = "test-role-amb"
    await db.sessions.delete_many({"_id": sid})
    await db.conversations.delete_many({"session_id": sid})

    out = await orchestrator.run_turn(db, sid, "verify me")
    sess = await db.sessions.find_one({"_id": sid})
    assert sess["auth_state"] == "awaiting_role"
    assert "client" in out["blocks"][0]["text"].lower() and "employee" in out["blocks"][0]["text"].lower()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))
