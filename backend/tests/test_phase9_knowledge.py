"""Phase 9 — SMIFS Knowledge API integration regression tests.

Covers:
- GET  /api/admin/knowledge/status              (api_reachable, counts, last_sync, hallucination_events_7d)
- POST /api/admin/knowledge/sync (delta, dry_run=True / False; idempotent re-run)
- GET  /api/admin/rag/debug                     (product-topic gating + source weighting + smifs_top)
- POST /api/agent/turn                          (verified employee — Mackertich ONE → citations with source=smifs_knowledge + is_official)
- Hallucination refusal on invented product     ("Mackertich ONE Sapphire AIF Category IV")
- GET  /api/admin/knowledge/hallucination_events (accumulates refused / unchecked_claim events)
- Cross-role visitor product gating             (no upload/archive sources)
- Source-weight ordering                        (top result for 'AIF' is smifs_knowledge)

Admin token: X-Admin-Token: smifs-admin-2026
Employee:    aaditya.jaiswal@smifs.com / PAN BQPPJ8323M
"""
from __future__ import annotations
import os
import re
import uuid
import time

import pytest
import requests

try:
    from dotenv import dotenv_values
    _fe = dotenv_values("/app/frontend/.env")
    BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _fe.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
    _be = dotenv_values("/app/backend/.env")
    ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or _be.get("ADMIN_TOKEN") or "smifs-admin-2026"
except Exception:
    BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
    ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or "smifs-admin-2026"

assert BASE_URL, "REACT_APP_BACKEND_URL not configured"

EMP_EMAIL = "aaditya.jaiswal@smifs.com"
EMP_PAN = "BQPPJ8323M"
TURN_TIMEOUT = 180
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


# ------------------------- helpers -------------------------
def _turn(sid: str, text: str, timeout: int = TURN_TIMEOUT):
    return requests.post(
        f"{BASE_URL}/api/agent/turn",
        json={"session_id": sid, "message": text},
        timeout=timeout,
    )


def _text_blocks_concat(j) -> str:
    out = []
    for b in (j.get("blocks") or []):
        if b.get("type") == "text":
            out.append(b.get("text") or b.get("content") or "")
    return "\n".join(out)


def _citations(j):
    cits = []
    for b in (j.get("blocks") or []):
        if b.get("type") == "text":
            for c in (b.get("citations") or []):
                cits.append(c)
        # some impls may attach at top-level
    top = j.get("citations")
    if isinstance(top, list):
        cits.extend(top)
    return cits


def _verify_employee(sid: str):
    r1 = _turn(sid, "I am an SMIFS employee, I want to verify my identity.")
    assert r1.status_code == 200, r1.text
    r2 = _turn(sid, EMP_EMAIL)
    assert r2.status_code == 200, r2.text
    r3 = _turn(sid, EMP_PAN)
    assert r3.status_code == 200, r3.text
    return r3.json()


# ------------------------- fixtures -------------------------
@pytest.fixture(scope="module")
def verified_sid():
    sid = f"test-ph9-emp-{uuid.uuid4().hex[:8]}"
    out = _verify_employee(sid)
    btypes = [b.get("type") for b in (out.get("blocks") or [])]
    verified_ok = (
        "employee_card" in btypes
        or any(isinstance(s, dict) and s.get("to") == "verified" for s in (out.get("trace") or []))
    )
    if not verified_ok:
        pytest.skip(f"Employee verification did not succeed; blocks={btypes}")
    return sid


@pytest.fixture(scope="module")
def visitor_sid():
    return f"test-ph9-visitor-{uuid.uuid4().hex[:8]}"


# ============================================================
# 1. /admin/knowledge/status
# ============================================================
class TestKnowledgeStatus:
    def test_status_api_reachable_and_counts(self):
        r = requests.get(f"{BASE_URL}/api/admin/knowledge/status", headers=ADMIN_HEADERS, timeout=60)
        assert r.status_code == 200, r.text
        j = r.json()

        assert j.get("api_reachable") is True, f"api_reachable false: {j}"
        assert j.get("api_configured") is True

        total_smifs = j.get("total_smifs_chunks", 0)
        assert total_smifs >= 1800, f"expected >=1800 smifs chunks, got {total_smifs}"

        cbs = j.get("counts_by_source") or {}
        assert "smifs_knowledge" in cbs, f"missing smifs_knowledge in counts_by_source: {cbs}"
        assert cbs["smifs_knowledge"] >= 1800

        last_sync = j.get("last_sync") or {}
        assert last_sync, "last_sync object missing"
        ts = last_sync.get("last_sync_at") or last_sync.get("at") or ""
        # Basic ISO-8601 sanity
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts or ""), f"bad ISO ts: {ts}"

        # hallucination_events_7d must be present and an int
        hc = j.get("hallucination_events_7d")
        assert isinstance(hc, int), f"hallucination_events_7d should be int, got {hc!r}"

    def test_status_requires_admin_token(self):
        r = requests.get(f"{BASE_URL}/api/admin/knowledge/status", timeout=30)
        assert r.status_code == 401


