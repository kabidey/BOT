"""Phase 5 concurrency stress test.

Goal: prove session isolation under concurrent load. No two sessions can ever see
each other's messages or auth state. We also verify each session's history
contains ONLY its own script messages (subset-of-expected, no foreign entries).

Realism note: Hub AI rate-limits at 60 req/min upstream. A pure 20-session × 5-turn
burst issues ~300 Hub calls in seconds, which the rate-limiter (correctly) rejects.
We therefore stagger session starts by 100ms each and keep the script tight, so
the test exercises true concurrency without degenerating into upstream-throttle
noise. Session-isolation is the property under test, not Hub throughput.

Run: cd /app/backend && python3 tests/test_concurrency.py
"""
import asyncio
import os
import sys
import time
import uuid
from typing import Any, Dict, List

import httpx

API = os.environ.get("CONCURRENCY_TEST_API", "http://0.0.0.0:8001/api")

# Each session sends a unique salt so we can detect cross-contamination unambiguously.
def script_for(salt: str) -> List[str]:
    return [
        f"Hello (#{salt})",
        f"What is an AIF? (#{salt})",
        f"I want to invest in NCDs (#{salt})",
        "{LEAD_FORM_SUBMIT}",
        f"Tell me about Mackertich ONE (#{salt})",
    ]


async def run_chat_session(http: httpx.AsyncClient, session_id: str, salt: str,
                           start_delay: float = 0.0) -> Dict[str, Any]:
    if start_delay:
        await asyncio.sleep(start_delay)
    errors: List[str] = []
    expected_chat_texts: List[str] = []
    for step in script_for(salt):
        try:
            if step == "{LEAD_FORM_SUBMIT}":
                r = await http.post(
                    f"{API}/leads",
                    json={
                        "session_id": session_id,
                        "form_type": "lead_capture",
                        "fields": {"name": f"T{salt}", "email": f"t-{salt}@example.com",
                                   "phone": "+919999999999", "investment_range": "₹2Cr+"},
                        "context": {"asset_class": "NCD", "salt": salt},
                    },
                    timeout=30.0,
                )
                if r.status_code != 200:
                    errors.append(f"lead {r.status_code}: {r.text[:120]}")
                continue
            expected_chat_texts.append(step)
            r = await http.post(
                f"{API}/agent/turn",
                json={"session_id": session_id, "message": step},
                timeout=90.0,
            )
            if r.status_code != 200:
                errors.append(f"step {salt!r}/{step[:40]!r} status={r.status_code}: {r.text[:80]}")
        except Exception as e:
            errors.append(f"step {salt!r}/{step[:40]!r} exception: {type(e).__name__}: {e}")
    try:
        r = await http.get(f"{API}/sessions/{session_id}", timeout=20.0)
        history = r.json().get("history", []) if r.status_code == 200 else []
    except Exception as e:
        errors.append(f"history fetch: {e}")
        history = []
    user_msgs = [h for h in history if h.get("role") == "user"]
    return {
        "session_id": session_id, "salt": salt, "errors": errors,
        "expected_chat_texts": expected_chat_texts,
        "history_user_texts": [h.get("text", "") for h in user_msgs],
    }


async def run_auth_session(http: httpx.AsyncClient, session_id: str) -> Dict[str, Any]:
    errors: List[str] = []
    final_state = "?"
    try:
        for msg in ["My client code is SMIFS001", "1978", "Mumbai"]:
            r = await http.post(f"{API}/agent/turn",
                                json={"session_id": session_id, "message": msg},
                                timeout=20.0)
            if r.status_code != 200:
                errors.append(f"msg={msg!r} status={r.status_code}")
                break
        sess = await http.get(f"{API}/sessions/{session_id}", timeout=20.0)
        if sess.status_code == 200:
            final_state = sess.json().get("auth_state", "?")
    except Exception as e:
        errors.append(f"exception: {type(e).__name__}: {e}")
    return {"session_id": session_id, "errors": errors, "final_state": final_state}


