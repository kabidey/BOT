"""Phase 16 — SMIFS Knowledge API probe.

Hits every plausible endpoint and dumps the response body to
`/app/deliverables/phase16/knowledge_api_probe/<endpoint>.json` so we can
diff field-by-field against what `knowledge_sync.py` currently consumes.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = (os.environ.get("SMIFS_KNOWLEDGE_BASE_URL") or "").rstrip("/")
KEY  = os.environ.get("SMIFS_KNOWLEDGE_API_KEY") or ""
OUT  = Path("/app/deliverables/phase16/knowledge_api_probe")
OUT.mkdir(parents=True, exist_ok=True)

HEADERS = {"X-API-Key": KEY, "Accept": "application/json"}

# Endpoints to probe — superset of what we currently call + likely additions.
ENDPOINTS = [
    ("GET", "/api/knowledge/stats", None, "stats"),
    ("GET", "/api/knowledge", {"limit": 3}, "list_limit3"),
    ("GET", "/api/knowledge", {"limit": 1, "offset": 0}, "list_first1"),
    ("GET", "/api/knowledge", {"limit": 1, "offset": 500}, "list_mid"),
    ("GET", "/api/knowledge", {"limit": 1, "offset": 1800}, "list_tail"),
    # plausible new endpoints
    ("GET", "/api/knowledge/search", {"q": "NCD"}, "search_ncd"),
    ("GET", "/api/knowledge/search", {"q": "AIF Cat III"}, "search_aif"),
    ("GET", "/api/knowledge/search", {"q": "PMS minimum corpus"}, "search_pms"),
    ("GET", "/api/knowledge/categories", None, "categories"),
    ("GET", "/api/knowledge/tags", None, "tags"),
    ("GET", "/api/knowledge/topics", None, "topics"),
    ("GET", "/api/knowledge/sources", None, "sources"),
    ("GET", "/api/knowledge/doc_types", None, "doc_types"),
    ("GET", "/api/knowledge/products", None, "products"),
    ("GET", "/api/knowledge/effective_dates", None, "effective_dates"),
    ("GET", "/api/knowledge/related", {"q": "AIF"}, "related_aif"),
    ("GET", "/api/knowledge/faq", None, "faq"),
    # versioning / health
    ("GET", "/api/knowledge/version", None, "version"),
    ("GET", "/api/knowledge/health", None, "health"),
    ("GET", "/api/openapi.json", None, "openapi"),
    ("GET", "/openapi.json", None, "openapi_root"),
    ("GET", "/api/docs", None, "docs_api"),
]


async def main() -> None:
    print(f"Probing {BASE} ({'KEY set' if KEY else 'NO KEY'})\n")
    if not BASE or not KEY:
        sys.exit("Missing env")
    summary = []
    async with httpx.AsyncClient(timeout=30) as c:
        for method, path, params, label in ENDPOINTS:
            url = f"{BASE}{path}"
            try:
                r = await c.request(method, url, headers=HEADERS, params=params or {})
                status = r.status_code
                ct = r.headers.get("content-type", "")
                body_text = r.text
                # Try to parse JSON
                try:
                    body_json = r.json()
                    sample = json.dumps(body_json, indent=2)[:4000]
                except Exception:
                    body_json = None
                    sample = body_text[:1500]
                out_path = OUT / f"{label}.json"
                if body_json is not None:
                    out_path.write_text(json.dumps(body_json, indent=2, ensure_ascii=False)[:120_000])
                else:
                    out_path.write_text(body_text[:120_000])
                bytes_len = len(body_text)
                summary.append({
                    "label": label, "path": path, "params": params,
                    "status": status, "content_type": ct, "bytes": bytes_len,
                    "json_parsed": body_json is not None,
                })
                print(f"  {status}  {path:38s} params={params!s:30s} -> {label}.json ({bytes_len}b, json={body_json is not None})")
            except Exception as e:
                summary.append({"label": label, "path": path, "error": repr(e)})
                print(f"  ERR  {path:38s} -> {e!r}")
    (OUT / "_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nSummary written to {OUT}/_summary.json")


if __name__ == "__main__":
    asyncio.run(main())
