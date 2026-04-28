"""Phase 3 backend tests — In-chat verification + session rehydration + signout."""
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip()
                break
BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="session")
def http():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _turn(http, message, sid=None, timeout=120):
    payload = {"message": message}
    if sid:
        payload["session_id"] = sid
    r = http.post(f"{API}/agent/turn", json=payload, timeout=timeout)
    return r


def _text(d):
    parts = []
    for b in d.get("blocks", []):
        if b.get("type") == "text":
            parts.append(b.get("text", ""))
    return "\n".join(parts)


# ---- Verification happy path: SMIFS001 → 1978 → Mumbai ----
class TestVerificationHappyPath:
    def test_full_verification_flow(self, http):
        # Step 1: anonymous -> AUTH_CHALLENGE
        r1 = _turn(http, "My client code is SMIFS001")
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        sid = d1["session_id"]
        assert d1["intent"] == "AUTH_CHALLENGE", f"intent={d1['intent']}, full={d1}"
        t1 = _text(d1).lower()
        assert "1 of 2" in t1, f"missing '1 of 2': {t1[:300]}"
        assert "year of birth" in t1, f"missing 'Year of birth' prompt: {t1[:300]}"

        # Step 2: wrong Q1
        r2 = _turn(http, "1990", sid=sid)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["intent"] == "AUTH_CHALLENGE", f"intent={d2['intent']}"
        t2 = _text(d2).lower()
        assert "1/3" in t2, f"missing attempts counter: {t2[:300]}"
        assert "year of birth" in t2, f"should re-ask Year of birth: {t2[:300]}"

        # Step 3: correct Q1 = 1978
        r3 = _turn(http, "1978", sid=sid)
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["intent"] == "AUTH_CHALLENGE", f"intent={d3['intent']}"
        t3 = _text(d3).lower()
        assert "2 of 2" in t3, f"missing '2 of 2': {t3[:300]}"
        assert "city" in t3, f"missing 'City' prompt: {t3[:300]}"

        # Step 4: correct Q2 = Mumbai → AUTH_VERIFIED
        r4 = _turn(http, "Mumbai", sid=sid)
        assert r4.status_code == 200
        d4 = r4.json()
        assert d4["intent"] == "AUTH_VERIFIED", f"intent={d4['intent']}, blocks={d4.get('blocks')}"
        types = [b["type"] for b in d4["blocks"]]
        assert "text" in types and "client_card" in types, f"got types={types}"
        cc = next(b for b in d4["blocks"] if b["type"] == "client_card")
        data = cc.get("data") or cc
        assert data.get("verified") is True, f"verified={data.get('verified')}"
        assert data.get("name") == "Aarav Mehta"
        hs = (data.get("holdings_summary") or "")
        assert "₹1.2 Cr" in hs or "1.2 Cr" in hs, f"holdings_summary missing 1.2 Cr: {hs}"

        # Step 5: personalization injection — KNOWLEDGE addresses Aarav and mentions a holding
        r5 = _turn(http, "How does taxation work for the asset classes I currently hold?", sid=sid)
        assert r5.status_code == 200
        d5 = r5.json()
        assert d5["intent"] == "KNOWLEDGE", f"intent={d5['intent']}"
        t5 = _text(d5)
        t5l = t5.lower()
        assert "aarav" in t5l, f"Reply should address user as 'Aarav': {t5[:400]}"
        assert any(tok in t5l for tok in ["ncd", "aif", "mutual fund"]), (
            f"Reply should mention at least one held asset: {t5[:400]}"
        )


# ---- Lockout flow ----
class TestLockoutFlow:
    def test_three_wrong_then_locked(self, http):
        r1 = _turn(http, "My client code is SMIFS003")
        assert r1.status_code == 200
        d1 = r1.json()
        sid = d1["session_id"]
        assert d1["intent"] == "AUTH_CHALLENGE"

        # 3 wrong answers
        for i in range(2):
            ri = _turn(http, "1234", sid=sid)
            assert ri.status_code == 200
            di = ri.json()
            assert di["intent"] == "AUTH_CHALLENGE", f"step {i+1} intent={di['intent']}"

        r3 = _turn(http, "9999", sid=sid)
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["intent"] == "AUTH_LOCKED", f"3rd-wrong intent={d3['intent']}, full={d3}"
        types = [b["type"] for b in d3["blocks"]]
        assert "escalation_card" in types, f"expected escalation_card, got {types}"

        # Subsequent message stays locked
        r4 = _turn(http, "Mumbai", sid=sid)
        assert r4.status_code == 200
        d4 = r4.json()
        assert d4["intent"] == "AUTH_LOCKED", f"subsequent intent={d4['intent']}"


