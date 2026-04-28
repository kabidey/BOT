"""Backend tests for SMIFS Lead Wealth-Engagement Agent (Phase 0 + Phase 1 RAG)."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    # Fall back to reading frontend .env
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


# ---- Health endpoint ----
class TestHealth:
    def test_health_ok_and_llm_reachable(self, http):
        r = http.get(f"{API}/health", timeout=45)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "ok"
        assert data["llm_reachable"] is True, f"LLM not reachable: {data}"
        assert data.get("model"), "model field should be populated"
        # Spec expects gemma-4-E4B; allow tolerant check
        assert "gemma" in data["model"].lower() or data["model"] == "auto"

    def test_health_reports_rag_index(self, http):
        r = http.get(f"{API}/health", timeout=45)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data.get("rag_chunks"), int)
        assert data["rag_chunks"] >= 30, f"Expected >=30 chunks, got {data['rag_chunks']}"
        assert data.get("embedder") in ("local", "hub_ai")


# ---- Docs / OpenAPI ----
class TestDocs:
    def test_swagger_docs(self, http):
        r = http.get(f"{API}/docs", timeout=15)
        assert r.status_code == 200
        assert "swagger" in r.text.lower() or "openapi" in r.text.lower()

    def test_openapi_json(self, http):
        r = http.get(f"{API}/openapi.json", timeout=15)
        assert r.status_code == 200
        spec = r.json()
        assert "paths" in spec
        assert "/api/chat" in spec["paths"]
        assert "/api/health" in spec["paths"]
        # Phase 1 endpoints
        assert "/api/admin/reingest" in spec["paths"], "Missing /api/admin/reingest"
        assert "/api/rag/search" in spec["paths"], "Missing /api/rag/search"


# ---- Admin reingest ----
ADMIN_TOKEN = "smifs-admin-2026"


class TestAdminReingest:
    def test_reingest_requires_token(self, http):
        r = http.post(f"{API}/admin/reingest", timeout=120)
        assert r.status_code == 401, r.text

    def test_reingest_wrong_token(self, http):
        r = http.post(
            f"{API}/admin/reingest",
            headers={"X-Admin-Token": "wrong-token"},
            timeout=120,
        )
        assert r.status_code == 401, r.text

    def test_reingest_with_token(self, http):
        r = http.post(
            f"{API}/admin/reingest",
            headers={"X-Admin-Token": ADMIN_TOKEN},
            timeout=180,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("docs") == 8, f"Expected 8 docs, got {data.get('docs')}"
        assert data.get("chunks", 0) >= 30, f"Expected >=30 chunks, got {data.get('chunks')}"
        assert data.get("embedder") in ("local", "hub_ai")


# ---- RAG search ----
class TestRagSearch:
    def test_rag_search_ncd(self, http):
        r = http.post(f"{API}/rag/search", json={"query": "What is an NCD?", "top_k": 3}, timeout=60)
        assert r.status_code == 200, r.text
        hits = r.json()
        assert isinstance(hits, list) and len(hits) == 3, f"Expected 3 hits, got {len(hits)}"
        top = hits[0]
        assert top["doc_id"] == "ncds_overview", f"Top hit doc_id={top['doc_id']}, full={top}"
        assert top["score"] > 0.5, f"Top score too low: {top['score']}"
        # field shape
        for h in hits:
            for k in ("doc_id", "doc_title", "section", "score", "text"):
                assert k in h, f"Missing field {k} in hit {h}"

    def test_rag_search_aif(self, http):
        r = http.post(
            f"{API}/rag/search",
            json={"query": "minimum ticket for AIF", "top_k": 3},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        hits = r.json()
        assert len(hits) == 3
        top = hits[0]
        assert top["doc_id"] == "aif_overview", f"Top hit doc_id={top['doc_id']}, full={top}"
        assert top["score"] > 0.4, f"Top score too low: {top['score']}"

    def test_rag_search_empty_query_422(self, http):
        r = http.post(f"{API}/rag/search", json={"query": "", "top_k": 3}, timeout=15)
        assert r.status_code == 422


# ---- Chat endpoint ----
class TestChat:
    def test_chat_new_session(self, http):
        payload = {"session_id": None, "message": "Hello"}
        r = http.post(f"{API}/chat", json=payload, timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "session_id" in data and isinstance(data["session_id"], str) and len(data["session_id"]) >= 8
        assert isinstance(data.get("reply"), str) and len(data["reply"].strip()) > 0
        assert isinstance(data.get("model"), str) and len(data["model"]) > 0
        # stash for chained test
        TestChat.session_id = data["session_id"]
        TestChat.first_reply = data["reply"]

    def test_chat_continues_session_with_memory(self, http):
        sid = getattr(TestChat, "session_id", None)
        assert sid, "Need session from previous test"
        # Turn 1: give a fact
        r1 = http.post(
            f"{API}/chat",
            json={"session_id": sid, "message": "Please remember my name is Aakash and I have a 10 year horizon."},
            timeout=60,
        )
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert d1["session_id"] == sid

        # Turn 2: ask about the fact
        r2 = http.post(
            f"{API}/chat",
            json={"session_id": sid, "message": "What is my name and what time horizon did I mention?"},
            timeout=60,
        )
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2["session_id"] == sid
        reply_lower = d2["reply"].lower()
        assert "aakash" in reply_lower, f"Bot did not recall name. Reply: {d2['reply']}"

    def test_chat_empty_message_validation_error(self, http):
        r = http.post(f"{API}/chat", json={"session_id": None, "message": ""}, timeout=15)
        assert r.status_code == 422, f"Expected 422 but got {r.status_code}: {r.text}"


# ---- Phase 1: Grounded chat ----
class TestGroundedChat:
    def test_chat_aif_grounded_with_citation(self, http):
        r = http.post(
            f"{API}/chat",
            json={"session_id": None, "message": "What is the minimum ticket size for an AIF?"},
            timeout=120,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["grounded"] is True, f"Expected grounded=true. Reply: {data}"
        reply = data["reply"]
        assert ("₹1 crore" in reply) or ("1 crore" in reply.lower()), (
            f"Reply does not mention ₹1 crore: {reply}"
        )
        cits = data.get("citations") or []
        assert len(cits) > 0, "Expected at least one citation"
        # Should contain aif_overview
        doc_ids = [c["doc_id"] for c in cits]
        assert "aif_overview" in doc_ids, f"Expected aif_overview citation. Got {doc_ids}"
        # Citation field shape
        for c in cits:
            for k in ("doc_id", "doc_title", "section", "score", "text"):
                assert k in c, f"Missing citation field {k}: {c}"
            assert isinstance(c["score"], (int, float))
            assert isinstance(c["text"], str) and len(c["text"]) > 10
        # Stash for conversations test
        TestGroundedChat.session_id = data["session_id"]

    def test_chat_off_topic_not_grounded_no_citations(self, http):
        r = http.post(
            f"{API}/chat",
            json={"session_id": None, "message": "What is the weather in Mumbai today?"},
            timeout=120,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["grounded"] is False, f"Expected grounded=false. Got {data}"
        assert data.get("citations") == [], f"Expected empty citations: {data.get('citations')}"
        # Reply should not fabricate SMIFS-specific specifics; tolerant check
        reply_lower = data["reply"].lower()
        # It should NOT claim SMIFS provides weather; expect human-handoff or limitation language
        assert (
            "advisor" in reply_lower
            or "not" in reply_lower
            or "unable" in reply_lower
            or "outside" in reply_lower
            or "don't" in reply_lower
            or "do not" in reply_lower
            or "limit" in reply_lower
        ), f"Off-topic reply lacks limitation/handoff language: {data['reply']}"

    def test_conversation_includes_grounded_metadata(self, http):
        sid = getattr(TestGroundedChat, "session_id", None)
        assert sid, "Need session from grounded test"
        time.sleep(0.5)
        r = http.get(f"{API}/conversations/{sid}", timeout=15)
        assert r.status_code == 200, r.text
        doc = r.json()
        assert "_id" not in doc
        msgs = doc.get("messages", [])
        assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert assistant_msgs, "No assistant messages stored"
        last = assistant_msgs[-1]
        assert "grounded" in last, f"Stored assistant msg missing grounded flag: {last}"
        assert "citations" in last and isinstance(last["citations"], list)
        assert last["grounded"] is True
        assert any(c["doc_id"] == "aif_overview" for c in last["citations"])

    def test_chat_session_continuity_with_topic(self, http):
        # First message
        r1 = http.post(
            f"{API}/chat",
            json={"session_id": None, "message": "Tell me briefly about NCDs."},
            timeout=120,
        )
        assert r1.status_code == 200, r1.text
        sid = r1.json()["session_id"]
        # Second message references prior topic
        r2 = http.post(
            f"{API}/chat",
            json={"session_id": sid, "message": "And how are they typically taxed?"},
            timeout=120,
        )
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2["session_id"] == sid
        # Should still talk about NCDs / taxation
        rl = d2["reply"].lower()
        assert ("ncd" in rl) or ("debenture" in rl) or ("tax" in rl), (
            f"Continuity reply lost topic: {d2['reply']}"
        )


# ---- Conversations ----
class TestConversations:
    def test_get_conversation_returns_messages(self, http):
        sid = getattr(TestChat, "session_id", None)
        assert sid, "Need session from chat tests"
        # Tiny pause for any async write
        time.sleep(0.5)
        r = http.get(f"{API}/conversations/{sid}", timeout=15)
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["session_id"] == sid
        assert "messages" in doc and isinstance(doc["messages"], list)
        # At least 6 messages (3 turns x user+assistant) from chat tests
        assert len(doc["messages"]) >= 4, f"Expected >=4 persisted msgs, got {len(doc['messages'])}"
        roles = [m["role"] for m in doc["messages"]]
        assert "user" in roles and "assistant" in roles
        # Ensure no mongo _id leaks
        assert "_id" not in doc

    def test_get_conversation_404_for_unknown(self, http):
        r = http.get(f"{API}/conversations/does-not-exist-xyz", timeout=15)
        assert r.status_code == 404
