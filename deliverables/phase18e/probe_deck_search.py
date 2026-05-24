"""Phase 18c — Deck Vector Engine re-probe (May 24, 2026, ~6pm).

Read-only probe. Same harness as 18 / 18b, with three additions:
  - sample `totalIndexed` over 60 s
  - distribution by source across 20 broad queries
  - 20-row coverage parity vs local cosine
"""
from __future__ import annotations
import os, sys, json, time, asyncio, statistics, hashlib
from pathlib import Path
from typing import Any, Dict, List

import httpx

BASE = (os.environ.get("SMIFS_KNOWLEDGE_BASE_URL") or "").rstrip("/")
KEY  = os.environ.get("SMIFS_KNOWLEDGE_API_KEY") or ""
OUT  = Path("/app/deliverables/phase18e")
RAW  = OUT / "raw"
RAW.mkdir(parents=True, exist_ok=True)
HDR  = {"X-API-Key": KEY, "Content-Type": "application/json"}


async def call(client, body, attempts=3):
    last = None
    for i in range(attempts):
        try:
            r = await client.post(f"{BASE}/api/knowledge/search", headers=HDR, json=body, timeout=30.0)
            if r.status_code == 200:
                return {"ok": True, "status": 200, "json": r.json(), "elapsed_ms": int(r.elapsed.total_seconds() * 1000)}
            if 500 <= r.status_code < 600:
                last = {"ok": False, "status": r.status_code, "elapsed_ms": int(r.elapsed.total_seconds() * 1000)}
                await asyncio.sleep(2 ** i); continue
            return {"ok": False, "status": r.status_code, "body": r.text[:600], "elapsed_ms": int(r.elapsed.total_seconds() * 1000)}
        except Exception as e:
            last = {"ok": False, "status": "exc", "exc": f"{type(e).__name__}: {e}"}
            await asyncio.sleep(2 ** i)
    return last or {"ok": False, "status": "unknown"}


def slug(q): 
    h = hashlib.sha1(q.encode("utf-8")).hexdigest()[:8]
    keep = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in q.lower())[:36]
    return f"{keep}__{h}"

def save_raw(label, body, resp):
    (RAW / f"{label}.json").write_text(json.dumps({"request": body, "response": resp}, indent=2, ensure_ascii=False))


SCHEMA_QUERIES = [
    "What is an AIF Category II?",
    "PURPLE STYLE LABS NCD debt funding focused vehicle",
    "Retirement planning slide v2 bedrock",
    "ARN transfer process for mutual funds",
    "Mediclaim providers in SMIFS distribution",
]

PARAM_PROBES = [
    {"q": "AIF", "top_k": 5},
    {"q": "AIF", "top_k": 5, "min_score": 0.1},
    {"q": "AIF", "top_k": 5, "minScore": 0.1},
    {"q": "AIF", "top_k": 5, "sources": ["bedrock"]},
    {"q": "AIF", "top_k": 5, "sources": ["academy"]},
    {"q": "AIF", "top_k": 5, "sources": ["document"]},
    {"q": "AIF", "top_k": 5, "source": "bedrock"},
    {"q": "AIF", "top_k": 5, "subsource": "bedrock"},
    {"q": "AIF", "top_k": 5, "exclude_sources": ["sales_pitch"]},
    {"q": "AIF", "top_k": 5, "audience": "all"},
    {"q": "AIF", "top_k": 5, "audience": "employee_only"},
    {"q": "AIF", "top_k": 5, "language": "en"},
    {"q": "AIF", "top_k": 5, "is_focused": True},
    {"q": "AIF", "top_k": 5, "is_active": True},
    {"q": "AIF", "top_k": 5, "vehicle_id": "cc602b11-9fc2-4bbd-b6af-df529f3bf719"},
    {"q": "AIF", "top_k": 5, "filters": {"source": "bedrock"}},
]