# ============================================================
# 2. /admin/knowledge/sync — delta dry_run + real delta idempotency
# ============================================================
class TestKnowledgeSync:
    def test_sync_delta_dry_run_no_writes(self):
        r = requests.post(
            f"{BASE_URL}/api/admin/knowledge/sync",
            headers=ADMIN_HEADERS,
            json={"mode": "delta", "dry_run": True},
            timeout=300,
        )
        assert r.status_code == 200, r.text
        j = r.json()
        for k in ("fetched", "upserted", "skipped", "removed"):
            assert k in j, f"missing '{k}' in dry_run result: {j}"
            assert isinstance(j[k], int)
        # preview is optional but when present must be list of <=5
        if "preview" in j:
            assert isinstance(j["preview"], list)
            assert len(j["preview"]) <= 5
        assert j["fetched"] >= 1, "dry_run fetched 0 chunks from live API"

    def test_sync_delta_real_then_idempotent(self):
        # first real delta
        r1 = requests.post(
            f"{BASE_URL}/api/admin/knowledge/sync",
            headers=ADMIN_HEADERS,
            json={"mode": "delta", "dry_run": False},
            timeout=600,
        )
        assert r1.status_code == 200, r1.text
        j1 = r1.json()
        for k in ("fetched", "upserted", "skipped", "removed"):
            assert k in j1
        f1 = j1["fetched"]
        assert f1 >= 1

        # immediate re-run should be (near-)idempotent: skipped ~= fetched
        r2 = requests.post(
            f"{BASE_URL}/api/admin/knowledge/sync",
            headers=ADMIN_HEADERS,
            json={"mode": "delta", "dry_run": False},
            timeout=600,
        )
        assert r2.status_code == 200, r2.text
        j2 = r2.json()
        f2, s2, u2 = j2["fetched"], j2["skipped"], j2["upserted"]
        # Allow tiny live-API drift: upserted should be a small fraction of fetched.
        assert f2 >= 1
        assert s2 >= int(0.9 * f2), (
            f"expected skipped>=90% of fetched on idempotent re-run, got fetched={f2} skipped={s2} upserted={u2}"
        )


