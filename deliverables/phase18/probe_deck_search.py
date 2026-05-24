"""Phase 18 — Deck Vector Engine probe.

Pure read-only probe of POST /api/knowledge/search on the SMIFS Deck. Writes
raw JSON responses and a small structured summary into the deliverables
folder. Does NOT change any code path used by the running bot.

Run from /app/backend so .env loads (or pre-export the two vars).
"""
from __future__ import annotations

import os, sys, json, time, asyncio, statistics, hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

BASE = (os.environ.get("SMIFS_KNOWLEDGE_BASE_URL") or "").rstrip("/")
KEY  = os.environ.get("SMIFS_KNOWLEDGE_API_KEY") or ""
OUT  = Path("/app/deliverables/phase18/deck_search_probe")
RAW  = OUT / "raw"
RAW.mkdir(parents=True, exist_ok=True)

HDR = {"X-API-Key": KEY, "Content-Type": "application/json"}


# --------- robust call w/ exponential backoff for Cloudflare 5xx ---------
async def call(client: httpx.AsyncClient, body: Dict[str, Any], *, attempts: int = 4) -> Dict[str, Any]:
    last = None
    for i in range(attempts):
        try:
            r = await client.post(f"{BASE}/api/knowledge/search", headers=HDR, json=body, timeout=30.0)
            if r.status_code == 200:
                return {"ok": True, "status": 200, "json": r.json(), "elapsed_ms": int(r.elapsed.total_seconds() * 1000)}
            if 500 <= r.status_code < 600:
                last = {"ok": False, "status": r.status_code, "body": r.text[:400], "elapsed_ms": int(r.elapsed.total_seconds() * 1000)}
                await asyncio.sleep(2 ** i)
                continue
            return {"ok": False, "status": r.status_code, "body": r.text[:1200], "elapsed_ms": int(r.elapsed.total_seconds() * 1000)}
        except Exception as e:
            last = {"ok": False, "status": "exc", "exc": f"{type(e).__name__}: {e}"}
            await asyncio.sleep(2 ** i)
    return last or {"ok": False, "status": "unknown"}


def slug(q: str) -> str:
    h = hashlib.sha1(q.encode("utf-8")).hexdigest()[:8]
    keep = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in q.lower())[:32]
    return f"{keep}__{h}"


def save_raw(label: str, body: Dict[str, Any], resp: Dict[str, Any]) -> None:
    fp = RAW / f"{label}.json"
    fp.write_text(json.dumps({"request": body, "response": resp}, indent=2, ensure_ascii=False))


# --------- (1) schema discovery ---------
SCHEMA_QUERIES = [
    "What is an AIF Category II?",
    "PURPLE STYLE LABS NCD debt funding focused vehicle",
    "Retirement planning slide v2 bedrock",
    "ARN transfer process for mutual funds",
    "Mediclaim providers in SMIFS distribution",
]

# --------- (2) parameter exploration ---------
PARAM_PROBES = [
    {"q": "AIF", "top_k": 5},
    {"q": "AIF", "top_k": 5, "min_score": 0.1},
    {"q": "AIF", "top_k": 5, "minScore": 0.1},
    {"q": "AIF", "top_k": 5, "sources": ["bedrock"]},
    {"q": "AIF", "top_k": 5, "source": "bedrock"},
    {"q": "AIF", "top_k": 5, "subsource": "bedrock"},
    {"q": "AIF", "top_k": 5, "exclude_sources": ["sales_pitch"]},
    {"q": "AIF", "top_k": 5, "audience": "all"},
    {"q": "AIF", "top_k": 5, "language": "en"},
    {"q": "AIF", "top_k": 5, "vehicle_id": "cc602b11-9fc2-4bbd-b6af-df529f3bf719"},
    {"q": "AIF", "top_k": 5, "is_focused": True},
    {"q": "AIF", "top_k": 5, "filters": {"source": "bedrock"}},
    {"q": "AIF", "top_k": 5, "filter": {"source": "bedrock"}},
]

