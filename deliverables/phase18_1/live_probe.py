"""Phase 18.1 — live-deck integration probe.

Calls deck_search() through the real (live) backend module with a query
that DEFINITELY produces deck hits. Dumps the post-enrichment + post-
audience-gate hit envelope to prove:

  * source_engine = "deck_search"
  * relevance = float score
  * audience pulled from local enrichment (when join lands)
  * vehicle_id / vehicle_name / version_no / is_focused / updated_at_iso
    populated for vehicle-type hits
  * employee_only sources dropped for visitor sessions
  * full pipeline (timeout / slow-warning / telemetry / audience drop)
    fires correctly
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from agents import deck_search


async def main():
    mc = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mc[os.environ["DB_NAME"]]

    out_dir = Path("/app/deliverables/phase18_1")
    out_dir.mkdir(parents=True, exist_ok=True)

    cases = [
        # (label, query, session_type, auth_state)
        ("01_employee_full_access", "Alchemy Ascent strategy details", "employee", "verified"),
        ("02_visitor_prefilter", "Bharat Value Fund Series VI", "visitor", "anonymous"),
        ("03_client_audience_gate", "Aditya Birla insurance", "client", "verified"),
        ("04_multilingual_tamil", "AIF க்கான தமிழ் pitch", "employee", "verified"),
    ]
    results = {}
    for label, q, st, auth in cases:
        print(f"\n=== {label}: q={q!r}  session_type={st}  auth_state={auth} ===")
        hits = await deck_search.deck_search(
            q, top_k=5, db=db, session_type=st, auth_state=auth, locale="en",
        )
        if not hits:
            print("  (no hits returned — likely filtered by score floor or audience gate)")
            results[label] = {"q": q, "session_type": st, "auth_state": auth,
                              "hits": [], "n": 0}
            continue
        # Render the FE-shape citation envelope per hit
        compact = []
        for h in hits[:3]:
            row = {
                "doc_id": h["doc_id"],
                "doc_title": h["doc_title"],
                "section": h["section"],
                "subsource": h.get("subsource"),
                "source_raw_deck": h.get("source_raw"),
                "score": h["score"],
                "relevance": h.get("relevance"),
                "source_engine": h.get("source_engine"),
                "audience": h.get("audience"),
                "vehicle_id": h.get("vehicle_id"),
                "vehicle_name": h.get("vehicle_name"),
                "vehicle_type": h.get("vehicle_type"),
                "version_no": h.get("version_no"),
                "is_focused": h.get("is_focused"),
                "is_active": h.get("is_active"),
                "updated_at_iso": h.get("updated_at_iso"),
                "text_preview": (h.get("text") or "")[:120],
            }
            compact.append(row)
        for r in compact:
            print(json.dumps(r, ensure_ascii=False, indent=2)[:1200])
        results[label] = {"q": q, "session_type": st, "auth_state": auth,
                          "n": len(hits), "hits": compact}

    # Telemetry snapshot
    snap = deck_search.status()
    snap["dumped_at"] = datetime.now(timezone.utc).isoformat()
    print("\n=== status() snapshot ===")
    print(json.dumps({k: v for k, v in snap.items() if k != "last_10_calls"}, indent=2))
    print(f"\nlast {len(snap['last_10_calls'])} calls (ring buffer):")
    for c in snap["last_10_calls"]:
        print(" ", c)

    # Persist deliverable
    (out_dir / "live_probe.json").write_text(
        json.dumps({"results": results, "status_snapshot": snap}, indent=2, ensure_ascii=False)
    )
    print(f"\nWrote {out_dir}/live_probe.json")
    mc.close()


if __name__ == "__main__":
    asyncio.run(main())
