"""Phase 16 — sample 1 chunk from each `source` value to enumerate metadata
shapes per subsource. Also try common filter params to see what server-side
filtering the API supports.
"""
from __future__ import annotations
import asyncio, json, os
from pathlib import Path
import httpx
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

BASE = os.environ["SMIFS_KNOWLEDGE_BASE_URL"].rstrip("/")
HDR  = {"X-API-Key": os.environ["SMIFS_KNOWLEDGE_API_KEY"], "Accept": "application/json"}
OUT  = Path("/app/deliverables/phase16/knowledge_api_probe")


async def main() -> None:
    async with httpx.AsyncClient(timeout=30) as c:
        # Try filter params
        for params, label in [
            ({"source": "academy", "limit": 1}, "filt_source_academy"),
            ({"source": "document", "limit": 1}, "filt_source_document"),
            ({"source": "bedrock", "limit": 1}, "filt_source_bedrock"),
            ({"source": "growth_insurance", "limit": 1}, "filt_source_growth_insurance"),
            ({"source": "growth_revenue", "limit": 1}, "filt_source_growth_revenue"),
            ({"source": "sales_pitch", "limit": 1}, "filt_source_sales_pitch"),
            ({"source": "vehicle", "limit": 1}, "filt_source_vehicle"),
            ({"q": "NCD", "limit": 3}, "filt_q_ncd"),
            ({"search": "NCD", "limit": 3}, "filt_search_ncd"),
            ({"query": "NCD", "limit": 3}, "filt_query_ncd"),
            ({"vehicleType": "AIF", "limit": 1}, "filt_vehicleType_aif"),
            ({"section": "PMS", "limit": 1}, "filt_section_pms"),
        ]:
            r = await c.get(f"{BASE}/api/knowledge", headers=HDR, params=params)
            (OUT / f"{label}.json").write_text(r.text[:30000])
            try:
                data = r.json()
                chunks = data.get("chunks", [])
                first = chunks[0] if chunks else None
                src_of_first = (first or {}).get("source") if first else None
                print(f"  {r.status_code}  params={params!s:60s} -> {len(chunks)} chunks, first.source={src_of_first}")
            except Exception:
                print(f"  {r.status_code}  params={params!s:60s} -> non-JSON {r.text[:80]}")

        # Now sweep offsets to find boundaries of each subsource
        OUT_SAMPLES = OUT / "by_subsource"
        OUT_SAMPLES.mkdir(parents=True, exist_ok=True)
        seen_subsources: dict[str, dict] = {}
        for offset in (0, 100, 200, 300, 700, 800, 1000, 1100, 1290, 1500, 1700, 1800, 1900):
            r = await c.get(f"{BASE}/api/knowledge", headers=HDR, params={"limit": 1, "offset": offset})
            try:
                ch = (r.json().get("chunks") or [None])[0]
                if ch:
                    s = ch.get("source")
                    if s and s not in seen_subsources:
                        seen_subsources[s] = ch
                    print(f"  offset={offset:5d} -> source={ch.get('source'):20s} sourceId={(ch.get('sourceId') or '')[:30]!r}")
            except Exception as e:
                print(f"  offset={offset:5d} -> err {e}")
        for s, ch in seen_subsources.items():
            (OUT_SAMPLES / f"{s}.json").write_text(json.dumps(ch, indent=2))
        print(f"\nDistinct subsources sampled: {sorted(seen_subsources)}")


if __name__ == "__main__":
    asyncio.run(main())