# --------- (3) multilingual ---------
MULTILINGUAL = [
    ("en",       "investment opportunities right now"),
    ("hi",       "निवेश के अवसर"),
    ("ta",       "AIF க்கான தமிழ் pitch"),
    ("bn",       "এআইএফ বিনিয়োগ সুযোগ"),
    ("mr",       "गुंतवणुकीच्या संधी"),
    ("hinglish", "AIF ka structure kya hai"),
    ("en2",      "what is AIF structure"),
]

# --------- (4) score histogram (30 queries) ---------
HIST_QUERIES = SCHEMA_QUERIES + [
    "PMS strategy small cap focused",
    "NCD primary issue tenure",
    "fixed deposit rates corporate",
    "insurance distribution providers",
    "mutual fund SIP eligibility KYC",
    "bharat NCD series III tranche",
    "AIF sales pitch script",
    "OrgLens employee directory",
    "what is alpha generation",
    "growth revenue dashboard FY26",
    "tax implications of long term capital gains AIF",
    "SEBI category II investor disclosure",
    "client onboarding KYC process",
    "house view bedrock fortnightly offering",
    "retirement planning slide v2.1",
    "PMS provider Marcellus",
    "Sapphire AIF lock in tenure",
    "PURPLE STYLE LABS NCD coupon",
    "Mackertich ONE Fund of Fund Series VI",
    "Mediclaim group health insurance",
    "what does AIF mean",
    "how to invest in NCDs",
    "wealth manager onboarding flow",
    "ARN code transfer documentation",
    "DCF valuation slide",
]

# --------- (5) parallel latency ---------
PAR_QUERIES = HIST_QUERIES[:10]


