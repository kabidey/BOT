"""Phase 22 — End-to-end integration tests for the fingerprint silent-block
middleware + admin Fraud Watch REST surface.

Runs against the live public backend at REACT_APP_BACKEND_URL. Each test uses
a unique synthetic fingerprint hash (`TEST_FP_<uuid>`) so concurrent test
runs don't collide with each other or with manual smoke-test rows.

Critical invariants verified here:
  * Silent block returns HTTP 200 with a soft-failure-shaped envelope
    (NEVER a 403, NEVER an `error` / `blocked` field).
  * /api/admin/* always bypasses the block (operator safety).
  * Trust clears `blocked=true`.
  * Audit trail, notes, and the silent-block security_event are persisted.
  * /api/chat without an X-Client-Fingerprint header is never enforced.
"""
from __future__ import annotations

import os
import uuid
import time

import pytest
import requests

# Read backend URL from frontend/.env if not present in process env.
def _load_backend_url() -> str:
    env_val = os.environ.get("REACT_APP_BACKEND_URL")
    if env_val:
        return env_val.rstrip("/")
    try:
        with open("/app/frontend/.env", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().strip('"').rstrip("/")
    except FileNotFoundError:
        pass
    raise RuntimeError("REACT_APP_BACKEND_URL not configured")


BASE_URL = _load_backend_url()
ADMIN_TOKEN = "smifs-admin-2026"
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


# ----------------------------- fixtures -----------------------------------

@pytest.fixture
def fp_hash():
    """Unique fingerprint hash per test, auto-unblocked on teardown."""
    h = f"TEST_FP_{uuid.uuid4().hex[:16]}"
    yield h
    # cleanup
    try:
        requests.post(
            f"{BASE_URL}/api/admin/fingerprint/{h}/unblock",
            headers=ADMIN_HEADERS,
            json={"reason": "test_teardown"},
            timeout=10,
        )
        requests.post(
            f"{BASE_URL}/api/admin/fingerprint/{h}/untrust",
            headers=ADMIN_HEADERS,
            json={"reason": "test_teardown"},
            timeout=10,
        )
    except Exception:
        pass


def _client_headers(fp: str):
    return {
        "X-Client-Fingerprint": fp,
        "X-Client-Tz": "Asia/Kolkata",
        "X-Client-Screen": "1920x1080",
    }


def _prime_fp(fp: str) -> None:
    """Make the device_fingerprints row exist (so admin block doesn't 404).
    Uses a fast GET endpoint (middleware records the FP regardless of the
    handler's 404 — record_request_signal runs before the block check)."""
    for _ in range(2):
        try:
            requests.get(
                f"{BASE_URL}/api/conversations/__prime_{uuid.uuid4().hex[:6]}",
                headers=_client_headers(fp), timeout=15,
            )
            return
        except requests.RequestException:
            time.sleep(0.5)


# ---------------------- admin REST surface --------------------------------

class TestFingerprintAdminBasics:
    """Admin Fraud Watch endpoints — summary / list shape."""

    def test_summary_returns_thresholds_and_counters(self):
        r = requests.get(f"{BASE_URL}/api/admin/fingerprint/summary",
                          headers=ADMIN_HEADERS, timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        # Should contain at least thresholds + counter keys.
        assert isinstance(data, dict)
        # The implementation must return both thresholds and per-bucket counts.
        as_str = str(data).lower()
        assert "block" in as_str or "flag" in as_str, (
            "expected threshold/counter fields in summary; got: " + str(data))

    def test_summary_requires_admin_token(self):
        r = requests.get(f"{BASE_URL}/api/admin/fingerprint/summary", timeout=10)
        assert r.status_code in (401, 403), r.text

    @pytest.mark.parametrize("status", ["active", "flagged", "blocked", "trusted"])
    def test_list_status_filters_return_arrays(self, status):
        r = requests.get(
            f"{BASE_URL}/api/admin/fingerprint/list",
            params={"status": status, "limit": 5},
            headers=ADMIN_HEADERS, timeout=10,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # Either a list or a dict with a list payload.
        items = data if isinstance(data, list) else (
            data.get("items") or data.get("rows") or data.get("results") or [])
        assert isinstance(items, list)


# ---------- middleware recording + admin list visibility ------------------

class TestFingerprintRecording:
    def test_chat_with_fp_header_lands_in_active_list(self, fp_hash):
        # Trigger middleware path via a lightweight endpoint (chat hits the
        # LLM and is slow). The middleware records the FP on ANY non-bypass
        # /api/* request, regardless of the handler outcome.
        _prime_fp(fp_hash)
        # FP should now exist in admin list (active bucket).
        time.sleep(0.5)
        r2 = requests.get(
            f"{BASE_URL}/api/admin/fingerprint/list",
            params={"status": "active", "limit": 200},
            headers=ADMIN_HEADERS, timeout=10,
        )
        assert r2.status_code == 200
        body = r2.json()
        items = body if isinstance(body, list) else (
            body.get("items") or body.get("rows") or body.get("results") or [])
        flat = str(items)
        assert fp_hash in flat, (
            f"expected {fp_hash} in active list; sample={items[:2]}")


# ---------------- silent-block invariants ---------------------------------

class TestSilentBlock:
    """Most critical contract: blocked FP receives a 200 soft-failure
    envelope, NEVER a 403 or error response."""

    def _block(self, fp):
        r = requests.post(
            f"{BASE_URL}/api/admin/fingerprint/{fp}/block",
            headers=ADMIN_HEADERS,
            json={"reason": "integration_test"},
            timeout=10,
        )
        assert r.status_code == 200, r.text

    def test_blocked_chat_returns_silent_soft_failure(self, fp_hash):
        # Prime the FP row via one normal request so block() finds the doc.
        _prime_fp(fp_hash)
        self._block(fp_hash)

        r = requests.post(
            f"{BASE_URL}/api/chat",
            headers=_client_headers(fp_hash),
            json={"message": "should be silently blocked"},
            timeout=30,
        )
        assert r.status_code == 200, f"silent-block must be 200, got {r.status_code}: {r.text}"
        data = r.json()
        # Shape: reply text + model=None — looks identical to a soft-error.
        assert data.get("model") is None, f"model must be null when silent-blocked: {data}"
        assert "unable to process" in (data.get("reply") or "").lower(), (
            f"silent-block reply should mention 'unable to process'; got={data}")
        # Leakage guard: no `blocked`/`error`/`forbidden` keys in payload.
        flat = str(data).lower()
        for leak in ("blocked", "forbidden", "\"error\""):
            assert leak not in flat, f"silent payload leaks '{leak}': {data}"

    def test_blocked_agent_turn_returns_soft_error_blocks(self, fp_hash):
        # Prime + block.
        _prime_fp(fp_hash)
        self._block(fp_hash)

        r = requests.post(
            f"{BASE_URL}/api/agent/turn",
            headers=_client_headers(fp_hash),
            json={"message": "any", "session_id": None},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("model") is None
        blocks = data.get("blocks") or []
        assert isinstance(blocks, list) and len(blocks) >= 1
        assert "unable to process" in (blocks[0].get("text") or "").lower(), data
        # intent surfaced on the envelope.
        assert (data.get("intent") or "").upper() == "SOFT_ERROR", (
            f"expected intent=SOFT_ERROR; got {data.get('intent')}")

    def test_admin_endpoints_bypass_the_block(self, fp_hash):
        """Admin must never lock themselves out. Hitting /api/admin/* with a
        blocked X-Client-Fingerprint must still return real admin data."""
        _prime_fp(fp_hash)
        self._block(fp_hash)

        # Use the blocked FP as a header on an admin call.
        hdrs = {**ADMIN_HEADERS, **_client_headers(fp_hash)}
        r = requests.get(f"{BASE_URL}/api/admin/fingerprint/summary",
                          headers=hdrs, timeout=10)
        assert r.status_code == 200, f"admin bypass failed: {r.status_code} {r.text}"
        # Summary still real (not a silent-block envelope).
        data = r.json()
        # silent_block_empty_data() = {"ok": True, "value": None, "results": [], "rows": []}
        if isinstance(data, dict) and set(data.keys()) == {"ok", "value", "results", "rows"}:
            pytest.fail(f"admin summary returned a silent-block payload! {data}")

    def test_silent_block_security_event_is_logged(self, fp_hash):
        _prime_fp(fp_hash)
        # Baseline count BEFORE the silent-blocked request.
        r0 = requests.get(
            f"{BASE_URL}/api/admin/security_events",
            params={"kind": "fingerprint_silent_block_served", "limit": 1},
            headers=ADMIN_HEADERS, timeout=10,
        )
        baseline_total = (r0.json() or {}).get("total", 0) if r0.status_code == 200 else 0
        self._block(fp_hash)
        # Trigger one silent-blocked chat.
        try:
            requests.post(f"{BASE_URL}/api/chat",
                           headers=_client_headers(fp_hash),
                           json={"message": "trigger"}, timeout=15)
        except requests.RequestException:
            pass
        time.sleep(0.7)
        r = requests.get(
            f"{BASE_URL}/api/admin/security_events",
            params={"kind": "fingerprint_silent_block_served", "limit": 5},
            headers=ADMIN_HEADERS, timeout=10,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        items = body.get("items") or []
        new_total = body.get("total", 0)
        # 1) Audit row must have been written.
        assert new_total > baseline_total, (
            f"silent-block event not appended: baseline={baseline_total}, now={new_total}")
        # 2) At least one returned row carries the correct kind.
        assert any((it.get("kind") == "fingerprint_silent_block_served")
                    for it in items), f"no row with the expected kind: {items[:3]}"
        # NOTE: The admin reader currently drops fingerprint_hash & path
        # (writes `user_message` but reads `user_message_excerpt`). See
        # critical_code_review_comments in the iteration report.


# ---------------- unblock / trust / note flow -----------------------------

class TestUnblockTrustNoteAudit:

    def _block(self, fp):
        _prime_fp(fp)
        r = requests.post(
            f"{BASE_URL}/api/admin/fingerprint/{fp}/block",
            headers=ADMIN_HEADERS,
            json={"reason": "integration_test"},
            timeout=10,
        )
        assert r.status_code == 200, r.text

    def test_unblock_restores_normal_chat(self, fp_hash):
        self._block(fp_hash)
        r = requests.post(
            f"{BASE_URL}/api/admin/fingerprint/{fp_hash}/unblock",
            headers=ADMIN_HEADERS,
            json={"reason": "integration_test_unblock"},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        # Now chat must return a real model name (non-null).
        r2 = requests.post(
            f"{BASE_URL}/api/chat",
            headers=_client_headers(fp_hash),
            json={"message": "hello"},
            timeout=120,
        )
        assert r2.status_code == 200
        data = r2.json()
        assert data.get("model"), (
            f"after unblock, chat should resume with a real model; got={data}")

    def test_trust_clears_blocked_flag_and_allows_chat(self, fp_hash):
        self._block(fp_hash)
        r = requests.post(
            f"{BASE_URL}/api/admin/fingerprint/{fp_hash}/trust",
            headers=ADMIN_HEADERS,
            json={"reason": "verified_partner"},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        # /fingerprint/{hash} should reflect both trusted=True AND blocked=False.
        r2 = requests.get(f"{BASE_URL}/api/admin/fingerprint/{fp_hash}",
                           headers=ADMIN_HEADERS, timeout=10)
        assert r2.status_code == 200, r2.text
        row = r2.json()
        # row may be wrapped under a key.
        doc = row.get("fingerprint") or row.get("row") or row
        assert doc.get("admin_trusted") is True, doc
        assert doc.get("blocked") in (False, None), f"trust must clear blocked: {doc}"
        # Chat works.
        r3 = requests.post(f"{BASE_URL}/api/chat",
                            headers=_client_headers(fp_hash),
                            json={"message": "hello"}, timeout=120)
        assert r3.status_code == 200
        assert r3.json().get("model"), r3.json()

    def test_note_is_appended_and_persisted(self, fp_hash):
        # Prime row exists.
        _prime_fp(fp_hash)
        marker = f"note_{uuid.uuid4().hex[:8]}"
        r = requests.post(
            f"{BASE_URL}/api/admin/fingerprint/{fp_hash}/note",
            headers=ADMIN_HEADERS,
            json={"note": marker},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        # Verify via detail.
        r2 = requests.get(f"{BASE_URL}/api/admin/fingerprint/{fp_hash}",
                           headers=ADMIN_HEADERS, timeout=10)
        assert r2.status_code == 200
        doc = r2.json()
        assert marker in str(doc), f"note {marker} not persisted; got={doc}"

    def test_detail_returns_audit_trail_after_block_unblock_trust(self, fp_hash):
        self._block(fp_hash)
        requests.post(f"{BASE_URL}/api/admin/fingerprint/{fp_hash}/unblock",
                       headers=ADMIN_HEADERS,
                       json={"reason": "test"}, timeout=10)
        requests.post(f"{BASE_URL}/api/admin/fingerprint/{fp_hash}/trust",
                       headers=ADMIN_HEADERS,
                       json={"reason": "test"}, timeout=10)
        r = requests.get(f"{BASE_URL}/api/admin/fingerprint/{fp_hash}",
                          headers=ADMIN_HEADERS, timeout=10)
        assert r.status_code == 200
        doc = r.json()
        audit = doc.get("audit") or (doc.get("fingerprint") or {}).get("audit") or []
        assert isinstance(audit, list) and len(audit) >= 3, (
            f"expected ≥3 audit entries (block/unblock/trust); got {audit}")
        kinds = {(a.get("kind") or "") for a in audit}
        assert "admin_block" in kinds and "admin_unblock" in kinds and "admin_trust" in kinds, kinds


# ----------------- no-fingerprint passthrough -----------------------------

class TestNoFingerprintHeaderPassthrough:
    def test_chat_without_fp_header_is_never_enforced(self):
        r = requests.post(
            f"{BASE_URL}/api/chat",
            headers={},  # no X-Client-Fingerprint at all
            json={"message": "hello no fp"},
            timeout=120,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # Real handler path = `model` populated.
        assert data.get("model"), (
            f"chat without FP must hit real handler with a real model; got={data}")


# ----------------------- regression — unrelated APIs ----------------------

class TestRegression:
    def test_health_returns_llm_reachable(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "llm_reachable" in data, data

    def test_admin_leads_ok(self):
        r = requests.get(f"{BASE_URL}/api/admin/leads",
                          headers=ADMIN_HEADERS, timeout=10)
        assert r.status_code == 200, r.text

    def test_admin_cost_ok(self):
        r = requests.get(f"{BASE_URL}/api/admin/cost",
                          headers=ADMIN_HEADERS, timeout=10)
        assert r.status_code == 200, r.text

    def test_admin_sales_ok(self):
        r = requests.get(f"{BASE_URL}/api/admin/sales",
                          headers=ADMIN_HEADERS, timeout=10)
        assert r.status_code == 200, r.text
