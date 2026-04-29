"""Phase 7 — idle expiry + identity-keyed rehydration backend tests.

Covers:
  * Idle expiry via mutating sessions.updated_at_dt → 3 min ago
  * /api/agent/turn returns NEW session_id + resume_offer block
  * GET /api/sessions/{sid}/rehydration_candidates
  * POST /api/sessions/{sid}/resume — matching hashes → 200, merged history
  * POST /api/sessions/{sid}/resume — non-matching hashes → 403
  * POST /api/sessions/{sid}/decline_resume — marks priors lifecycle=ended
  * Mongo sessions docs store HMAC hashes, NEVER plaintext PAN/Aadhaar/
    email/phone at the root level
  * 30-day TTL index on sessions.updated_at_dt
"""
import os
import time
import uuid
from datetime import datetime, timezone, timedelta

import pymongo
import pytest
import requests
from dotenv import load_dotenv

# Load backend env to read MONGO_URL/DB_NAME for direct Mongo inspection
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

assert BASE_URL, "REACT_APP_BACKEND_URL must be set"
assert MONGO_URL and DB_NAME, "MONGO_URL and DB_NAME must be configured"

EMP_EMAIL = "aaditya.jaiswal@smifs.com"
EMP_PAN = "BQPPJ8323M"


# --------------------------------- fixtures ---------------------------------
@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def mongo():
    cli = pymongo.MongoClient(MONGO_URL)
    try:
        yield cli[DB_NAME]
    finally:
        cli.close()


def _age_session(mongo_db, sid: str, minutes: int = 3):
    """Backdate a session's updated_at_dt so the lifecycle thinks it's idle."""
    past = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    res = mongo_db.sessions.update_one(
        {"_id": sid},
        {"$set": {"updated_at_dt": past, "updated_at": past.isoformat()}},
    )
    assert res.matched_count == 1, f"session {sid} not found to age"


def _turn(api, sid, message):
    r = api.post(f"{BASE_URL}/api/agent/turn",
                 json={"session_id": sid, "message": message}, timeout=60)
    return r


def _verify_employee(api, sid):
    """Walk the two-step auth flow (email → PAN) for the seeded employee."""
    # step 1 — email
    r = _turn(api, sid, f"Please verify me. My email is {EMP_EMAIL}.")
    assert r.status_code == 200, r.text
    # step 2 — PAN
    r = _turn(api, sid, f"My PAN is {EMP_PAN}")
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------- tests ------------------------------------
class TestTTLIndex:
    """30-day TTL index on sessions.updated_at_dt (Phase 7)."""

    def test_30d_ttl_index_exists(self, mongo):
        idx = list(mongo.sessions.list_indexes())
        ttl = [i for i in idx if i.get("expireAfterSeconds") is not None]
        assert ttl, f"no TTL index on sessions; indexes={[i['name'] for i in idx]}"
        # Exactly one current TTL and it should be 30 days (2592000s)
        secs = [i["expireAfterSeconds"] for i in ttl]
        assert 2592000 in secs, f"expected 30d (2592000s) TTL, got {secs}"
        # must be on updated_at_dt
        target = [i for i in ttl if i["expireAfterSeconds"] == 2592000][0]
        keys = list(target["key"].keys())
        assert keys == ["updated_at_dt"], f"TTL not on updated_at_dt: {keys}"


