"""Phase 12 — 20-row coverage matrix runner.

Tests routing + tool firing across:
- 10 employee questions (self + directory_* tools)
- 8 client questions (CLIENT_PROFILE + new client_* tools)
- 2 visitor questions (must still escalate for product-specifics)

Emits a markdown table + JSON snapshot.
"""
import asyncio, json, os, sys, re
from urllib.request import Request, urlopen
from urllib.parse import urlencode

API = os.environ.get("API_URL")
ASSERT_EMP = ("aaditya.jaiswal@smifs.com", "BQPPJ8323M")
ASSERT_CLI = ("63876", "ARIPP3602Q")

ROWS = [
    # role,         question                                                  expect_intent       expect_tool
    ("employee",    "What is my designation?",                                "KNOWLEDGE",        None),
    ("employee",    "Who do I report to and who do they report to?",          "DIRECTORY_QUERY",  "directory_my_reporting_chain"),
    ("employee",    "Show me my direct reports.",                             "DIRECTORY_QUERY",  "directory_my_team"),
    ("employee",    "List all compliance department members.",                "DIRECTORY_QUERY",  "directory_search_employees"),
    ("employee",    "Tell me about Awanish Chandra.",                         "DIRECTORY_QUERY",  "directory_lookup_employee"),
    ("employee",    "How many departments do we have?",                       "DIRECTORY_QUERY",  "directory_departments"),
    ("employee",    "List all SMIFS office locations.",                       "DIRECTORY_QUERY",  "directory_locations"),
    ("employee",    "Who joined SMIFS in the last 30 days?",                  "DIRECTORY_QUERY",  "directory_recent_joins"),
    ("employee",    "What's my HRBP's name?",                                 "KNOWLEDGE",        None),
    ("employee",    "What's the minimum ticket for Mackertich ONE PMS?",      "KNOWLEDGE",        None),
    # ---- Clients ----
    ("client",      "What's my risk profile?",                                "KNOWLEDGE",        None),
    ("client",      "Who is my relationship manager?",                        "KNOWLEDGE",        None),
    ("client",      "Show my equity portfolio holdings.",                     "CLIENT_QUERY",     "client_portfolio"),
    ("client",      "What's my account ledger balance?",                      "CLIENT_QUERY",     "client_ledger_balance"),
    ("client",      "Show me my recent trades.",                              "CLIENT_QUERY",     "client_recent_trades"),
    ("client",      "When did I deposit money into my account?",              "CLIENT_QUERY",     "client_deposits_withdrawals"),
    ("client",      "Show me my mutual fund holdings.",                       "CLIENT_QUERY",     "client_mf_holdings"),
    ("client",      "What's the minimum ticket for Mackertich ONE PMS?",      "ESCALATION",       None),
    # ---- Visitors ----
    ("visitor",     "What is an AIF?",                                        "KNOWLEDGE",        None),
    ("visitor",     "What is the minimum ticket for Mackertich ONE PMS?",     "CALLBACK_REQUEST", None),
]


UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 phase12-coverage"


def post(path, body):
    req = Request(f"{API}{path}", data=json.dumps(body).encode(),
                  headers={"Content-Type": "application/json",
                           "Origin": API,
                           "Referer": API + "/",
                           "User-Agent": UA},
                  method="POST")
    with urlopen(req, timeout=45) as r:
        return json.loads(r.read())


def get(path):
    req = Request(f"{API}{path}", headers={"User-Agent": UA})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def verify_employee(sid):
    post(f"/api/sessions/{sid}/select_role", {"role": "employee"})
    post("/api/agent/turn", {"session_id": sid, "message": ASSERT_EMP[0]})
    post("/api/agent/turn", {"session_id": sid, "message": ASSERT_EMP[1]})


def verify_client(sid):
    post(f"/api/sessions/{sid}/select_role", {"role": "client"})
    post("/api/agent/turn", {"session_id": sid, "message": ASSERT_CLI[0]})
    post("/api/agent/turn", {"session_id": sid, "message": ASSERT_CLI[1]})


def visitor(sid):
    post(f"/api/sessions/{sid}/select_role", {"role": "visitor"})


def main():
    import time
    sids = {}
    # Create + verify ONE session per role, then re-use it for all rows of that role.
    sids["employee"] = f"p12-emp-{int(time.time())}"
    verify_employee(sids["employee"])
    sids["client"] = f"p12-cli-{int(time.time())}"
    verify_client(sids["client"])
    sids["visitor"] = f"p12-vis-{int(time.time())}"
    visitor(sids["visitor"])

    out_rows = []
    print("| # | Role     | Question | Got intent | Got tool | Block types | Reply len | OK |")
    print("|---|----------|----------|------------|----------|-------------|-----------|----|")
    pass_count = 0
    for i, (role, q, expect_intent, expect_tool) in enumerate(ROWS):
        sid = sids[role]
        try:
            d = post("/api/agent/turn", {"session_id": sid, "message": q})
        except Exception as e:
            print(f"| {i+1} | {role:8} | {q[:50]:50} | ERR | {e} | | | ✗ |")
            continue
        intent = d.get("intent")
        trace = d.get("trace") or []
        tool_name = None
        for s in trace:
            if s.get("tool_name"):
                tool_name = s["tool_name"]; break
        blocks = [b.get("type") for b in (d.get("blocks") or [])]
        text = "".join(b.get("text", "") for b in (d.get("blocks") or []) if b.get("type") == "text")
        ok_intent = (intent == expect_intent) or (expect_intent in (intent or "") if expect_intent else False)
        ok_tool = (not expect_tool) or (tool_name == expect_tool)
        ok = ok_intent and ok_tool
        if ok: pass_count += 1
        ok_mark = "✓" if ok else "✗"
        out_rows.append({"role": role, "q": q, "expect_intent": expect_intent, "expect_tool": expect_tool,
                         "got_intent": intent, "got_tool": tool_name, "blocks": blocks, "len": len(text), "ok": ok})
        print(f"| {i+1} | {role:8} | {q[:50]:50} | {intent or '-':18} | {tool_name or '-':28} | {blocks} | {len(text)} | {ok_mark} |")

    print()
    print(f"PASS: {pass_count}/{len(ROWS)}")
    with open("/app/deliverables/phase12/coverage_matrix.json", "w") as f:
        json.dump(out_rows, f, indent=2, default=str)


main()
