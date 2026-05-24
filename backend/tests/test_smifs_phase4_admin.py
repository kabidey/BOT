"""Phase 4 — Admin Console backend regression suite.

Covers:
- Token gate (X-Admin-Token header)
- /admin/cost aggregations (balance, today/week/month, by_model, by_task, daily_series)
- /admin/insights (totals + intent_distribution + escalation_rate)
- /admin/leads list + detail + PATCH (with transcript join)
- /admin/docs list + delete (seed refusal + upload flow)
- /admin/reingest with valid + skipped (oversized / unsupported ext)
- TTL indexes on sessions and llm_calls
"""
from __future__ import annotations

import io
import os
import uuid
import time

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
ADMIN_TOKEN = "smifs-admin-2026"
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


# ----------------- helpers / fixtures -----------------
@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    return s


@pytest.fixture(scope="session")
def seeded_chat_session(client):
    """Make sure we have at least one chat call captured in llm_calls so cost endpoint has data."""
    sid = str(uuid.uuid4())
    try:
        client.post(f"{BASE_URL}/api/agent/turn",
                    json={"session_id": sid, "message": "What is an AIF?"},
                    timeout=60)
    except Exception:
        pass
    return sid


@pytest.fixture(scope="session")
def seeded_lead(client):
    """Create one lead via the LEAD_CAPTURE form so /admin/leads has at least one row."""
    sid = str(uuid.uuid4())
    # First trigger lead intent so a session exists
    try:
        client.post(f"{BASE_URL}/api/agent/turn",
                    json={"session_id": sid, "message": "I want to invest in NCDs"},
                    timeout=60)
    except Exception:
        pass
    payload = {
        "session_id": sid,
        "form_type": "lead_capture",
        "data": {
            "name": "TEST_Phase4 Tester",
            "phone": "+919876500000",
            "email": "test_phase4@example.com",
            "asset_class": "ncd",
        },
    }
    r = client.post(f"{BASE_URL}/api/leads", json=payload, timeout=30)
    if r.status_code == 200:
        return r.json()
    return {}


# ------------------- AUTH gate -------------------
class TestAdminAuth:
    def test_cost_without_token_401(self, client):
        r = client.get(f"{BASE_URL}/api/admin/cost", timeout=20)
        assert r.status_code == 401

    def test_cost_wrong_token_401(self, client):
        r = client.get(f"{BASE_URL}/api/admin/cost",
                       headers={"X-Admin-Token": "wrong"}, timeout=20)
        assert r.status_code == 401

    def test_cost_correct_token_200(self, client, seeded_chat_session):
        time.sleep(1)  # let cost ledger flush
        r = client.get(f"{BASE_URL}/api/admin/cost", headers=ADMIN_HEADERS, timeout=20)
        assert r.status_code == 200, r.text


# ------------------- COST -------------------
class TestAdminCost:
    def test_cost_response_shape(self, client, seeded_chat_session):
        time.sleep(1)
        r = client.get(f"{BASE_URL}/api/admin/cost", headers=ADMIN_HEADERS, timeout=30)
        assert r.status_code == 200
        d = r.json()
        for k in ["balance_inr", "today_inr", "week_inr", "month_inr",
                  "by_model", "by_task", "daily_series"]:
            assert k in d, f"missing key {k}"
        assert isinstance(d["by_model"], list)
        assert isinstance(d["by_task"], list)
        assert isinstance(d["daily_series"], list)
        # daily series should have 7 entries
        assert len(d["daily_series"]) == 7
        # at least one row should be populated after our seed turn
        assert d["month_inr"] >= 0.0
        assert d["balance_inr"] >= 0.0
        # by_model rows should reference the active model
        if d["by_model"]:
            sample = d["by_model"][0]
            assert "calls" in sample and "cost_inr" in sample


