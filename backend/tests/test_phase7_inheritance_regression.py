"""Phase 7 RETEST — verifying the lifecycle hash-inheritance fix.

Confirms (post-fix in lifecycle.py:90-135):
1. After idle expiry, the newly-minted session row INHERITS identity hashes
   from the prior session (Mongo direct inspection).
2. /api/agent/turn top-level response now exposes `prior_session_id` and
   `resume_offer` fields after idle expiry (TurnResponse widened).
"""
import os
import uuid
from datetime import datetime, timezone, timedelta

import pymongo
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

EMP_EMAIL = "aaditya.jaiswal@smifs.com"
EMP_PAN = "BQPPJ8323M"

IDENTITY_HASH_FIELDS = ("emp_id_hash", "ucc_hash", "pan_hash", "email_hash", "phone_hash")


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


def _turn(api, sid, message):
    return api.post(f"{BASE_URL}/api/agent/turn",
                    json={"session_id": sid, "message": message}, timeout=60)


def _verify_employee(api, sid):
    r = _turn(api, sid, f"Please verify me. My email is {EMP_EMAIL}.")
    assert r.status_code == 200, r.text
    sid = r.json().get("session_id", sid)
    r = _turn(api, sid, f"My PAN is {EMP_PAN}")
    assert r.status_code == 200, r.text
    return r.json()


def _age(mongo_db, sid, minutes=3):
    past = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    mongo_db.sessions.update_one(
        {"_id": sid},
        {"$set": {"updated_at_dt": past, "updated_at": past.isoformat()}},
    )


# ---------------------------------------------------------------------------
class TestIdentityHashInheritance:
    """The new session must INHERIT prior emp_id_hash/email_hash/pan_hash/etc."""

    def test_new_session_inherits_identity_hashes_from_prior(self, api, mongo):
        sid = str(uuid.uuid4())
        resp = _verify_employee(api, sid)
        sid = resp.get("session_id", sid)

        prior = mongo.sessions.find_one({"_id": sid})
        assert prior, "verified session not persisted"
        prior_hashes = {f: prior.get(f) for f in IDENTITY_HASH_FIELDS if prior.get(f)}
        assert prior_hashes, "prior session should have at least one identity hash"

        # backdate to trigger idle expiry
        _age(mongo, sid, minutes=3)

        r = _turn(api, sid, "Are you still there?")
        assert r.status_code == 200, r.text
        body = r.json()
        new_sid = body["session_id"]
        assert new_sid != sid, "expected a newly-minted session id"

        # Direct Mongo inspection of the new session row
        new_sess = mongo.sessions.find_one({"_id": new_sid})
        assert new_sess, "new session row was not inserted"
        assert new_sess.get("lifecycle") == "active"
        assert new_sess.get("prior_session_id") == sid

        # Every identity hash present on prior must be present (and equal) on new
        for f, v in prior_hashes.items():
            assert new_sess.get(f) == v, (
                f"hash {f} not inherited: prior={v!r}, new={new_sess.get(f)!r}"
            )

        # And critically NO plaintext PII leaked into the new doc
        forbidden = {"pan", "pan_number", "aadhar", "aadhaar", "aadhar_no",
                     "email", "personal_mobile", "mobile_number", "phone"}
        leaked = forbidden & set(new_sess.keys())
        assert not leaked, f"plaintext PII leaked onto inherited row: {leaked}"

        # Stash for the next test in this module
        pytest._inh_prior = sid
        pytest._inh_new = new_sid


# ---------------------------------------------------------------------------
class TestTurnResponseTopLevelFields:
    """TurnResponse Pydantic model must now expose prior_session_id +
    resume_offer at the response root after idle expiry."""

    def test_turn_response_exposes_prior_and_resume_offer_at_root(self, api, mongo):
        sid = str(uuid.uuid4())
        resp = _verify_employee(api, sid)
        sid = resp.get("session_id", sid)
        # build a meaningful prior conversation
        _turn(api, sid, "What mutual funds do you offer?")
        _age(mongo, sid, minutes=3)

        r = _turn(api, sid, "Hello again")
        assert r.status_code == 200, r.text
        body = r.json()

        # widened TurnResponse must surface these
        assert "prior_session_id" in body, f"prior_session_id missing from root: {list(body.keys())}"
        assert body["prior_session_id"] == sid, body.get("prior_session_id")

        assert "resume_offer" in body, f"resume_offer missing from root: {list(body.keys())}"
        offer = body.get("resume_offer")
        assert isinstance(offer, list) and offer, "resume_offer should be a non-empty list"
        # each candidate carries the documented contract
        for c in offer:
            for k in ("prior_session_id", "summary", "message_count", "ended_at", "session_type"):
                assert k in c, f"candidate missing '{k}': {c}"
        # and the just-expired sid should be in there
        pids = [c["prior_session_id"] for c in offer]
        assert sid in pids, f"prior {sid} missing from offers {pids}"
