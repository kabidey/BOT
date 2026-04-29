"""Diagnostic runner for chat quality regression.
Hits POST /api/agent/turn for each probe and prints reply, grounded, citations, model.
Also runs rag.search directly to show top-score per query (independent of LLM).
"""
import asyncio
import json
import os
import sys
import uuid
import httpx

API = "http://0.0.0.0:8001/api"

PROBES_RAG = [
    "Explain the difference between AIF Cat I, Cat II, and Cat III",
    "How are NCDs taxed in India? List long-term and short-term cases.",
    "What is the minimum investment for PMS at SMIFS?",
    "Tell me about KYC requirements for AIF investments",
    "Are NCDs secured? What's the difference between secured and unsecured NCDs?",
]

PROBES_SYNTH = [
    "Compare AIF and PMS for an HNI investor with 2 crore",
    "What are SMIFS's offerings for fixed-income investors?",
]

FOLLOWUP = [
    "What is an AIF?",
    "What's the minimum investment for that?",
]


async def call_turn(http: httpx.AsyncClient, sid: str, msg: str):
    r = await http.post(f"{API}/agent/turn", json={"session_id": sid, "message": msg}, timeout=60.0)
    r.raise_for_status()
    return r.json()


async def search_topscore(http: httpx.AsyncClient, q: str, k: int = 8):
    r = await http.post(f"{API}/rag/search", json={"query": q, "top_k": k}, timeout=30.0)
    r.raise_for_status()
    hits = r.json()
    return hits


def summarize(label: str, q: str, payload: dict, hits: list):
    blocks = payload.get("blocks", [])
    text = ""
    grounded = False
    for b in blocks:
        if b.get("type") == "text":
            text = b.get("text", "")
            grounded = bool(b.get("grounded"))
            break
    cits = payload.get("citations", [])
    intent = payload.get("intent")
    model = payload.get("model")
    print(f"\n[{label}] Q: {q}")
    print(f"  intent={intent} model={model} grounded={grounded} citations={len(cits)} chars={len(text)}")
    if hits:
        print(f"  top_search_scores={[round(h['score'],3) for h in hits[:5]]}")
        print(f"  top_doc/section={[(h['doc_id'], h['section']) for h in hits[:3]]}")
    print(f"  citations_doc_titles={[c.get('doc_title') for c in cits]}")
    print(f"  reply: {text[:400]}{'...' if len(text)>400 else ''}")
    return {"q": q, "intent": intent, "model": model, "grounded": grounded,
            "citations": len(cits), "chars": len(text),
            "top_scores": [round(h['score'],3) for h in hits[:5]] if hits else [],
            "citation_docs": sorted({c.get('doc_id') for c in cits}),
            "reply_text": text}


async def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "RUN"
    results = {"rag": [], "synth": [], "followup": []}
    async with httpx.AsyncClient() as http:
        # RAG probes
        for q in PROBES_RAG:
            sid = str(uuid.uuid4())
            hits = await search_topscore(http, q)
            payload = await call_turn(http, sid, q)
            results["rag"].append(summarize("RAG", q, payload, hits))

        # Synthesis probes
        for q in PROBES_SYNTH:
            sid = str(uuid.uuid4())
            hits = await search_topscore(http, q)
            payload = await call_turn(http, sid, q)
            results["synth"].append(summarize("SYN", q, payload, hits))

        # Conversational follow-up — same session
        sid = str(uuid.uuid4())
        for i, q in enumerate(FOLLOWUP):
            hits = await search_topscore(http, q)
            payload = await call_turn(http, sid, q)
            results["followup"].append(summarize(f"FUP-T{i+1}", q, payload, hits))

    out_path = f"/app/test_reports/diag_{label}.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n=> Wrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