class TestIdentityHashStorageAndPII:
    """After employee verification, session doc must store hashes and
    must NOT store plaintext PAN/Aadhaar/email/phone at its root."""

    def test_employee_verify_writes_hashes_no_plaintext(self, api, mongo):
        sid = str(uuid.uuid4())
        resp = _verify_employee(api, sid)
        # auth flow may return a fresh sid if server minted one — pick it up
        sid = resp.get("session_id", sid)

        sess = mongo.sessions.find_one({"_id": sid})
        assert sess, "session doc not persisted"

        # ---- hashes present ----
        assert sess.get("emp_id_hash"), "emp_id_hash missing"
        assert sess.get("email_hash"), "email_hash missing"
        # pan_hash is stored (fingerprint only — never reversible)
        assert sess.get("pan_hash"), "pan_hash missing"

        # ---- NO plaintext PII at doc root ----
        forbidden_root_keys = {
            "pan", "pan_number", "aadhar", "aadhaar", "aadhar_no",
            "email", "personal_mobile", "mobile_number", "phone",
        }
        present_forbidden = forbidden_root_keys & set(sess.keys())
        assert not present_forbidden, f"plaintext PII at session root: {present_forbidden}"

        # ---- plaintext PAN must not appear anywhere in the doc ----
        import json as _json
        dumped = _json.dumps(sess, default=str)
        assert EMP_PAN not in dumped, "plaintext PAN found in session doc"
        # Full email MAY be inside identity.raw (scrubbed). But Phase 7 strips
        # it too — verify:
        ident = sess.get("identity") or {}
        raw = ident.get("raw") or {}
        assert "email" not in raw, "raw.email leaked into storage"
        assert not any(k in raw for k in ("mobile_number", "personal_mobile",
                                          "phone", "pan", "aadhar", "aadhar_no")), \
            f"raw contains forbidden keys: {list(raw.keys())}"


class TestIdleExpiryAndResumeOffer:
    """Backdating updated_at_dt simulates >120s idle and triggers expiry."""

    def test_idle_turn_returns_new_sid_and_resume_offer(self, api, mongo):
        sid = str(uuid.uuid4())
        # Seed a verified conversation with real prior messages
        resp = _verify_employee(api, sid)
        sid = resp.get("session_id", sid)
        # add a normal message so prior has content
        r = _turn(api, sid, "What mutual funds do you offer?")
        assert r.status_code == 200

        # Idle the session (>120s)
        _age_session(mongo, sid, minutes=3)

        # Next turn must mint a NEW session id
        r2 = _turn(api, sid, "Are you still there?")
        assert r2.status_code == 200, r2.text
        body = r2.json()
        new_sid = body["session_id"]
        assert new_sid != sid, "expected a newly-minted session id after idle expiry"

        # NOTE: TurnResponse Pydantic model does NOT expose prior_session_id or
        # resume_offer at the response root — those are only preserved on the
        # /agent/turn/stream "result" event. For /agent/turn we verify via the
        # prepended resume_offer block instead (orchestrator line 438-440).
        blocks = body.get("blocks") or []
        resume_blocks = [b for b in blocks if b.get("type") == "resume_offer"]
        assert resume_blocks, f"resume_offer block missing from blocks: {[b.get('type') for b in blocks]}"
        candidates = (resume_blocks[0].get("data") or {}).get("candidates") or []
        assert candidates, "resume_offer.data.candidates must be non-empty"
        # Our just-expired session should be present in candidates
        pids = [c.get("prior_session_id") for c in candidates]
        assert sid in pids, f"expired sid {sid} not offered; got {pids}"
        c0 = next(c for c in candidates if c.get("prior_session_id") == sid)
        for k in ("prior_session_id", "summary", "message_count", "ended_at", "session_type"):
            assert k in c0, f"candidate missing '{k}': {c0}"

        # Prior is now lifecycle=expired (or ended) in Mongo
        prior = mongo.sessions.find_one({"_id": sid}, {"lifecycle": 1})
        assert prior and prior.get("lifecycle") in ("expired", "ended")

        # Stash for next tests
        pytest._phase7_prior_sid = sid
        pytest._phase7_new_sid = new_sid