MULTILINGUAL = [
    ("en",       "investment opportunities right now"),
    ("hi",       "निवेश के अवसर"),
    ("hi2",      "AIF क्या होता है"),
    ("ta",       "AIF க்கான தமிழ் pitch"),
    ("ta2",      "PMS திட்டம் என்ன"),
    ("bn",       "এআইএফ বিনিয়োগ সুযোগ"),
    ("mr",       "गुंतवणुकीच्या संधी"),
    ("hinglish", "AIF ka structure kya hai"),
    ("en2",      "what is AIF structure"),
]

HIST_QUERIES = SCHEMA_QUERIES + [
    "PMS strategy small cap focused", "NCD primary issue tenure",
    "fixed deposit rates corporate", "insurance distribution providers",
    "mutual fund SIP eligibility KYC", "bharat NCD series III tranche",
    "AIF sales pitch script", "OrgLens employee directory",
    "what is alpha generation", "growth revenue dashboard FY26",
    "tax implications of long term capital gains AIF",
    "SEBI category II investor disclosure", "client onboarding KYC process",
    "house view bedrock fortnightly offering", "retirement planning slide v2.1",
    "PMS provider Marcellus", "Sapphire AIF lock in tenure",
    "PURPLE STYLE LABS NCD coupon", "Mackertich ONE Fund of Fund Series VI",
    "Mediclaim group health insurance", "what does AIF mean",
    "how to invest in NCDs", "wealth manager onboarding flow",
    "ARN code transfer documentation", "DCF valuation slide",
]
PAR_QUERIES = HIST_QUERIES[:10]

SOURCE_PROBE_QUERIES = [
    "AIF", "PMS", "NCD", "MF SIP", "insurance", "academy", "beginner",
    "risk", "tax", "vehicle", "sales pitch", "fortnightly",
    "compliance", "SEBI", "onboarding", "partner", "equity",
    "debt", "fund of fund", "growth",
]


