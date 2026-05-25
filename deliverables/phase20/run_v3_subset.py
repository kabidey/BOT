#!/usr/bin/env python3
"""Phase 20 v3 — Subset runner.

Re-runs only the rows that did NOT achieve PASS in v2 (15 PARTIAL + 1 BLOCKED).
For each row we run TWO synthesis variants back-to-back:
  • gpt-4o   (the current default)
  • claude-sonnet-4-5-20251002   (the probe — controlled via PHASE_20_SYNTHESIS_MODEL)

We swap the model by hitting the backend with a custom HTTP header
`X-Phase20-Synthesis-Model` and the orchestrator picks it up via env, or — in
practice — we patch the env via supervisor restart on each variant. Simpler
approach used here: set env, restart backend, run subset, capture scores,
restart again with the other model.

Outputs:
  /app/deliverables/phase20/matrix_results_v3.json
  /app/deliverables/phase20/matrix_run_v3.md   (per-row movement, composer probe)
"""
import asyncio
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/deliverables/phase20")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from run_matrix import (QUESTIONS, EMPLOYEE_IDENTITY, CLIENT_IDENTITY,
                        _bootstrap_session, _score, _extract_trace_bits)

API_BASE = os.environ.get("PREVIEW_API_BASE", "http://localhost:8001")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
MODEL_TO_PROBE = os.environ.get("PROBE_MODEL", "gpt-4o")
LABEL = os.environ.get("PROBE_LABEL", "default")


async def run_subset(ids: List[str]) -> Dict[str, Dict[str, Any]]:
    db = AsyncIOMotorClient(MONGO_URL)[DB_NAME]
    out: Dict[str, Dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=240.0) as http:
        for qid in ids:
            q = next(qq for qq in QUESTIONS if qq.id == qid)
            sid = await _bootstrap_session(db, role=q.role)
            t0 = time.time()
            try:
                resp = await http.post(f"{API_BASE}/api/agent/turn",
                                       json={"session_id": sid, "message": q.text})
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                out[qid] = {"score": "FAIL", "blocks": [], "tools_called": [],
                            "error": f"{type(e).__name__}: {e}",
                            "latency_ms": int((time.time()-t0)*1000)}
                print(f"  {qid} FAIL ({type(e).__name__})")
                continue
            score = _score(q, data)
            blocks = [b.get("type") for b in (data.get("blocks") or []) if isinstance(b, dict)]
            tb = _extract_trace_bits(data.get("trace") or [])
            tools = tb.get("tools_called") or []
            # Excerpt
            excerpt_parts = []
            for b in data.get("blocks") or []:
                if not isinstance(b, dict): continue
                if b.get("type") == "text": excerpt_parts.append(b.get("text",""))
                elif b.get("type") == "table": excerpt_parts.append(
                    f"[table:{b.get('title','?')} rows={len(b.get('rows') or [])} "
                    f"fallback={b.get('fallback_synthesised',False)}]")
                elif b.get("type") == "chart": excerpt_parts.append(
                    f"[chart:{b.get('kind','?')} {b.get('title','')}]")
                elif b.get("type") == "image": excerpt_parts.append(f"[image]")
                elif b.get("type") in ("employee_card","client_card"):
                    excerpt_parts.append(f"[{b.get('type')}]")
            excerpt = " | ".join(excerpt_parts)[:240]
            # Detect gate-driven outcomes
            trace = data.get("trace") or []
            gate_reprompt = any(t.get("step")=="hard_gate_reprompt" for t in trace)
            gate_retry_ok = any(t.get("step")=="hard_gate_retry_ok" for t in trace)
            gate_fallback = any(t.get("step")=="hard_gate_programmatic_fallback" for t in trace)
            out[qid] = {
                "score": score, "blocks": blocks, "tools_called": tools,
                "reply_excerpt": excerpt,
                "latency_ms": int((time.time()-t0)*1000),
                "intent": data.get("intent"),
                "analyzer": tb.get("analyzer"),
                "analyzer_hint_matched_actual_use": bool(
                    set((tb.get("analyzer") or {}).get("tool_hint") or []) & set(tools)),
                "gate_reprompt": gate_reprompt,
                "gate_retry_ok": gate_retry_ok,
                "gate_programmatic_fallback": gate_fallback,
            }
            gate_note = ""
            if gate_reprompt:
                gate_note = " (gate-retry-ok)" if gate_retry_ok else (" (gate-fallback)" if gate_fallback else " (gate-tried)")
            print(f"  {qid} {score:7s} [{','.join(blocks) or '-'}] tools={len(tools)} {out[qid]['latency_ms']}ms{gate_note}")
    return out


async def main():
    # Load v2 to determine which rows need re-runs.
    v2 = json.load(open("/app/deliverables/phase20/matrix_results_v2.json"))
    rerun_ids = [r["id"] for r in v2["rows"] if r["score"] != "PASS" and r["score"] != "BLOCKED"]
    # E1 is BLOCKED-by-design, skip.
    print(f"Re-running {len(rerun_ids)} rows with model={MODEL_TO_PROBE} label={LABEL}: {rerun_ids}")
    out = await run_subset(rerun_ids)
    # Persist this variant's results.
    target = f"/app/deliverables/phase20/_v3_probe_{LABEL}.json"
    with open(target, "w") as f:
        json.dump({"model": MODEL_TO_PROBE, "label": LABEL, "rerun_ids": rerun_ids,
                   "results": out}, f, indent=2, default=str)
    print(f"\nWrote {target}")
    by_score = {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "BLOCKED": 0}
    for r in out.values():
        by_score[r["score"]] = by_score.get(r["score"], 0) + 1
    print(f"Subset summary ({LABEL}): {by_score}")


if __name__ == "__main__":
    asyncio.run(main())
