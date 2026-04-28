"""Backend tests for SMIFS Phase 2 — Multi-agent orchestrator (router + specialists + leads + SSE)."""
import json
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
ADMIN_TOKEN = "smifs-admin-2026"


@pytest.fixture(scope="session")
def http():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _post_turn(http, message, session_id=None, timeout=120):
    payload = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    return http.post(f"{API}/agent/turn", json=payload, timeout=timeout)


# ---- Phase 2 Health: gpt-4o-mini and last_chat_model after first chat ----
class TestHealthPhase2:
    def test_health_model_is_gpt_4o_mini(self, http):
        r = http.get(f"{API}/health", timeout=45)
        assert r.status_code == 200
        data = r.json()
        assert data["llm_reachable"] is True
        m = (data.get("model") or "").lower()
        assert "gpt-4o-mini" in m, f"Active model expected gpt-4o-mini, got {data.get('model')}"
        assert isinstance(data.get("rag_chunks"), int) and data["rag_chunks"] >= 30
        assert data.get("embedder") in ("local", "hub_ai")

    def test_health_last_chat_model_after_turn(self, http):
        # Trigger a small_talk turn first to ensure last_chat_model is populated
        r0 = _post_turn(http, "Hello")
        assert r0.status_code == 200
        time.sleep(0.3)
        r = http.get(f"{API}/health", timeout=15)
        data = r.json()
        assert data.get("last_chat_model"), f"last_chat_model not populated: {data}"