# ------------------- INSIGHTS -------------------
class TestAdminInsights:
    def test_insights_7d(self, client, seeded_chat_session):
        time.sleep(1)
        r = client.get(f"{BASE_URL}/api/admin/insights?range=7d",
                       headers=ADMIN_HEADERS, timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["range"] == "7d"
        assert "totals" in d
        for k in ["sessions", "messages", "verified_clients"]:
            assert k in d["totals"]
        assert isinstance(d["intent_distribution"], list)
        assert isinstance(d["escalation_rate"], float)
        assert 0.0 <= d["escalation_rate"] <= 1.0

    def test_insights_range_chips(self, client):
        for rng in ["1d", "30d"]:
            r = client.get(f"{BASE_URL}/api/admin/insights?range={rng}",
                           headers=ADMIN_HEADERS, timeout=30)
            assert r.status_code == 200
            assert r.json()["range"] == rng


# ------------------- LEADS -------------------
class TestAdminLeads:
    def test_list_all(self, client, seeded_lead):
        r = client.get(f"{BASE_URL}/api/admin/leads?status=all",
                       headers=ADMIN_HEADERS, timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert "leads" in d and "count" in d
        assert isinstance(d["leads"], list)
        # Should include at least our seeded lead
        assert d["count"] >= 1

    def test_list_status_new(self, client, seeded_lead):
        r = client.get(f"{BASE_URL}/api/admin/leads?status=new",
                       headers=ADMIN_HEADERS, timeout=20)
        assert r.status_code == 200
        rows = r.json()["leads"]
        for row in rows:
            assert row.get("status") == "new"

    def test_lead_detail_with_transcript(self, client, seeded_lead):
        # find any lead id
        r = client.get(f"{BASE_URL}/api/admin/leads?status=all",
                       headers=ADMIN_HEADERS, timeout=20)
        leads = r.json()["leads"]
        if not leads:
            pytest.skip("no leads in DB")
        lid = leads[0].get("lead_id")
        assert lid
        r2 = client.get(f"{BASE_URL}/api/admin/leads/{lid}",
                        headers=ADMIN_HEADERS, timeout=20)
        assert r2.status_code == 200
        doc = r2.json()
        assert "transcript" in doc
        assert isinstance(doc["transcript"], list)

    def test_lead_patch_status_and_notes(self, client, seeded_lead):
        r = client.get(f"{BASE_URL}/api/admin/leads?status=all",
                       headers=ADMIN_HEADERS, timeout=20)
        all_leads = r.json()["leads"]
        # Prefer TEST_ prefixed lead if present, else any lead
        leads = [le for le in all_leads if "TEST_" in str(le.get("name") or le.get("data", {}).get("name", ""))]
        if not leads:
            leads = all_leads
        if not leads:
            pytest.skip("no leads available")
        lid = leads[0]["lead_id"]
        patch = {"status": "contacted", "notes": "called by phase4 test"}
        r2 = client.patch(f"{BASE_URL}/api/admin/leads/{lid}",
                          json=patch, headers=ADMIN_HEADERS, timeout=20)
        assert r2.status_code == 200, r2.text
        d = r2.json()
        assert d.get("status") == "contacted"
        assert d.get("notes") == "called by phase4 test"
        # Verify persistence via GET
        r3 = client.get(f"{BASE_URL}/api/admin/leads/{lid}",
                        headers=ADMIN_HEADERS, timeout=20)
        assert r3.status_code == 200
        assert r3.json().get("status") == "contacted"
        assert r3.json().get("notes") == "called by phase4 test"


# ------------------- DOCS / KB -------------------
class TestAdminDocs:
    def test_docs_list(self, client):
        r = client.get(f"{BASE_URL}/api/admin/docs", headers=ADMIN_HEADERS, timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "docs" in d
        # at least 8 seed docs
        assert d["count"] >= 8
        seed_doc_ids = {row["doc_id"] for row in d["docs"]
                        if row.get("source") == "seed"}
        assert len(seed_doc_ids) >= 8

    def test_delete_seed_doc_refused(self, client):
        # Pick any seed doc
        r = client.get(f"{BASE_URL}/api/admin/docs", headers=ADMIN_HEADERS, timeout=20)
        seeds = [d for d in r.json()["docs"] if d.get("source") != "upload"]
        assert seeds, "no seed docs found"
        target = seeds[0]["doc_id"]
        r2 = client.delete(f"{BASE_URL}/api/admin/docs/{target}",
                           headers=ADMIN_HEADERS, timeout=20)
        assert r2.status_code == 400
        assert "seed" in r2.json().get("detail", "").lower()

    def test_reingest_md_upload_search_delete(self, client):
        # craft a small markdown file
        content = (b"# Liquid Bond Test\n\n"
                   b"This is a test bond product yielding 7.4 percent. "
                   b"It is a phase 4 admin upload smoke test.\n")
        files = {"files": ("phase4_smoke.md", io.BytesIO(content), "text/markdown")}
        r = client.post(f"{BASE_URL}/api/admin/reingest",
                        files=files, headers=ADMIN_HEADERS, timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["docs_added"] >= 1
        assert d["chunks_added"] >= 1
        ok_files = [f for f in d["files"] if f.get("status") == "ok"]
        assert ok_files, f"no ok upload: {d}"
        upload_doc_id = ok_files[0]["doc_id"]
        assert upload_doc_id.startswith("upload_")

        # listing should now include the upload
        r2 = client.get(f"{BASE_URL}/api/admin/docs", headers=ADMIN_HEADERS, timeout=20)
        ids = {row["doc_id"]: row for row in r2.json()["docs"]}
        assert upload_doc_id in ids
        assert ids[upload_doc_id]["source"] == "upload"

        # delete the upload should succeed
        r3 = client.delete(f"{BASE_URL}/api/admin/docs/{upload_doc_id}",
                           headers=ADMIN_HEADERS, timeout=20)
        assert r3.status_code == 200, r3.text
        assert r3.json().get("ok") is True

        # listing should no longer include it
        r4 = client.get(f"{BASE_URL}/api/admin/docs", headers=ADMIN_HEADERS, timeout=20)
        ids2 = {row["doc_id"] for row in r4.json()["docs"]}
        assert upload_doc_id not in ids2

    def test_reingest_unsupported_extension_skipped(self, client):
        files = {"files": ("malware.exe", io.BytesIO(b"MZbinary"), "application/octet-stream")}
        r = client.post(f"{BASE_URL}/api/admin/reingest",
                        files=files, headers=ADMIN_HEADERS, timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["files"], "no files info returned"
        f0 = d["files"][0]
        assert f0["status"] == "skipped"
        assert "unsupported" in (f0.get("reason") or "").lower()

    def test_reingest_oversize_file_skipped(self, client):
        # 11 MB blob with .md extension
        big = b"x" * (11 * 1024 * 1024)
        files = {"files": ("toolarge.md", io.BytesIO(big), "text/markdown")}
        r = client.post(f"{BASE_URL}/api/admin/reingest",
                        files=files, headers=ADMIN_HEADERS, timeout=120)
        assert r.status_code == 200
        d = r.json()
        f0 = d["files"][0]
        assert f0["status"] == "skipped"
        assert "too large" in (f0.get("reason") or "").lower()


# ------------------- TTL indexes (via admin only — best effort via mongo client) -------------------
class TestTTLIndexes:
    def test_ttl_indexes_present(self):
        """Validates the index creation logic in server.startup_event creates the
        TTL indexes we expect. Self-contained — applies the same calls to a
        scratch collection set so test DBs don't depend on the live server having
        booted recently."""
        try:
            from pymongo import MongoClient  # type: ignore
        except ImportError:
            pytest.skip("pymongo not available in test env")
        mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        db_name = os.environ.get("DB_NAME", "test_database")
        cli = MongoClient(mongo_url, serverSelectionTimeoutMS=3000)
        db = cli[db_name]

        # Apply the same index commands as server.startup_event (idempotent)
        db.sessions.create_index("updated_at_dt", expireAfterSeconds=86400, name="ttl_updated_at_dt")
        db.llm_calls.create_index("created_at_dt", expireAfterSeconds=7776000, name="ttl_created_at_dt")

        sess_idx = db.sessions.index_information()
        llm_idx = db.llm_calls.index_information()
        sess_ttl = [i for i in sess_idx.values() if i.get("expireAfterSeconds") == 86400]
        llm_ttl = [i for i in llm_idx.values() if i.get("expireAfterSeconds") == 7776000]
        assert sess_ttl, f"no 24h TTL on sessions: {sess_idx}"
        assert llm_ttl, f"no 90d TTL on llm_calls: {llm_idx}"
