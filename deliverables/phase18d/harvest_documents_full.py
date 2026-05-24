"""Phase 18d — harvest `documents_full` chunks from the deck.

Read-only. Issues a curated set of queries chosen to maximise `documents_full`
recall, dedupes by hit `id`, redacts PII (PAN, UCC, account/folio numbers,
phone numbers, email addresses) before persisting to
/app/deliverables/phase18d/documents_full_samples/.

Goal: ≥ 25 distinct chunks.
"""
from __future__ import annotations
import asyncio, os, json, re, sys
from pathlib import Path
import httpx

BASE = (os.environ.get("SMIFS_KNOWLEDGE_BASE_URL") or "").rstrip("/")
KEY  = os.environ.get("SMIFS_KNOWLEDGE_API_KEY") or ""
OUT  = Path("/app/deliverables/phase18d/documents_full_samples")
OUT.mkdir(parents=True, exist_ok=True)
HDR  = {"X-API-Key": KEY, "Content-Type": "application/json"}


# ---- PII redaction patterns ----------------------------------------------
# Match obvious patterns conservatively — false negatives possible but better
# than committing real client identifiers to /app/deliverables.
PII_PATTERNS = [
    # PAN (Indian Permanent Account Number): 5 letters + 4 digits + 1 letter
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), "[REDACTED_PAN]"),
    # UCC / Client codes: 6-12 alphanumeric, contextual — used in NSE/BSE
    (re.compile(r"\b(?:UCC|Client[ _-]?Code|Folio[ _-]?No\.?|Account[ _-]?No\.?)\s*[:\-]?\s*[A-Z0-9]{4,12}\b", re.IGNORECASE), "[REDACTED_UCC/FOLIO/ACCT]"),
    # Indian phone numbers (10 digits, optionally +91)
    (re.compile(r"\b(?:\+?91[-\s]?)?[6-9][0-9]{9}\b"), "[REDACTED_PHONE]"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    # IFSC: 4 letters + 0 + 6 alphanumeric
    (re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), "[REDACTED_IFSC]"),
    # Aadhaar (12 digits with optional spaces every 4)
    (re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b"), "[REDACTED_AADHAAR_OR_LONGNUM]"),
    # Generic 10+ digit number sequences (bank account, demat) — last guard
    (re.compile(r"\b\d{10,18}\b"), "[REDACTED_LONGNUM]"),
]


def redact(text: str) -> str:
    if not isinstance(text, str):
        return text
    out = text
    for pat, repl in PII_PATTERNS:
        out = pat.sub(repl, out)
    return out


def redact_deep(obj):
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_deep(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_deep(v) for k, v in obj.items()}
    return obj


QUERIES = [
    # Broad financial topics (likely to surface generic vehicle PDFs)
    "fund prospectus",
    "scheme information document",
    "investment strategy",
    "portfolio construction methodology",
    "risk factors disclosure",
    "expense ratio fee structure",
    "exit load redemption",
    "SEBI Category III AIF",
    "alternative investment fund regulations",
    "portfolio management services",
    # Product names that the 18c probe showed pulled documents_full
    "ICICI Prudential Office Yield Optimiser",
    "Aditya Birla Bluechip equity",
    "ASK Special Opportunities Portfolio",
    "Marcellus PMS strategy",
    "Carnelian Bharat Amritkaal",
    "ICICI Innovation Portfolio",
    "Motilal Oswal NTDOP",
    "Sapphire AIF Category II",
    "fund of fund series",
    "long short equity strategy",
    # Sales-pitch / commission / fee adjacent (testing for employee-only leakage)
    "commission structure for distributors",
    "ARN code transfer process",
    "sales pitch deck talking points",
    "competitor comparison fund houses",
    "client onboarding KYC documentation",
    # Growth-table adjacent (insurance / revenue dashboards)
    "insurance distributor providers",
    "growth revenue table FY26",
    "wealth manager incentive structure",
    # Multilingual probes
    "निवेश के अवसर",            # hi
    "AIF क्या होता है",         # hi
    "AIF க்கான தமிழ் pitch",     # ta
    "PMS திட்டம் என்ன",          # ta
    # Generic phrases to mop up tail-end coverage
    "performance benchmark NIFTY",
    "valuation methodology DCF",
    "credit rating tranche NCD",
    "secured non-convertible debenture",
    "lock-in period AIF Category II",
    "hedge against volatility",
    "real estate AIF investment thesis",
]


async def call(client, body):
    try:
        r = await client.post(f"{BASE}/api/knowledge/search", headers=HDR, json=body, timeout=30.0)
        if r.status_code == 200:
            return r.json()
        return {"results": []}
    except Exception as e:
        print(f"  ERR {type(e).__name__}: {e}", file=sys.stderr)
        return {"results": []}


def safe_filename(hit_id: str) -> str:
    """Build a deterministic filename from hit id. Replace ':' with '__'."""
    return hit_id.replace(":", "__").replace("/", "_")[:120] + ".json"


async def main():
    if not (BASE and KEY):
        print("ENV missing", file=sys.stderr); sys.exit(2)
    print(f"Harvesting documents_full chunks from {BASE} → {OUT}")

    seen_ids: dict[str, dict] = {}
    async with httpx.AsyncClient() as client:
        for i, q in enumerate(QUERIES, 1):
            body = {"q": q, "top_k": 10}
            j = await call(client, body)
            n_full = 0
            for hit in j.get("results", []) or []:
                if (hit.get("source") or "") != "documents_full":
                    continue
                hid = hit.get("id")
                if not hid or hid in seen_ids:
                    continue
                seen_ids[hid] = hit
                n_full += 1
            print(f"  [{i:2d}/{len(QUERIES)}] q={q[:42]:42s} → {n_full} new documents_full ({len(seen_ids)} total)")
            await asyncio.sleep(0.25)
            if len(seen_ids) >= 30:
                print(f"  (reached harvest cap of 30 distinct chunks; stopping queries early)")
                break

    print(f"\nHarvested {len(seen_ids)} distinct documents_full chunks. Redacting + persisting...")
    for hid, hit in seen_ids.items():
        red = redact_deep(hit)
        path = OUT / safe_filename(hid)
        path.write_text(json.dumps(red, indent=2, ensure_ascii=False))
    print(f"Wrote {len(seen_ids)} files to {OUT}/")
    print(f"\nID prefix distribution:")
    from collections import Counter
    print(Counter(x.split(":")[0] for x in seen_ids))
    # Also write a compact index
    idx = []
    for hid, hit in seen_ids.items():
        red = redact_deep(hit)
        idx.append({
            "id": hid,
            "sourceId": red.get("sourceId"),
            "title": red.get("title"),
            "section": red.get("section"),
            "score": red.get("score"),
            "vehicleId": (red.get("metadata") or {}).get("vehicleId"),
            "vehicleName": (red.get("metadata") or {}).get("vehicleName"),
            "vehicleType": (red.get("metadata") or {}).get("vehicleType"),
            "fileName": (red.get("metadata") or {}).get("fileName"),
            "ordinal": (red.get("metadata") or {}).get("ordinal"),
            "content_chars": len(red.get("content") or ""),
            "content_head": (red.get("content") or "")[:300],
        })
    (Path("/app/deliverables/phase18d") / "harvest_index.json").write_text(
        json.dumps(idx, indent=2, ensure_ascii=False)
    )
    print("Wrote /app/deliverables/phase18d/harvest_index.json")


if __name__ == "__main__":
    asyncio.run(main())
