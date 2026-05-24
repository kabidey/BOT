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
from datetime import datetime, timedelta, timezone
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

# ---- Phase 11: background delta-sync scheduler ----
# How often to run a delta sync (seconds). 0 disables the scheduler.
KB_DELTA_SYNC_INTERVAL = int(os.environ.get("KB_DELTA_SYNC_INTERVAL_SECONDS", "900") or 0)
# Mongo-based mutex to keep manual + scheduled runs from colliding.
KB_SYNC_LOCK_ID = f"{KB_SOURCE}:sync_lock"
KB_SYNC_LOCK_TTL_SECONDS = 900  # lock expires after 15 min so a crashed run doesn't wedge scheduler
KB_RUNS_HISTORY_CAP = 200        # we keep most recent N rows in knowledge_sync_runs
_NEXT_SYNC_AT_ISO: Optional[str] = None


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


# ---------- Phase 16 — per-chunk metadata projector ----------
# Subsources whose content is for SMIFS employees only (internal sales scripts,
# revenue dashboards, insurance distribution matrices). The retrieval layer
# filters these out for clients and visitors.
_EMPLOYEE_ONLY_SUBSOURCES = {"sales_pitch", "growth_insurance", "growth_revenue"}

# Fields we deliberately DO NOT persist as top-level columns because they
# contain author email / internal identity (PII).
_PII_META_FIELDS = {"updatedBy"}


