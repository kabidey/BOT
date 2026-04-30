"""Phase 9 — SMIFS Knowledge API ingestion pipeline.

The SMIFS Knowledge API (deck.pesmifs.com/api/knowledge) is the PRIMARY corpus
for all Mackertich ONE product / offering answers. Chunks already arrive
pre-chunked by the source — we preserve the chunk boundaries as-is, embed each
chunk with Hub AI text-embedding-3-small, and upsert into `doc_chunks` with
`source="smifs_knowledge"` + full metadata.

Key design: idempotent. Each API chunk has a stable `id`. We track a SHA-1
content hash; if the content is byte-for-byte identical on resync, we skip
re-embed (cheap). If content changed, we delete + re-embed that chunk.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

import rag

logger = logging.getLogger(__name__)

KB_BASE_URL = (os.environ.get("SMIFS_KNOWLEDGE_BASE_URL") or "").rstrip("/")
KB_API_KEY = os.environ.get("SMIFS_KNOWLEDGE_API_KEY") or ""
KB_SOURCE = "smifs_knowledge"
KB_DOC_PREFIX = "smifs_kb_"

# Conservative client-side rate — probed ~10 rps comfortably without 429.
KB_MAX_RPS = 10
KB_PAGE_LIMIT = 2000  # API returns all 1801 chunks in one shot
KB_HTTP_TIMEOUT = 60


def _headers() -> Dict[str, str]:
    # Never log this header literal; hardening.SecretScrubFilter already masks.
    return {"X-API-Key": KB_API_KEY, "Accept": "application/json"}


def configured() -> bool:
    return bool(KB_BASE_URL and KB_API_KEY)


class KnowledgeAPIError(Exception):
    pass


async def _get(path: str, params: Optional[Dict[str, Any]] = None,
               retries: int = 3) -> Dict[str, Any]:
    """GET with exponential backoff on 429/5xx."""
    if not configured():
        raise KnowledgeAPIError("SMIFS_KNOWLEDGE env not configured")
    url = f"{KB_BASE_URL}{path}"
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=KB_HTTP_TIMEOUT) as c:
                r = await c.get(url, headers=_headers(), params=params or {})
            if r.status_code == 401:
                raise KnowledgeAPIError(f"SMIFS KB 401 — bad API key on {path}")
            if r.status_code == 404:
                raise KnowledgeAPIError(f"SMIFS KB 404 on {path}")
            if r.status_code == 429 or r.status_code >= 500:
                raise KnowledgeAPIError(f"{r.status_code} on {path}")
            r.raise_for_status()
            return r.json() or {}
        except KnowledgeAPIError as e:
            last_exc = e
            if "401" in str(e) or "404" in str(e):
                raise  # don't retry auth / not-found
            if attempt < retries:
                delay = (0.25 * (3 ** attempt)) + random.uniform(0, 0.3)
                logger.warning("SMIFS KB %s — retrying in %.2fs (attempt %d)", e, delay, attempt + 1)
                await asyncio.sleep(delay)
                continue
            raise
        except Exception as e:
            last_exc = e
            if attempt < retries:
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            raise
    raise last_exc or KnowledgeAPIError("unknown failure")


# -------- HTTP wrappers --------
async def fetch_stats() -> Dict[str, Any]:
    return await _get("/api/knowledge/stats")


async def fetch_all_chunks() -> List[Dict[str, Any]]:
    """Single-shot pull (API returns full corpus up to limit=2000)."""
    data = await _get("/api/knowledge", {"limit": KB_PAGE_LIMIT})
    return data.get("chunks") or []


async def probe_reachable() -> bool:
    """Light health check — avoid fetching 1801 chunks just to ping."""
    if not configured():
        return False
    try:
        await _get("/api/knowledge/stats")
        return True
    except Exception as e:
        logger.warning("SMIFS KB probe failed: %s", e)
        return False


# -------- Ingest --------
def _content_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def _kb_chunk_id(api_id: str) -> str:
    # Our internal _id — prefixed so we can grep across the index
    return f"{KB_DOC_PREFIX}{api_id}"


def _doc_id_for(api_chunk: Dict[str, Any]) -> str:
    # Use the source-level grouping so citations aggregate per logical doc
    sid = api_chunk.get("sourceId") or api_chunk.get("id")
    return f"{KB_DOC_PREFIX}{sid}"


def _section_for(api_chunk: Dict[str, Any]) -> str:
    # academy gives us chapterTitle + pageTitle; prefer that for readability
    meta = api_chunk.get("metadata") or {}
    if api_chunk.get("source") == "academy":
        parts = [p for p in (meta.get("chapterTitle"), meta.get("pageTitle")) if p]
        if parts:
            return " › ".join(parts)
    if api_chunk.get("source") == "document":
        return meta.get("fileName") or api_chunk.get("section") or ""
    return api_chunk.get("section") or ""


def _doc_title_for(api_chunk: Dict[str, Any]) -> str:
    meta = api_chunk.get("metadata") or {}
    t = api_chunk.get("title") or ""
    if api_chunk.get("source") == "academy" and meta.get("courseTitle"):
        return f"{meta['courseTitle']} · {t}"
    return t


async def sync(db, *, mode: str = "delta", dry_run: bool = False) -> Dict[str, Any]:
    """Sync SMIFS Knowledge into doc_chunks.

    mode='full'  → delete all existing smifs_knowledge chunks, re-embed every chunk.
    mode='delta' → upsert only new / changed chunks; remove chunks that disappeared upstream.
    dry_run=True → do everything EXCEPT the final embed + persist; return a preview.
    """
    t0 = datetime.now(timezone.utc)
    out: Dict[str, Any] = {
        "mode": mode, "dry_run": dry_run, "started_at": t0.isoformat(),
        "fetched": 0, "upserted": 0, "skipped": 0, "removed": 0, "errors": [],
        "preview": [],
    }
    if not configured():
        out["errors"].append("SMIFS_KNOWLEDGE env not set")
        return out

    try:
        chunks = await fetch_all_chunks()
    except Exception as e:
        out["errors"].append(f"fetch failed: {e!r}")
        return out
    out["fetched"] = len(chunks)

    # Build id → content_hash map of what's already on disk
    existing = {}
    cursor = db.doc_chunks.find({"source": KB_SOURCE}, {"_id": 1, "smifs_id": 1, "content_hash": 1})
    async for row in cursor:
        existing[row.get("smifs_id") or row.get("_id")] = row.get("content_hash")

    if mode == "full":
        to_embed = chunks
    else:  # delta
        to_embed = []
        for ch in chunks:
            api_id = ch.get("id")
            text = ch.get("content") or ""
            h = _content_hash(text)
            if existing.get(api_id) == h:
                out["skipped"] += 1
                continue
            to_embed.append(ch)

    for ch in to_embed[:5]:
        out["preview"].append({
            "id": ch.get("id"),
            "source": ch.get("source"),
            "title": ch.get("title"),
            "section": ch.get("section"),
            "content_len": len(ch.get("content") or ""),
        })

    if dry_run:
        out["finished_at"] = datetime.now(timezone.utc).isoformat()
        return out

    # Embed + upsert
    if to_embed:
        texts = [c.get("content") or "" for c in to_embed]
        try:
            vecs, embedder = await rag.embed_texts(texts)
        except Exception as e:
            out["errors"].append(f"embed failed: {e!r}")
            return out
        out["embedder"] = embedder
        now = datetime.now(timezone.utc).isoformat()
        operations = []
        from pymongo import UpdateOne, DeleteMany  # local import
        # Delete all chunks in affected doc_ids first (so re-chunks stay consistent)
        if mode == "full":
            await db.doc_chunks.delete_many({"source": KB_SOURCE})

        ids_seen = set()
        for ch, vec in zip(to_embed, vecs):
            api_id = ch.get("id")
            ids_seen.add(api_id)
            text = ch.get("content") or ""
            doc = {
                "_id": _kb_chunk_id(api_id),
                "smifs_id": api_id,
                "source": KB_SOURCE,
                "subsource": ch.get("source"),
                "doc_id": _doc_id_for(ch),
                "doc_title": _doc_title_for(ch),
                "section": _section_for(ch),
                "text": text,
                "embedding": vec.tolist(),
                "smifs_metadata": ch.get("metadata") or {},
                "content_hash": _content_hash(text),
                "updated_at": now,
                "source_updated_at": (ch.get("metadata") or {}).get("updatedAt"),
            }
            operations.append(UpdateOne({"_id": doc["_id"]}, {"$set": doc}, upsert=True))

        if operations:
            # Bulk-write in reasonable batches
            BATCH = 200
            for i in range(0, len(operations), BATCH):
                await db.doc_chunks.bulk_write(operations[i:i + BATCH], ordered=False)
        out["upserted"] = len(to_embed)

    # Remove stale chunks (disappeared from upstream)
    if mode == "delta":
        upstream_ids = {c.get("id") for c in chunks}
        to_delete = [k for k in existing.keys() if k not in upstream_ids]
        if to_delete:
            await db.doc_chunks.delete_many({"source": KB_SOURCE, "smifs_id": {"$in": to_delete}})
            out["removed"] = len(to_delete)

    # Persist sync-meta so admin status can show last_sync_at
    await db.kb_sync_meta.update_one(
        {"_id": KB_SOURCE},
        {"$set": {"last_sync_at": datetime.now(timezone.utc).isoformat(),
                  "last_mode": mode, "last_result": {k: out[k] for k in ("fetched", "upserted", "skipped", "removed")}}},
        upsert=True,
    )

    # Rebuild in-memory index
    try:
        await rag.reload_index_from_db(db)
    except Exception as e:
        out["errors"].append(f"index reload: {e!r}")

    out["finished_at"] = datetime.now(timezone.utc).isoformat()
    logger.info("SMIFS KB sync done: mode=%s fetched=%d upserted=%d skipped=%d removed=%d",
                mode, out["fetched"], out["upserted"], out["skipped"], out["removed"])
    return out


async def status(db) -> Dict[str, Any]:
    """Counts + health for the admin KB tab."""
    counts_cursor = db.doc_chunks.aggregate([
        {"$group": {"_id": "$source", "count": {"$sum": 1}}},
    ])
    counts = {}
    async for row in counts_cursor:
        counts[row["_id"] or "unknown"] = row["count"]
    meta = await db.kb_sync_meta.find_one({"_id": KB_SOURCE}, {"_id": 0})
    return {
        "api_configured": configured(),
        "api_reachable": await probe_reachable(),
        "counts_by_source": counts,
        "total_smifs_chunks": counts.get(KB_SOURCE, 0),
        "total_seed_chunks": counts.get("seed", 0),
        "total_uploaded_chunks": counts.get("upload", 0),
        "total_archive_chunks": counts.get("session_archive", 0),
        "last_sync": meta or {},
    }


async def startup_sync_if_empty(db) -> None:
    """Run a full sync in background on startup if the index has no SMIFS chunks."""
    if not configured():
        logger.info("SMIFS KB sync skipped — env not configured")
        return
    n = await db.doc_chunks.count_documents({"source": KB_SOURCE})
    if n > 0:
        logger.info("SMIFS KB index present (%d chunks) — skipping auto-sync", n)
        return
    logger.info("SMIFS KB index empty — kicking off full sync")
    try:
        result = await sync(db, mode="full")
        logger.info("SMIFS KB startup sync: %s", {k: result.get(k) for k in ("fetched", "upserted", "skipped", "errors")})
    except Exception as e:
        logger.exception("SMIFS KB startup sync failed: %s", e)
