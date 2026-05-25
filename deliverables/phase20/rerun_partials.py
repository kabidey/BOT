#!/usr/bin/env python3
"""Re-run the PARTIAL rows from the last matrix run to see if the
prompt tightening moved any of them up to PASS. Patches the canonical
matrix_results.json + matrix_results.md in-place with new scores."""
import asyncio, json, os, sys, time, uuid
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
import httpx
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/deliverables/phase20")
from run_matrix import (QUESTIONS, EMPLOYEE_IDENTITY, CLIENT_IDENTITY,
                        _bootstrap_session, _score, _extract_trace_bits)

API_BASE = os.environ.get("PREVIEW_API_BASE", "http://localhost:8001")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

async def main():
    db = AsyncIOMotorClient(MONGO_URL)[DB_NAME]
    out = json.load(open("/app/deliverables/phase20/matrix_results.json"))
    rows = out["rows"]
    by_id = {r["id"]: r for r in rows}
    partials = [r["id"] for r in rows if r["score"] == "PARTIAL"]
    print(f"Re-running {len(partials)} PARTIAL rows: {partials}")

    async with httpx.AsyncClient(timeout=240.0) as http:
        for qid in partials:
            q = next(q for q in QUESTIONS if q.id == qid)
            sid = await _bootstrap_session(db, role=q.role)
            t0 = time.time()
            try:
                resp = await http.post(f"{API_BASE}/api/agent/turn",
                                       json={"session_id": sid, "message": q.text})
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  {qid} ERROR {type(e).__name__}: {e}")
                continue
            score = _score(q, data)
            block_types = [b.get("type") for b in (data.get("blocks") or []) if isinstance(b, dict)]
            tb = _extract_trace_bits(data.get("trace") or [])
            tools = tb.get("tools_called") or []
            print(f"  {qid} {by_id[qid]['score']} -> {score}  blocks={block_types}  tools={tools[:4]}")
            r = by_id[qid]
            r["score"] = score
            r["blocks"] = block_types
            r["tools_called"] = tools
            r["latency_ms"] = int((time.time()-t0)*1000)
            r["analyzer"] = tb.get("analyzer")
            hint = (tb.get("analyzer") or {}).get("tool_hint") or []
            r["analyzer_hint_matched_actual_use"] = bool(set(hint) & set(tools))

    by_score = {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "BLOCKED": 0}
    for r in rows:
        by_score[r["score"]] = by_score.get(r["score"], 0) + 1
    out["summary"] = by_score
    json.dump(out, open("/app/deliverables/phase20/matrix_results.json","w"), indent=2, default=str)
    print(f"\nNEW SUMMARY: {by_score}")
    print(f"Gate: PASS>=45 {'MET' if by_score['PASS']>=45 else 'NOT MET'}")

if __name__ == "__main__":
    asyncio.run(main())