# ---- /api/agent/turn — intent branches ----
class TestAgentTurnIntents:
    def test_knowledge_aif(self, http):
        r = _post_turn(http, "What is an AIF?")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["intent"] == "KNOWLEDGE", f"intent={d.get('intent')}, full={d}"
        blocks = d["blocks"]
        types = [b["type"] for b in blocks]
        assert "text" in types
        cits = d.get("citations") or []
        doc_ids = [c["doc_id"] for c in cits]
        assert "aif_overview" in doc_ids, f"Expected aif_overview in citations, got {doc_ids}"
        text_block = next(b for b in blocks if b["type"] == "text")
        body = (text_block.get("text") or text_block.get("body") or "").lower()
        assert "aif" in body or "sebi" in body, f"Text does not mention AIF/SEBI: {body[:200]}"

    def test_lead_capture_ncd_normalization(self, http):
        r = _post_turn(http, "I am interested in investing in NCDs")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["intent"] == "LEAD_CAPTURE", f"intent={d['intent']}"
        types = [b["type"] for b in d["blocks"]]
        assert "text" in types and "form" in types, f"Got types={types}"
        form_block = next(b for b in d["blocks"] if b["type"] == "form")
        schema = form_block.get("schema") or form_block.get("data") or form_block
        # Be flexible about nesting
        form_type = schema.get("form_type") or form_block.get("form_type")
        assert form_type == "lead_capture", f"form_type={form_type}, block={form_block}"
        ctx = (schema.get("context") or form_block.get("context") or {})
        ac = ctx.get("asset_class")
        assert ac == "NCD", f"asset_class normalization failed: {ac}, block={form_block}"

    def test_market_data_reliance(self, http):
        r = _post_turn(http, "What is the price of RELIANCE?")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["intent"] == "MARKET_DATA", f"intent={d['intent']}"
        types = [b["type"] for b in d["blocks"]]
        assert "market_card" in types
        mc = next(b for b in d["blocks"] if b["type"] == "market_card")
        data = mc.get("data") or mc
        assert data.get("symbol") == "RELIANCE", f"symbol={data.get('symbol')}"
        # last_price ~ 2842.55 — allow tolerance
        lp = data.get("last_price")
        assert lp is not None, f"missing last_price: {data}"
        assert abs(float(lp) - 2842.55) < 5.0, f"last_price unexpected: {lp}"
        cp = data.get("change_pct")
        assert cp is not None
        assert abs(float(cp) - 1.24) < 0.5, f"change_pct unexpected: {cp}"

    def test_callback_request(self, http):
        r = _post_turn(http, "Please call me back tomorrow morning")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["intent"] == "CALLBACK_REQUEST", f"intent={d['intent']}"
        types = [b["type"] for b in d["blocks"]]
        assert "text" in types and "form" in types
        form_block = next(b for b in d["blocks"] if b["type"] == "form")
        schema = form_block.get("schema") or form_block
        form_type = schema.get("form_type") or form_block.get("form_type")
        assert form_type == "callback", f"form_type={form_type}"

    def test_client_lookup_without_code(self, http):
        r = _post_turn(http, "I want to know my portfolio status")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["intent"] == "CLIENT_LOOKUP", f"intent={d['intent']}"
        types = [b["type"] for b in d["blocks"]]
        assert "text" in types
        assert "client_card" not in types, f"Should not have client_card without code, got {types}"

    def test_client_lookup_with_code(self, http):
        r = _post_turn(http, "My client code is SMIFS001")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["intent"] == "CLIENT_LOOKUP", f"intent={d['intent']}"
        types = [b["type"] for b in d["blocks"]]
        assert "client_card" in types, f"Expected client_card, got {types}"
        cc = next(b for b in d["blocks"] if b["type"] == "client_card")
        data = cc.get("data") or cc
        assert data.get("code") == "SMIFS001", f"code={data.get('code')}"
        assert data.get("name") == "Aarav Mehta", f"name={data.get('name')}"
        assert data.get("verified") is False, f"verified should be False (Phase 3 gating): {data}"

    def test_escalation(self, http):
        r = _post_turn(
            http, "This is unacceptable, I need to speak to your manager"
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["intent"] == "ESCALATION", f"intent={d['intent']}"
        types = [b["type"] for b in d["blocks"]]
        assert "text" in types and "escalation_card" in types, f"Got {types}"

    def test_small_talk(self, http):
        r = _post_turn(http, "Hello")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["intent"] == "SMALL_TALK", f"intent={d['intent']}"
        types = [b["type"] for b in d["blocks"]]
        assert types == ["text"], f"Expected only text block, got {types}"

    def test_off_topic_no_fabrication(self, http):
        r = _post_turn(http, "What is the weather in Mumbai today?")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["intent"] in ("KNOWLEDGE", "ESCALATION"), f"intent={d['intent']}"
        if d["intent"] == "KNOWLEDGE":
            # Should be ungrounded with no citations
            cits = d.get("citations") or []
            assert cits == [], f"Off-topic should have no citations, got {cits}"
            # Look for grounded flag if present
            for b in d["blocks"]:
                if b["type"] == "text":
                    g = b.get("grounded")
                    if g is not None:
                        assert g is False, f"text.grounded should be False, got {g}"

    def test_empty_message_422(self, http):
        r = http.post(f"{API}/agent/turn", json={"message": ""}, timeout=15)
        assert r.status_code == 422, f"Expected 422 got {r.status_code}: {r.text}"


# ---- /api/agent/turn/stream — SSE ----
class TestAgentTurnStream:
    def test_stream_status_then_result(self, http):
        url = f"{API}/agent/turn/stream"
        with requests.post(
            url,
            json={"message": "What is an AIF?"},
            stream=True,
            timeout=120,
            headers={"Content-Type": "application/json"},
        ) as resp:
            assert resp.status_code == 200, resp.text
            ct = resp.headers.get("Content-Type", "")
            assert "text/event-stream" in ct, f"unexpected content-type: {ct}"

            events = []  # (event_name, data_dict)
            current_event = None
            current_data_lines = []

            for raw_line in resp.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line = raw_line
                if line == "":
                    # dispatch
                    if current_event and current_data_lines:
                        data_str = "\n".join(current_data_lines)
                        try:
                            data = json.loads(data_str)
                        except Exception:
                            data = {"_raw": data_str}
                        events.append((current_event, data))
                    current_event = None
                    current_data_lines = []
                    if any(e[0] == "result" for e in events):
                        break
                    continue
                if line.startswith(":"):
                    # heartbeat comment
                    continue
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    current_data_lines.append(line.split(":", 1)[1].lstrip())

            status_events = [e for e in events if e[0] == "status"]
            result_events = [e for e in events if e[0] == "result"]
            assert len(status_events) >= 1, f"no status events; events={events}"
            assert len(result_events) == 1, f"expected one result; events={events}"

            for _, sd in status_events:
                assert "step" in sd and "label" in sd, f"status missing fields: {sd}"

            result = result_events[0][1]
            for k in ("session_id", "blocks", "citations", "model", "intent"):
                assert k in result, f"result missing {k}: keys={list(result.keys())}"
            assert result["intent"] == "KNOWLEDGE"


# ---- /api/leads ----
class TestLeads:
    def test_leads_lead_capture_persists(self, http):
        unique = uuid.uuid4().hex[:6]
        payload = {
            "form_type": "lead_capture",
            "fields": {
                "name": f"TEST_User_{unique}",
                "email": f"test_{unique}@example.com",
                "phone": "+91-9000000000",
                "investment_range": "₹50L–₹2Cr",
            },
            "context": {"asset_class": "NCD"},
        }
        r = http.post(f"{API}/leads", json=payload, timeout=30)
        assert r.status_code in (200, 201), r.text
        data = r.json()
        assert "lead_id" in data and isinstance(data["lead_id"], str) and len(data["lead_id"]) > 0
        assert "message" in data

        # Verify persistence via direct mongo
        try:
            from pymongo import MongoClient
            mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
            db_name = os.environ.get("DB_NAME", "test_database")
            client = MongoClient(mongo_url, serverSelectionTimeoutMS=2000)
            doc = client[db_name]["leads"].find_one({"lead_id": data["lead_id"]})
            if doc is None:
                # Fallback: search by email
                doc = client[db_name]["leads"].find_one({"fields.email": payload["fields"]["email"]})
            assert doc is not None, "Lead not persisted in mongo 'leads' collection"
            assert doc.get("status") == "new", f"status={doc.get('status')}"
            assert doc.get("form_type") == "lead_capture"
        except ImportError:
            pytest.skip("pymongo not available for direct DB check")

    def test_leads_callback_persists(self, http):
        unique = uuid.uuid4().hex[:6]
        payload = {
            "form_type": "callback",
            "fields": {
                "name": f"TEST_CB_{unique}",
                "phone": "+91-9000000001",
                "preferred_time": "Tomorrow morning",
                "topic": "NCD investment",
            },
            "context": {},
        }
        r = http.post(f"{API}/leads", json=payload, timeout=30)
        assert r.status_code in (200, 201), r.text
        data = r.json()
        assert "lead_id" in data

    def test_leads_unknown_form_type_400(self, http):
        payload = {
            "form_type": "totally_invalid_type",
            "fields": {"foo": "bar"},
            "context": {},
        }
        r = http.post(f"{API}/leads", json=payload, timeout=15)
        assert r.status_code == 400, f"Expected 400 got {r.status_code}: {r.text}"


# ---- Backward compat: /api/chat legacy shape ----
class TestChatLegacyCompat:
    def test_chat_legacy_shape(self, http):
        r = http.post(
            f"{API}/chat",
            json={"session_id": None, "message": "What is an AIF?"},
            timeout=120,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # Required legacy fields
        for k in ("session_id", "reply", "model", "grounded", "citations"):
            assert k in data, f"Legacy /api/chat missing {k}, keys={list(data.keys())}"
        # Should NOT include 'blocks' (Phase 2 only on /api/agent/turn)
        assert "blocks" not in data, f"Legacy /api/chat should not return 'blocks' field: {list(data.keys())}"
        reply_lower = data["reply"].lower()
        assert "aif" in reply_lower or "alternative investment" in reply_lower, (
            f"AIF answer text not found in reply: {data['reply'][:200]}"
        )


# ---- Conversations metadata for Phase 2 (intent + blocks + citations) ----
class TestConversationsPhase2:
    def test_conversation_stores_intent_blocks_citations(self, http):
        r = _post_turn(http, "What is an AIF?")
        assert r.status_code == 200
        sid = r.json()["session_id"]
        time.sleep(0.5)
        rc = http.get(f"{API}/conversations/{sid}", timeout=15)
        assert rc.status_code == 200
        doc = rc.json()
        assert "_id" not in doc
        msgs = doc.get("messages", [])
        assistant = [m for m in msgs if m["role"] == "assistant"]
        assert assistant, "no assistant messages persisted"
        last = assistant[-1]
        assert last.get("intent") == "KNOWLEDGE", f"intent missing/incorrect: {last.get('intent')}"
        assert isinstance(last.get("blocks"), list) and len(last["blocks"]) >= 1
        assert isinstance(last.get("citations"), list) and len(last["citations"]) >= 1


# ---- Phase 1 endpoints unchanged ----
class TestPhase1Unchanged:
    def test_rag_search_still_works(self, http):
        r = http.post(f"{API}/rag/search", json={"query": "What is an NCD?", "top_k": 3}, timeout=60)
        assert r.status_code == 200
        hits = r.json()
        assert isinstance(hits, list) and len(hits) == 3
        assert hits[0]["doc_id"] == "ncds_overview"

    def test_admin_reingest_token_works(self, http):
        r = http.post(
            f"{API}/admin/reingest", headers={"X-Admin-Token": ADMIN_TOKEN}, timeout=180
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("docs") == 8

    def test_docs_200(self, http):
        r = http.get(f"{API}/docs", timeout=15)
        assert r.status_code == 200