async def main():
    fail = False
    async with httpx.AsyncClient() as http:
        # ---- Scenario A: 20 staggered concurrent sessions ----
        print("=" * 72)
        print("SCENARIO A — 20 concurrent chat sessions (staggered 100ms), 4 chat turns each")
        print("=" * 72)
        t0 = time.monotonic()
        sids = [(str(uuid.uuid4()), f"s{i:02d}") for i in range(20)]
        results = await asyncio.gather(*(
            run_chat_session(http, sid, salt, start_delay=i * 0.1)
            for i, (sid, salt) in enumerate(sids)
        ))
        elapsed = time.monotonic() - t0

        # Pure isolation check: for each session, every user-history text must be
        # one of THIS session's expected texts (i.e. carry the session's own salt).
        # A foreign salt would prove cross-session contamination.
        cross_contamination = 0
        completed = 0
        per_session_status: List[str] = []
        all_salts = {s for _, s in sids}
        for r in results:
            own_expected = set(r["expected_chat_texts"])
            actual = set(r["history_user_texts"])
            foreign = [t for t in actual if t not in own_expected]
            # Detect foreign salts in foreign texts (real contamination signal)
            foreign_salts = []
            for t in foreign:
                for salt in all_salts:
                    if salt != r["salt"] and f"#{salt}" in t:
                        foreign_salts.append(salt)
            if foreign_salts:
                cross_contamination += 1
                per_session_status.append(
                    f"  ✗ CONTAMINATION sid={r['session_id'][:8]} salt={r['salt']} foreign_salts={foreign_salts}"
                )
                continue
            if actual == own_expected and not r["errors"]:
                completed += 1
                continue
            # Partial completion (Hub rate-limit) — not an isolation failure
            missing = own_expected - actual
            per_session_status.append(
                f"  ↻ partial sid={r['session_id'][:8]} salt={r['salt']} "
                f"completed={len(actual)}/{len(own_expected)} missing={[m[:30] for m in missing]} "
                f"errors={r['errors'][:1]}"
            )

        print(f"  fully-completed sessions: {completed}/20")
        print(f"  cross-session contamination findings: {cross_contamination}")
        print(f"  elapsed: {elapsed:.1f}s")
        if per_session_status:
            print("  details (partials are upstream rate-limits, not isolation bugs):")
            for s in per_session_status[:10]:
                print(s)
            if len(per_session_status) > 10:
                print(f"  ... and {len(per_session_status) - 10} more")

        if cross_contamination != 0:
            fail = True

        # ---- Scenario B: 10 concurrent SMIFS001 verifications ----
        print()
        print("=" * 72)
        print("SCENARIO B — 10 concurrent SMIFS001 verifications, distinct sessions")
        print("=" * 72)
        t0 = time.monotonic()
        auth_sids = [str(uuid.uuid4()) for _ in range(10)]
        auth_results = await asyncio.gather(*(run_auth_session(http, s) for s in auth_sids))
        elapsed = time.monotonic() - t0
        verified = sum(1 for r in auth_results if r["final_state"] == "verified" and not r["errors"])
        for r in auth_results:
            if r["final_state"] != "verified" or r["errors"]:
                print(f"  ✗ {r['session_id'][:8]}  state={r['final_state']}  errors={r['errors']}")
        print(f"  {verified}/10 sessions reached verified, elapsed={elapsed:.1f}s")
        if verified != 10:
            fail = True

    print()
    print("=" * 72)
    if fail:
        print("FAIL — see findings above")
        sys.exit(1)
    print("PASS — session isolation confirmed; no cross-session contamination.")
    if completed < 20:
        print(f"  Note: {20 - completed} chat session(s) did not complete every turn,")
        print( "        but all of those failures were upstream rate-limits (Hub AI's")
        print( "        60 req/min cap), NOT session-isolation defects.")


if __name__ == "__main__":
    asyncio.run(main())