async def main():
    if not (BASE and KEY):
        print("ENV missing — SMIFS_KNOWLEDGE_BASE_URL or SMIFS_KNOWLEDGE_API_KEY", file=sys.stderr)
        sys.exit(2)
    print(f"Probing {BASE}/api/knowledge/search")
    async with httpx.AsyncClient() as client:
        summary: Dict[str, Any] = {"base": BASE}

        # --- (1) schema
        schema_hits = []
        for q in SCHEMA_QUERIES:
            body = {"q": q, "top_k": 5}
            resp = await call(client, body)
            save_raw(f"schema__{slug(q)}", body, resp)
            schema_hits.append({"q": q, "resp": resp.get("json") or resp})
        summary["schema_runs"] = len(schema_hits)
        # Field discovery on hits
        all_hit_keys: Dict[str, int] = {}
        envelope_keys: Dict[str, int] = {}
        for sh in schema_hits:
            j = sh["resp"] if isinstance(sh["resp"], dict) else {}
            for k in j.keys():
                envelope_keys[k] = envelope_keys.get(k, 0) + 1
            for hit in (j.get("results") or []):
                for k in hit.keys():
                    all_hit_keys[k] = all_hit_keys.get(k, 0) + 1
        summary["envelope_keys"] = envelope_keys
        summary["hit_keys"] = all_hit_keys

        # --- (2) parameter exploration
        param_results = []
        for body in PARAM_PROBES:
            resp = await call(client, body)
            j = resp.get("json") or {}
            extra = {
                "echoed_sources": j.get("sources"),
                "echoed_minScore": j.get("minScore"),
                "echoed_topK": j.get("topK"),
                "n_results": len(j.get("results") or []),
                "totalIndexed": j.get("totalIndexed"),
                "status": resp.get("status"),
            }
            param_results.append({"req": body, "echo": extra})
            save_raw(f"param__{slug(json.dumps(body, sort_keys=True))}", body, resp)
        summary["param_results"] = param_results

        # --- (3) multilingual
        ml_results = []
        for tag, q in MULTILINGUAL:
            body = {"q": q, "top_k": 5}
            resp = await call(client, body)
            save_raw(f"ml__{tag}__{slug(q)}", body, resp)
            j = resp.get("json") or {}
            ml_results.append({
                "lang": tag, "q": q, "status": resp.get("status"),
                "n_results": len(j.get("results") or []),
                "top_titles": [(h.get("title") or h.get("source") or h.get("id") or "")[:80]
                               for h in (j.get("results") or [])[:5]],
                "top_scores": [round(float(h.get("score", 0)), 4) for h in (j.get("results") or [])[:5]],
            })
        summary["multilingual"] = ml_results

        # --- (4) score histogram
        scores: List[float] = []
        per_q = []
        for q in HIST_QUERIES:
            body = {"q": q, "top_k": 5}
            resp = await call(client, body)
            j = resp.get("json") or {}
            ss = [float(h.get("score", 0)) for h in (j.get("results") or [])]
            per_q.append({"q": q, "n": len(ss), "max": max(ss) if ss else None, "min": min(ss) if ss else None,
                          "elapsed_ms": resp.get("elapsed_ms")})
            scores.extend(ss)
        if scores:
            summary["score_stats"] = {
                "n": len(scores), "min": round(min(scores), 4), "max": round(max(scores), 4),
                "median": round(statistics.median(scores), 4),
                "p90": round(sorted(scores)[int(len(scores)*0.9) - 1], 4) if len(scores) >= 10 else None,
                "p10": round(sorted(scores)[max(0, int(len(scores)*0.1) - 1)], 4) if len(scores) >= 10 else None,
            }
        else:
            summary["score_stats"] = {"n": 0, "note": "no results across hist queries — index likely empty"}
        summary["per_query_hist"] = per_q

        # --- (5) latency (serial p50/p95 + 10-parallel wall clock)
        serial_lat = [pq["elapsed_ms"] for pq in per_q if pq.get("elapsed_ms")]
        if serial_lat:
            slat = sorted(serial_lat)
            summary["latency_serial_ms"] = {
                "n": len(slat),
                "p50": slat[len(slat)//2],
                "p95": slat[min(len(slat)-1, int(len(slat)*0.95))],
                "min": slat[0], "max": slat[-1],
            }
        # parallel
        async def one(q):
            body = {"q": q, "top_k": 5}
            t0 = time.monotonic()
            resp = await call(client, body)
            return int((time.monotonic() - t0) * 1000), len((resp.get("json") or {}).get("results") or [])
        t_par_start = time.monotonic()
        pres = await asyncio.gather(*(one(q) for q in PAR_QUERIES))
        wall = int((time.monotonic() - t_par_start) * 1000)
        summary["latency_parallel"] = {
            "n": len(PAR_QUERIES), "wall_ms": wall,
            "individual_ms_sorted": sorted([p[0] for p in pres]),
            "n_results": [p[1] for p in pres],
        }

        # --- (6) join key check
        # Run one schema query and capture hit IDs; cross-check against our local
        # doc_chunks `smifs_id` collection to see if IDs match.
        join = {"strategy": "compare hit.id (if any) to doc_chunks.smifs_id"}
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            mc = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = mc[os.environ["DB_NAME"]]
            sample_ids = []
            for sh in schema_hits:
                j = sh["resp"] if isinstance(sh["resp"], dict) else {}
                for h in (j.get("results") or [])[:3]:
                    if h.get("id"): sample_ids.append(h["id"])
            join["sample_hit_ids"] = sample_ids[:5]
            if sample_ids:
                n_match = await db.doc_chunks.count_documents({"smifs_id": {"$in": sample_ids}})
                join["matched_in_doc_chunks"] = n_match
                # Probe other potential join keys
                for k in ("doc_id", "_id"):
                    n = await db.doc_chunks.count_documents({k: {"$in": sample_ids}})
                    join[f"matched_via_{k}"] = n
            else:
                join["note"] = "no hits returned across schema queries — cannot verify join key"
            mc.close()
        except Exception as e:
            join["error"] = f"{type(e).__name__}: {e}"
        summary["join_key_check"] = join

        (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        print(json.dumps({k: summary[k] for k in ("envelope_keys","hit_keys","score_stats","latency_serial_ms","latency_parallel","join_key_check")}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
