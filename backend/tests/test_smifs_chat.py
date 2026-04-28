"""Backend tests for SMIFS Lead Wealth-Engagement Agent (Phase 0)."""
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