async def main():
    if not (BASE and KEY):
        print("ENV missing", file=sys.stderr); sys.exit(2)
    print(f"Probing {BASE}/api/knowledge/search → {OUT}")

    async with httpx.AsyncClient() as client:
        summary: Dict[str, Any] = {"base": BASE, "probe_started_at": time.time()}

        # --- (0) totalIndexed over a 60-second window
        print("(0) totalIndexed sampling (60s window)")
        ti_samples = []
        for i in range(7):  # 0, 10, 20, …, 60 seconds
            body = {"q": "AIF", "top_k": 1}
            resp = await call(client, body)
            j = resp.get("json") or {}
            ti_samples.append({"t_s": i * 10, "totalIndexed": j.get("totalIndexed"),
                                "n_results": len(j.get("results") or [])})
            if i < 6: await asyncio.sleep(10)
        summary["totalIndexed_window"] = ti_samples

        # --- (1) schema discovery
        print("(1) schema queries")
        schema_hits = []
        for q in SCHEMA_QUERIES:
            body = {"q": q, "top_k": 5}
            resp = await call(client, body)
            save_raw(f"schema__{slug(q)}", body, resp)
            schema_hits.append({"q": q, "resp": resp.get("json") or resp})
        envelope_keys: Dict[str, int] = {}
        all_hit_keys: Dict[str, int] = {}
        meta_keys_by_source: Dict[str, Dict[str, int]] = {}
        for sh in schema_hits:
            j = sh["resp"] if isinstance(sh["resp"], dict) else {}
            for k in j.keys(): envelope_keys[k] = envelope_keys.get(k, 0) + 1
            for hit in (j.get("results") or []):
                for k in hit.keys(): all_hit_keys[k] = all_hit_keys.get(k, 0) + 1
                src = hit.get("source") or "?"
                meta = hit.get("metadata") or {}
                if isinstance(meta, dict):
                    bucket = meta_keys_by_source.setdefault(src, {})
                    for mk in meta.keys(): bucket[mk] = bucket.get(mk, 0) + 1
        summary["envelope_keys"] = envelope_keys
        summary["hit_keys"] = all_hit_keys
        summary["metadata_keys_by_source"] = meta_keys_by_source

        # --- (2) param exploration
        print("(2) param probes")
        param_results = []
        for body in PARAM_PROBES:
            resp = await call(client, body)
            j = resp.get("json") or {}
            results = j.get("results") or []
            extra = {
                "echoed_sources": j.get("sources"),
                "echoed_minScore": j.get("minScore"),
                "n_results": len(results),
                "result_sources": [r.get("source") for r in results],
                "result_ids": [r.get("id") for r in results],
                "totalIndexed": j.get("totalIndexed"),
                "status": resp.get("status"),
            }
            param_results.append({"req": body, "echo": extra})
            save_raw(f"param__{slug(json.dumps(body, sort_keys=True))}", body, resp)
        summary["param_results"] = param_results

        # --- (3) multilingual
        print("(3) multilingual")
        ml_results = []
        for tag, q in MULTILINGUAL:
            body = {"q": q, "top_k": 5}
            resp = await call(client, body)
            save_raw(f"ml__{tag}__{slug(q)}", body, resp)
            j = resp.get("json") or {}
            ml_results.append({
                "lang": tag, "q": q, "status": resp.get("status"),
                "n_results": len(j.get("results") or []),
                "top_sources": [h.get("source") for h in (j.get("results") or [])[:5]],
                "top_titles":  [(h.get("title") or "")[:60] for h in (j.get("results") or [])[:5]],
                "top_scores":  [round(float(h.get("score", 0)), 4) for h in (j.get("results") or [])[:5]],
            })
        summary["multilingual"] = ml_results

        # --- (4) histogram + per-query latency
        print("(4) histogram + serial latency")
        scores: List[float] = []
        per_q = []
        per_q_hits: List[List[dict]] = []
        for q in HIST_QUERIES:
            body = {"q": q, "top_k": 5}
            resp = await call(client, body)
            j = resp.get("json") or {}
            hits = j.get("results") or []
            ss = [float(h.get("score", 0)) for h in hits]
            per_q.append({"q": q, "n": len(ss), "max": max(ss) if ss else None,
                          "min": min(ss) if ss else None,
                          "elapsed_ms": resp.get("elapsed_ms")})
            scores.extend(ss)
            per_q_hits.append(hits)
        if scores:
            sorted_scores = sorted(scores)
            summary["score_stats"] = {
                "n": len(scores), "min": round(min(scores), 4), "max": round(max(scores), 4),
                "p10":    round(sorted_scores[len(sorted_scores)//10], 4),
                "p25":    round(sorted_scores[len(sorted_scores)//4], 4),
                "median": round(statistics.median(scores), 4),
                "p75":    round(sorted_scores[3*len(sorted_scores)//4], 4),
                "p90":    round(sorted_scores[9*len(sorted_scores)//10], 4),
                "p99":    round(sorted_scores[int(0.99*len(sorted_scores))], 4),
            }
        summary["per_query_hist"] = per_q
        summary["all_scores"] = scores

        # --- (5) latency
        serial_lat = [pq["elapsed_ms"] for pq in per_q if pq.get("elapsed_ms")]
        slat = sorted(serial_lat)
        summary["latency_serial_ms"] = {
            "n": len(slat), "p50": slat[len(slat)//2],
            "p95": slat[min(len(slat)-1, int(len(slat)*0.95))],
            "min": slat[0], "max": slat[-1],
        }
        async def one(q):
            body = {"q": q, "top_k": 5}
            t0 = time.monotonic()
            resp = await call(client, body)
            return int((time.monotonic() - t0) * 1000), len((resp.get("json") or {}).get("results") or [])
        t0 = time.monotonic()
        pres = await asyncio.gather(*(one(q) for q in PAR_QUERIES))
        wall = int((time.monotonic() - t0) * 1000)
        summary["latency_parallel"] = {
            "n": len(PAR_QUERIES), "wall_ms": wall,
            "individual_ms_sorted": sorted([p[0] for p in pres]),
        }

        # --- (6) source distribution across 20 broad queries (top_k=25)
        print("(6) source distribution")
        from collections import Counter
        src_counter = Counter(); unique_ids = set()
        for q in SOURCE_PROBE_QUERIES:
            body = {"q": q, "top_k": 25}
            resp = await call(client, body)
            for h in (resp.get("json") or {}).get("results") or []:
                src_counter[h.get("source") or "?"] += 1
                if h.get("id"): unique_ids.add(h["id"])
        summary["source_distribution"] = dict(src_counter)
        summary["unique_ids_observed"] = len(unique_ids)

        # --- (7) join key check (20 fresh hits across all sources)
        print("(7) join key (20 fresh hits)")
        join_ids: List[str] = []
        for q in ("AIF", "PMS", "NCD", "insurance", "academy"):
            body = {"q": q, "top_k": 5}
            resp = await call(client, body)
            for h in (resp.get("json") or {}).get("results") or []:
                if h.get("id"): join_ids.append(h["id"])
        join_ids = list(dict.fromkeys(join_ids))[:25]
        join_result: Dict[str, Any] = {"sample_ids": join_ids[:8], "n_sampled": len(join_ids)}
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            mc = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = mc[os.environ["DB_NAME"]]
            n_match = await db.doc_chunks.count_documents({"smifs_id": {"$in": join_ids}})
            join_result["matched_smifs_id"] = n_match
            join_result["miss_rate_pct"] = round(100 * (len(join_ids) - n_match) / max(1, len(join_ids)), 1)
            # By prefix
            from collections import Counter as _C
            join_result["sample_prefix_distribution"] = dict(_C(x.split(":")[0] for x in join_ids))
            mc.close()
        except Exception as e:
            join_result["error"] = f"{type(e).__name__}: {e}"
        summary["join_key_check"] = join_result

        # --- (8) coverage parity vs local baseline (20 rows)
        print("(8) coverage parity vs local baseline (20 rows)")
        try:
            baseline = json.load(open("/app/deliverables/phase18/deck_search_probe/local_baseline.json"))
        except Exception as e:
            baseline = []
            print(f"  skipped baseline load: {e}")
        parity_rows = []
        for row in baseline:
            q = row["q"]
            body = {"q": q, "top_k": 10}
            resp = await call(client, body)
            deck_hits = (resp.get("json") or {}).get("results") or []
            deck_top3 = deck_hits[:3]
            local_top3 = row.get("top_3", [])
            local_titles = {(t.get("title") or "").lower().strip() for t in local_top3}
            overlap = sum(1 for t in deck_top3 if (t.get("title") or "").lower().strip() in local_titles)
            parity_rows.append({
                "q": q,
                "local_top1": (local_top3[0].get("title") if local_top3 else None),
                "local_top1_sub": (local_top3[0].get("subsource") if local_top3 else None),
                "local_top1_score": (local_top3[0].get("score") if local_top3 else None),
                "deck_top1": (deck_top3[0].get("title") if deck_top3 else None),
                "deck_top1_src": (deck_top3[0].get("source") if deck_top3 else None),
                "deck_top1_score": (deck_top3[0].get("score") if deck_top3 else None),
                "overlap_of_3": overlap,
                "verdict": ("identical" if overlap == 3 else
                            "partial_2" if overlap == 2 else
                            "partial_1" if overlap == 1 else "no_overlap"),
            })
        summary["coverage_parity"] = parity_rows

        summary["probe_completed_at"] = time.time()
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        print(f"Wrote {OUT}/summary.json")
        # Concise console digest
        print(json.dumps({
            "totalIndexed_window": ti_samples,
            "envelope_keys": envelope_keys,
            "hit_keys": all_hit_keys,
            "score_stats": summary["score_stats"],
            "latency_serial_ms": summary["latency_serial_ms"],
            "latency_parallel_wall_ms": summary["latency_parallel"]["wall_ms"],
            "source_distribution": summary["source_distribution"],
            "join_key_check": join_result,
            "parity_verdict_rollup": {v: sum(1 for r in parity_rows if r["verdict"] == v)
                                       for v in ("identical","partial_2","partial_1","no_overlap")},
        }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