# ---- Unknown code: no penalty, session stays anonymous ----
class TestUnknownCode:
    def test_unknown_code_no_penalty(self, http):
        r = _turn(http, "My client code is SMIFS999")
        assert r.status_code == 200
        d = r.json()
        sid = d["session_id"]
        assert d["intent"] == "AUTH_NOT_FOUND", f"intent={d['intent']}"
        text = _text(d).lower()
        assert "couldn" in text, f"text should mention 'couldn't find': {text[:200]}"

        # session stays anonymous
        rs = http.get(f"{API}/sessions/{sid}", timeout=15)
        assert rs.status_code == 200
        assert rs.json().get("auth_state") == "anonymous", f"state={rs.json().get('auth_state')}"


# ---- /api/sessions/{sid} GET: verified, 404, signout reset ----
class TestSessionEndpoints:
    def test_get_session_404_for_unknown(self, http):
        bogus = f"non-existent-{uuid.uuid4().hex}"
        r = http.get(f"{API}/sessions/{bogus}", timeout=15)
        assert r.status_code == 404

    def test_get_session_for_verified(self, http):
        # Verify SMIFS002 (Priya/1982/Bengaluru) for isolation from other tests
        r1 = _turn(http, "My client code is SMIFS002")
        sid = r1.json()["session_id"]
        _turn(http, "1982", sid=sid)
        r3 = _turn(http, "Bengaluru", sid=sid)
        assert r3.json()["intent"] == "AUTH_VERIFIED"

        time.sleep(0.4)
        rs = http.get(f"{API}/sessions/{sid}", timeout=15)
        assert rs.status_code == 200
        body = rs.json()
        assert body["session_id"] == sid
        assert body["auth_state"] == "verified"
        client = body.get("client") or {}
        assert client.get("name") == "Priya Iyer"
        assert client.get("code") == "SMIFS002"
        history = body.get("history") or []
        assert len(history) >= 6, f"expected >=6 history entries, got {len(history)}"
        # Alternating user/assistant; user has 'text', assistant has 'blocks'
        assert history[0]["role"] == "user" and "text" in history[0]
        assert history[1]["role"] == "assistant" and "blocks" in history[1]
        # Assistant entries carry intent
        assist = [h for h in history if h["role"] == "assistant"]
        assert any(h.get("intent") == "AUTH_VERIFIED" for h in assist), (
            "no AUTH_VERIFIED assistant entry in history"
        )
        # No mongo _id leak
        assert "_id" not in body

    def test_signout_resets_state(self, http):
        # Verify SMIFS004 then sign out
        r1 = _turn(http, "My client code is SMIFS004")
        sid = r1.json()["session_id"]
        _turn(http, "1975", sid=sid)
        r3 = _turn(http, "Hyderabad", sid=sid)
        assert r3.json()["intent"] == "AUTH_VERIFIED"

        rso = http.post(f"{API}/sessions/{sid}/signout", timeout=15)
        assert rso.status_code == 200
        body = rso.json()
        assert body["auth_state"] == "anonymous"
        assert body.get("client") is None

        rs = http.get(f"{API}/sessions/{sid}", timeout=15)
        assert rs.status_code == 200
        assert rs.json()["auth_state"] == "anonymous"
        assert rs.json().get("client") is None


# ---- Phase 0/1/2 unbroken ----
class TestPhase012Unbroken:
    def test_anon_knowledge_aif_still_grounded(self, http):
        r = _turn(http, "What is an AIF?")
        d = r.json()
        assert d["intent"] == "KNOWLEDGE"
        doc_ids = [c["doc_id"] for c in (d.get("citations") or [])]
        assert "aif_overview" in doc_ids

    def test_anon_market_data_reliance(self, http):
        r = _turn(http, "What is the price of RELIANCE?")
        d = r.json()
        assert d["intent"] == "MARKET_DATA"
        mc = next(b for b in d["blocks"] if b["type"] == "market_card")
        data = mc.get("data") or mc
        assert data.get("symbol") == "RELIANCE"

    def test_anon_lead_capture_ncd(self, http):
        r = _turn(http, "I am interested in investing in NCDs")
        d = r.json()
        assert d["intent"] == "LEAD_CAPTURE"
        types = [b["type"] for b in d["blocks"]]
        assert "form" in types

    def test_legacy_chat_endpoint_works(self, http):
        r = http.post(f"{API}/chat", json={"message": "What is an AIF?"}, timeout=120)
        assert r.status_code == 200
        d = r.json()
        for k in ("session_id", "reply", "model", "grounded", "citations"):
            assert k in d
        assert "blocks" not in d