class TestRehydrationCandidatesEndpoint:
    def test_get_candidates_returns_prior(self, api):
        new_sid = getattr(pytest, "_phase7_new_sid", None)
        prior_sid = getattr(pytest, "_phase7_prior_sid", None)
        assert new_sid and prior_sid, "prior test did not stash sids"

        r = api.get(f"{BASE_URL}/api/sessions/{new_sid}/rehydration_candidates", timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["session_id"] == new_sid
        assert isinstance(data["candidates"], list)
        pids = [c["prior_session_id"] for c in data["candidates"]]
        assert prior_sid in pids, f"prior {prior_sid} not in candidates {pids}"
        c = next(c for c in data["candidates"] if c["prior_session_id"] == prior_sid)
        for k in ("summary", "message_count", "ended_at", "session_type"):
            assert k in c, f"candidate missing '{k}'"
        assert isinstance(c["message_count"], int) and c["message_count"] >= 1


class TestResume:
    def test_resume_matching_identity_succeeds(self, api):
        new_sid = getattr(pytest, "_phase7_new_sid", None)
        prior_sid = getattr(pytest, "_phase7_prior_sid", None)
        assert new_sid and prior_sid

        r = api.post(
            f"{BASE_URL}/api/sessions/{new_sid}/resume",
            json={"prior_session_id": prior_sid}, timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # get_session returns history keyed 'messages' (see server.py:370)
        msgs = body.get("messages") or body.get("history") or []
        assert isinstance(msgs, list)
        assert len(msgs) >= 2, "merged history should contain prior messages"

    def test_resume_cross_user_forbidden(self, api, mongo):
        """Create two independent sessions with different identity hashes."""
        # Session A: seed a manual session with fabricated emp_id_hash
        a_sid = str(uuid.uuid4())
        b_sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        mongo.sessions.insert_one({
            "_id": a_sid, "lifecycle": "expired",
            "updated_at_dt": now, "updated_at": now.isoformat(),
            "emp_id_hash": "AAAAAAAAAA" * 6,  # bogus but distinct
            "session_type": "visitor",
        })
        mongo.sessions.insert_one({
            "_id": b_sid, "lifecycle": "active",
            "updated_at_dt": now, "updated_at": now.isoformat(),
            "emp_id_hash": "BBBBBBBBBB" * 6,
            "session_type": "visitor",
        })
        mongo.conversations.insert_one({
            "session_id": a_sid,
            "messages": [{"role": "user", "content": "hi"},
                         {"role": "assistant", "content": "hello"}],
            "updated_at": now.isoformat(),
        })
        try:
            r = api.post(
                f"{BASE_URL}/api/sessions/{b_sid}/resume",
                json={"prior_session_id": a_sid}, timeout=20,
            )
            assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text}"
        finally:
            mongo.sessions.delete_many({"_id": {"$in": [a_sid, b_sid]}})
            mongo.conversations.delete_many({"session_id": {"$in": [a_sid, b_sid]}})


class TestDeclineResume:
    def test_decline_marks_priors_ended(self, api, mongo):
        # Set up a pair of sessions sharing emp_id_hash
        shared_hash = "C" * 64
        cur_sid = str(uuid.uuid4())
        prior_sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        mongo.sessions.insert_many([
            {"_id": cur_sid, "lifecycle": "active",
             "updated_at_dt": now, "updated_at": now.isoformat(),
             "emp_id_hash": shared_hash, "session_type": "visitor"},
            {"_id": prior_sid, "lifecycle": "expired",
             "updated_at_dt": now, "updated_at": now.isoformat(),
             "emp_id_hash": shared_hash, "session_type": "visitor"},
        ])
        try:
            r = api.post(f"{BASE_URL}/api/sessions/{cur_sid}/decline_resume", timeout=20)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["session_id"] == cur_sid
            assert body["ended_prior_sessions"] >= 1

            # Prior should now be lifecycle=ended
            doc = mongo.sessions.find_one({"_id": prior_sid}, {"lifecycle": 1})
            assert doc and doc["lifecycle"] == "ended"
        finally:
            mongo.sessions.delete_many({"_id": {"$in": [cur_sid, prior_sid]}})


class TestRateLimitingAndAuthStillWorks:
    """Smoke check — Phase 7 did not regress rate limit / auth flows."""

    def test_basic_turn_ok(self, api):
        sid = str(uuid.uuid4())
        r = _turn(api, sid, "Hello")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session_id"]
        assert isinstance(body["blocks"], list) and len(body["blocks"]) >= 1

    def test_employee_verify_round_trip(self, api, mongo):
        sid = str(uuid.uuid4())
        resp = _verify_employee(api, sid)
        sid = resp.get("session_id", sid)
        sess = mongo.sessions.find_one({"_id": sid})
        assert sess and sess.get("auth_state") == "verified"