def _project_metadata(api_chunk: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 16 — extract Knowledge API per-chunk metadata we want as first-class
    columns on doc_chunks (vs buried in smifs_metadata).

    Recency-badge fallback chain (Phase 16.2):
      1. `metadata.updatedAt`    — exposed by `vehicle` (168) and `growth_*` (15) subsources.
      2. `metadata.createdAt`    — never seen in current payload, reserved if the API adds it.
      3. `metadata.generatedAt`  — `sales_pitch` (69) carries this (the date the AI pitch was generated).
      4. otherwise omit — UI then skips the "Updated <date>" badge rather than rendering "Updated unknown".

    Honest coverage NOTE: `document`, `academy`, and `bedrock` subsources (≈87% of the
    corpus) ship NO recency field at all from the SMIFS Knowledge API. We can't
    invent one. So in practice ~12–13% of citations carry a recency badge — every
    citation that has one is real; the rest are simply silent.

    - Pulls vehicle linkage, curation flags, version + recency proxies, and the
      growth/insurance vertical labels.
    - For `vehicle` subsource chunks (the parent vehicle row), the API does NOT
      populate `metadata.vehicleId/Name` because the chunk IS the vehicle —
      we derive these from the chunk's own `sourceId` + `title` so retrieval-time
      CTA chips fire on vehicle hits too.
    - Computes an `audience` tag used by retrieval-time role gating
      (`sales_pitch` / `growth_*` are employee-only).
    - Strips PII (updatedBy author email).
    """
    sub = api_chunk.get("source") or ""
    meta = api_chunk.get("metadata") or {}
    vehicle_id = meta.get("vehicleId")
    vehicle_name = meta.get("vehicleName")
    if sub == "vehicle" and not vehicle_id:
        # Parent vehicle chunk — backfill self-linkage from sourceId + title.
        vehicle_id = api_chunk.get("sourceId") or api_chunk.get("id")
        vehicle_name = vehicle_name or api_chunk.get("title")
    # Recency proxy with createdAt + generatedAt fallback (Phase 16.2)
    recency_iso = meta.get("updatedAt") or meta.get("createdAt") or meta.get("generatedAt")
    out: Dict[str, Any] = {
        "doc_type": sub,
        "vehicle_id": vehicle_id,
        "vehicle_name": vehicle_name,
        "vehicle_type": meta.get("vehicleType"),
        "is_active": meta.get("isActive"),
        "is_focused": meta.get("isFocused"),
        "sales_pitch_ready": meta.get("salesPitchReady"),
        "version_no": meta.get("versionNo"),
        "collateral_no": meta.get("collateralNo"),
        "kind": meta.get("kind"),
        "language": meta.get("language"),
        "provider": meta.get("provider"),
        "category": meta.get("category"),
        "vertical": meta.get("vertical"),
        "updated_at_iso": recency_iso,
        "audience": "employee_only" if sub in _EMPLOYEE_ONLY_SUBSOURCES else "all",
    }
    # Phase 16.1 — `version_no` arrives as a string ("v8.1", "v2.2"). Parse the
    # major version so the UI can gate "v<n>" badges on `>= 2` and the ranking
    # layer can sort versions numerically.
    v_raw = out.get("version_no")
    if isinstance(v_raw, str):
        import re as _re
        m = _re.match(r"v?(\d+)", v_raw.strip(), flags=_re.IGNORECASE)
        if m:
            try:
                out["version_major"] = int(m.group(1))
            except ValueError:
                pass
    elif isinstance(v_raw, (int, float)):
        out["version_major"] = int(v_raw)
    return {k: v for k, v in out.items() if v is not None}


def _scrubbed_smifs_metadata(api_chunk: Dict[str, Any]) -> Dict[str, Any]:
    """Drop PII keys (e.g. updatedBy) before persisting raw metadata blob."""
    meta = dict(api_chunk.get("metadata") or {})
    for pk in _PII_META_FIELDS:
        meta.pop(pk, None)
    return meta


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
        new_fields_counts: Dict[str, int] = {}
        for ch, vec in zip(to_embed, vecs):
            api_id = ch.get("id")
            ids_seen.add(api_id)
            text = ch.get("content") or ""
            projection = _project_metadata(ch)
            for k in projection:
                new_fields_counts[k] = new_fields_counts.get(k, 0) + 1
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
                "smifs_metadata": _scrubbed_smifs_metadata(ch),
                "content_hash": _content_hash(text),
                "updated_at": now,
                "source_updated_at": (ch.get("metadata") or {}).get("updatedAt"),
                **projection,
            }
            operations.append(UpdateOne({"_id": doc["_id"]}, {"$set": doc}, upsert=True))

        if operations:
            # Bulk-write in reasonable batches
            BATCH = 200
            for i in range(0, len(operations), BATCH):
                await db.doc_chunks.bulk_write(operations[i:i + BATCH], ordered=False)
        out["upserted"] = len(to_embed)
        out["new_fields_seen"] = new_fields_counts

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


async def status(db, *, include_runs: int = 5) -> Dict[str, Any]:
    """Counts + health for the admin KB tab.

    `include_runs` (1..50, default 5) caps how many recent rows from
    `knowledge_sync_runs` we surface in `last_run_summary`. Independently we
    ALWAYS pin the `phase16_backfill` run (if it exists) as
    `phase16_backfill_run` so the audit trail survives the rolling window.
    """
    counts_cursor = db.doc_chunks.aggregate([
        {"$group": {"_id": "$source", "count": {"$sum": 1}}},
    ])
    counts = {}
    async for row in counts_cursor:
        counts[row["_id"] or "unknown"] = row["count"]
    meta = await db.kb_sync_meta.find_one({"_id": KB_SOURCE}, {"_id": 0})
    # Phase 11 — scheduler info
    include_runs = max(1, min(int(include_runs or 5), 50))
    runs_cur = db.knowledge_sync_runs.find({}, {"_id": 0}).sort("started_at", -1).limit(include_runs)
    last_runs = await runs_cur.to_list(length=include_runs)
    # Phase 16 — pin the one-time full backfill run independent of the rolling
    # window, so admins can audit it weeks/months later.
    phase16_run = await db.knowledge_sync_runs.find_one(
        {"triggered_by": "phase16_backfill"}, {"_id": 0},
        sort=[("started_at", -1)],
    )
    return {
        "api_configured": configured(),
        "api_reachable": await probe_reachable(),
        "counts_by_source": counts,
        "total_smifs_chunks": counts.get(KB_SOURCE, 0),
        "total_seed_chunks": counts.get("seed", 0),
        "total_uploaded_chunks": counts.get("upload", 0),
        "total_archive_chunks": counts.get("session_archive", 0),
        "last_sync": meta or {},
        # Phase 11 — scheduler visibility
        "auto_sync_enabled": KB_DELTA_SYNC_INTERVAL > 0,
        "auto_sync_interval_seconds": KB_DELTA_SYNC_INTERVAL,
        "next_scheduled_sync_at": _NEXT_SYNC_AT_ISO,
        "last_run_summary": last_runs,
        "last_run_summary_window": include_runs,
        # Phase 16 — pinned backfill audit (None if backfill has not yet run)
        "phase16_backfill_run": phase16_run,
    }


async def _acquire_mutex(db) -> bool:
    """Try to acquire the sync mutex. Returns True on success, False if
    another run is still holding it. A stale lock (older than TTL) is
    forcibly reclaimed."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(seconds=KB_SYNC_LOCK_TTL_SECONDS)).isoformat()
    from pymongo.errors import DuplicateKeyError  # type: ignore
    try:
        await db.kb_sync_locks.insert_one({
            "_id": KB_SYNC_LOCK_ID, "acquired_at": now_iso, "expires_at": expires_iso,
        })
        return True
    except DuplicateKeyError:
        existing = await db.kb_sync_locks.find_one({"_id": KB_SYNC_LOCK_ID})
        if existing and existing.get("expires_at", "") < now_iso:
            await db.kb_sync_locks.replace_one(
                {"_id": KB_SYNC_LOCK_ID},
                {"_id": KB_SYNC_LOCK_ID, "acquired_at": now_iso, "expires_at": expires_iso,
                 "stolen_from": existing.get("acquired_at")},
            )
            logger.warning("SMIFS KB sync: stole stale lock acquired_at=%s", existing.get("acquired_at"))
            return True
        return False


async def _release_mutex(db) -> None:
    try:
        await db.kb_sync_locks.delete_one({"_id": KB_SYNC_LOCK_ID})
    except Exception:
        pass


async def _record_run(db, run_doc: Dict[str, Any]) -> None:
    try:
        await db.knowledge_sync_runs.insert_one(run_doc)
        # Trim history to cap
        count = await db.knowledge_sync_runs.count_documents({})
        if count > KB_RUNS_HISTORY_CAP:
            cur = db.knowledge_sync_runs.find({}, {"_id": 1}).sort("started_at", 1).limit(count - KB_RUNS_HISTORY_CAP)
            old_ids = [d["_id"] async for d in cur]
            if old_ids:
                await db.knowledge_sync_runs.delete_many({"_id": {"$in": old_ids}})
    except Exception as e:
        logger.warning("knowledge_sync_runs insert failed: %s", e)


async def run_sync(db, *, mode: str = "delta", dry_run: bool = False,
                   trigger: str = "manual") -> Dict[str, Any]:
    """Phase 11 wrapper around `sync()` that adds mutex + run history.

    Returns the same shape as `sync()` plus `trigger` + `conflict` flags.
    """
    t_start = datetime.now(timezone.utc)
    lock = await _acquire_mutex(db)
    if not lock:
        return {
            "mode": mode, "dry_run": dry_run, "trigger": trigger,
            "conflict": True, "started_at": t_start.isoformat(),
            "finished_at": t_start.isoformat(),
            "fetched": 0, "upserted": 0, "skipped": 0, "removed": 0,
            "errors": ["another sync is in progress"],
        }
    try:
        result = await sync(db, mode=mode, dry_run=dry_run)
    finally:
        await _release_mutex(db)
    result["trigger"] = trigger
    result["conflict"] = False
    try:
        duration_ms = int((datetime.now(timezone.utc) - t_start).total_seconds() * 1000)
        await _record_run(db, {
            "started_at": result.get("started_at"),
            "finished_at": result.get("finished_at"),
            "mode": result.get("mode"),
            "dry_run": result.get("dry_run"),
            "trigger": trigger,
            "triggered_by": trigger,
            "fetched": result.get("fetched", 0),
            "upserted": result.get("upserted", 0),
            "skipped": result.get("skipped", 0),
            "removed": result.get("removed", 0),
            "errors": result.get("errors", []),
            "duration_ms": duration_ms,
            "new_fields_seen": result.get("new_fields_seen") or {},
        })
    except Exception:
        logger.exception("knowledge_sync_runs record failed (non-fatal)")
    return result


async def delta_sync_loop(db) -> None:
    """Phase 11 — long-running background task: every KB_DELTA_SYNC_INTERVAL
    seconds (±jitter), run a delta sync. Set the env var to 0 to disable.
    Idempotent via the mutex."""
    global _NEXT_SYNC_AT_ISO
    if KB_DELTA_SYNC_INTERVAL <= 0:
        logger.info("SMIFS KB delta-sync scheduler DISABLED (KB_DELTA_SYNC_INTERVAL_SECONDS=0)")
        return
    if not configured():
        logger.info("SMIFS KB delta-sync scheduler skipped — env not configured")
        return
    logger.info("SMIFS KB delta-sync scheduler ACTIVE — interval=%ds", KB_DELTA_SYNC_INTERVAL)
    import datetime as _dt  # noqa: F401 (kept for clarity; not used directly)
    while True:
        # Jitter ±60s; never less than 30s between runs.
        jitter = random.randint(-60, 60)
        wait_s = max(30, KB_DELTA_SYNC_INTERVAL + jitter)
        _NEXT_SYNC_AT_ISO = (datetime.now(timezone.utc) + timedelta(seconds=wait_s)).isoformat()
        try:
            await asyncio.sleep(wait_s)
        except asyncio.CancelledError:
            logger.info("SMIFS KB delta-sync scheduler cancelled — shutting down loop")
            return
        try:
            await run_sync(db, mode="delta", trigger="scheduler")
        except Exception:
            logger.exception("SMIFS KB scheduler sync failed (non-fatal — will retry)")


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


async def phase16_backfill_if_needed(db) -> None:
    """Phase 16 — one-time `mode='full'` backfill so all already-indexed chunks
    pick up the new per-subsource metadata projection (vehicle_id, version_no,
    is_focused/active, audience, …).

    Gated on the `phase16_backfilled` flag in `kb_sync_meta` so we never run
    this more than once per environment.
    """
    if not configured():
        return
    meta = await db.kb_sync_meta.find_one({"_id": KB_SOURCE}, {"_id": 0}) or {}
    if meta.get("phase16_backfilled"):
        logger.info("Phase 16 backfill already complete — skipping")
        return
    # Skip backfill if the index is empty — `startup_sync_if_empty` will
    # handle initial population and the projector runs on every chunk anyway.
    n = await db.doc_chunks.count_documents({"source": KB_SOURCE})
    if n == 0:
        logger.info("Phase 16 backfill skipped — index empty (initial sync will project metadata)")
        return
    logger.info("Phase 16 backfill — running mode=full with trigger=phase16_backfill (%d chunks)", n)
    try:
        result = await run_sync(db, mode="full", trigger="phase16_backfill")
        # Only mark the flag on a successful, non-conflicting run that actually
        # upserted chunks. A conflict (stale mutex from a previous interrupted
        # run) returns upserted=0 — retry on next startup.
        if result.get("conflict") or result.get("errors") or (result.get("upserted", 0) == 0 and result.get("fetched", 0) > 0):
            logger.warning("Phase 16 backfill did NOT mark flag (conflict/error/zero upsert): %s",
                           {k: result.get(k) for k in ("conflict", "errors", "upserted", "fetched")})
            return
        await db.kb_sync_meta.update_one(
            {"_id": KB_SOURCE},
            {"$set": {
                "phase16_backfilled": True,
                "phase16_backfilled_at": datetime.now(timezone.utc).isoformat(),
                "phase16_new_fields_seen": result.get("new_fields_seen") or {},
            }},
            upsert=True,
        )
        logger.info("Phase 16 backfill complete: upserted=%d new_fields_seen=%s",
                    result.get("upserted", 0), result.get("new_fields_seen") or {})
    except Exception:
        logger.exception("Phase 16 backfill failed (non-fatal — will retry on next startup)")