# ============================================================
# 3. /admin/rag/debug — product-topic gating + source weighting
# ============================================================
class TestRagDebug:
    def test_product_topic_gating_and_smifs_strong(self):
        q = "What is the minimum ticket size for an AIF?"
        r = requests.get(
            f"{BASE_URL}/api/admin/rag/debug",
            headers=ADMIN_HEADERS,
            params={"q": q},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get("is_product_topic") is True
        assert j.get("restrict_sources") == ["smifs_knowledge", "seed"]
        hits = j.get("hits") or []
        assert hits, "no hits returned"
        # Every hit must have a source populated
        for h in hits:
            assert h.get("source"), f"hit missing source: {h}"
            assert h["source"] in ("smifs_knowledge", "seed")
        # Analysis surfaces has_smifs_strong (at least key present)
        analysis = j.get("analysis") or {}
        assert "has_smifs_strong" in analysis, f"analysis missing has_smifs_strong: {analysis}"
        assert analysis["has_smifs_strong"] is True, f"expected has_smifs_strong=True, got {analysis}"

    def test_source_weight_ordering_aif_top_is_smifs(self):
        r = requests.get(
            f"{BASE_URL}/api/admin/rag/debug",
            headers=ADMIN_HEADERS,
            params={"q": "AIF"},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        j = r.json()
        hits = j.get("hits") or []
        assert hits, "no hits for 'AIF'"
        assert hits[0].get("source") == "smifs_knowledge", (
            f"top-ranked hit is not smifs_knowledge: source={hits[0].get('source')} hits={[(h.get('source'), h.get('score')) for h in hits[:3]]}"
        )


# ============================================================
# 4. /agent/turn — verified employee, real Mackertich ONE answers cite smifs_knowledge
# ============================================================
class TestAgentTurnKnowledgeCitations:
    @pytest.mark.parametrize("question", [
        "What is Mackertich ONE?",
        "What products does Mackertich ONE offer?",
    ])
    def test_verified_employee_gets_smifs_official_citations(self, verified_sid, question):
        r = _turn(verified_sid, question)
        assert r.status_code == 200, r.text
        j = r.json()
        cits = _citations(j)
        assert cits, f"no citations on reply for '{question}'; blocks={[b.get('type') for b in (j.get('blocks') or [])]}"
        smifs_cits = [c for c in cits if (c.get("source") == "smifs_knowledge")]
        assert smifs_cits, f"no smifs_knowledge citation for '{question}'; sources={[c.get('source') for c in cits]}"
        # at least one should be marked is_official=True (gold badge)
        assert any(bool(c.get("is_official")) for c in smifs_cits), (
            f"no is_official=True citation among smifs_knowledge citations: {smifs_cits}"
        )


# ============================================================
# 5. Hallucination refusal on invented product
# ============================================================
class TestHallucinationRefusal:
    def test_invented_product_refusal_no_specific_lockin(self, verified_sid):
        q = "What is the lock-in period for Mackertich ONE Sapphire AIF Category IV?"
        r = _turn(verified_sid, q)
        assert r.status_code == 200, r.text
        j = r.json()
        txt = _text_blocks_concat(j).lower()
        # Must NOT assert a specific lock-in duration (months/years) for the invented product.
        bad_patterns = [
            r"\block[-\s]?in\s+(?:period\s+)?(?:is|of)?\s*\d+\s*(?:year|yr|month|mo)",
            r"\b\d+\s*(?:year|month)s?\s+lock[-\s]?in",
        ]
        for pat in bad_patterns:
            assert not re.search(pat, txt), f"reply asserts concrete lock-in for invented product: matched /{pat}/ in:\n{txt[:500]}"

        # Should offer escalation / acknowledge missing info
        hinted = any(kw in txt for kw in [
            "no verified", "not verified", "don't have", "do not have",
            "cannot confirm", "can't confirm", "escalate", "connect you", "reach out",
            "not available", "unable to find", "no information",
            "doesn't cover", "does not cover", "don't cover", "provided context",
            "connecting with", "mackertich one advisor", "advisor", "recommend",
            "cannot provide", "can't provide", "no specific", "not specified",
        ])
        assert hinted, f"refusal/escalation not surfaced: {txt[:500]}"

    def test_hallucination_events_accumulates(self, verified_sid):
        # trigger another invented-product refusal to bump the counter
        _turn(verified_sid, "Give the IRR guarantee for Mackertich ONE Platinum AIF Category XII.")

        # poll up to 5s for async log_event to land
        deadline = time.time() + 5
        events = []
        while time.time() < deadline:
            r = requests.get(
                f"{BASE_URL}/api/admin/knowledge/hallucination_events",
                headers=ADMIN_HEADERS,
                params={"limit": 50},
                timeout=30,
            )
            assert r.status_code == 200, r.text
            events = (r.json() or {}).get("events") or []
            if events:
                break
            time.sleep(0.5)

        assert isinstance(events, list)
        # Should have at least one event overall (fresh deployments may be empty, but we
        # just triggered one above AND Mackertich refusal test runs first in the class).
        assert events, "hallucination_events collection is empty after refusal"

        actions = {e.get("action") for e in events if isinstance(e, dict)}
        # accept either refused or unchecked_claim in the recent window
        assert actions & {"refused", "unchecked_claim"}, (
            f"expected 'refused' or 'unchecked_claim' action among recent events; got {actions}"
        )


# ============================================================
# 6. Cross-role guard — visitor product query is SMIFS-gated (no upload/archive)
# ============================================================
class TestVisitorProductGating:
    def test_visitor_product_query_no_upload_or_archive_sources(self, visitor_sid):
        # First let the visitor session be born with small talk
        _turn(visitor_sid, "hi")
        r = _turn(visitor_sid, "What products does Mackertich ONE offer?")
        assert r.status_code == 200, r.text
        j = r.json()
        cits = _citations(j)
        # Not every visitor path must return citations (may refuse/escalate), but IF it
        # does, none should come from upload/archive sources on a product topic.
        for c in cits:
            src = (c.get("source") or "").lower()
            assert src not in ("upload", "archive"), f"visitor got gated source leak: {c}"
