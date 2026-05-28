"""Phase 27 — Admin-triggered re-embed of `doc_chunks` to the active
HUB_EMBED_MODEL (text-embedding-3-large @ 3072 dim).

This mirrors `scripts/reembed_doc_chunks.py` but runs INSIDE the FastAPI
process so it can be triggered from outside a sandboxed production container
via `POST /api/admin/reembed/run`.

Design choices:
  * Resumable via the same `reembed_progress` checkpoint collection the
    CLI script uses (so a half-run CLI migration can be resumed via the
    endpoint and vice-versa).
  * Per-job audit row in `reembed_jobs` so the admin can poll status.
  * Hub AI key/base URL come from the same env vars rag.py uses.
  * On completion (non-dry-run), rebuilds the in-memory index and clears
    the dim-mismatch log flag.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# Pricing source: OpenAI text-embedding-3-large = $0.13 / 1M tokens.
NEW_MODEL = os.environ.get("HUB_EMBED_MODEL", "text-embedding-3-large")
NEW_DIM = int(os.environ.get("HUB_EMBED_DIM", "3072"))
PRICE_PER_M_TOKENS = float(os.environ.get("HUB_EMBED_PRICE_PER_M", "0.13"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def _query_filter() -> Dict[str, Any]:
    """Chunks needing re-embed: missing model field, wrong model, or wrong dim."""
    return {
        "$or": [
            {"embedding_model": {"$exists": False}},
            {"embedding_model": {"$ne": NEW_MODEL}},
            {"embedding_dim": {"$exists": False}},
            {"embedding_dim": {"$ne": NEW_DIM}},
        ]
    }


async def estimate(db) -> Dict[str, Any]:
    """Count chunks needing re-embed + total chunks + cost estimate."""
    needs_cursor = db.doc_chunks.find(_query_filter(), {"_id": 1, "text": 1})
    needs_raw = await needs_cursor.to_list(length=None)
    total_chunks = await db.doc_chunks.count_documents({})
    chunks_on_target = await db.doc_chunks.count_documents({
        "embedding_model": NEW_MODEL, "embedding_dim": NEW_DIM,
    })

    # Honour the existing resume checkpoint (shared with the CLI script) so
    # estimate matches what a kickoff would actually process.
    ckpt = await db.reembed_progress.find_one(
        {"_id": "doc_chunks_to_3large"}) or {}
    done_ids = set(ckpt.get("done_ids") or [])
    needs = [d for d in needs_raw if d["_id"] not in done_ids]

    token_est = sum(_estimate_tokens(d.get("text") or "") for d in needs)
    usd_est = round(token_est / 1_000_000 * PRICE_PER_M_TOKENS, 4)

    # Distribution of current dims (helps diagnose the prod 1536 vs 3072 split).
    dim_pipeline = [
        {"$group": {"_id": {"dim": "$embedding_dim", "model": "$embedding_model"},
                    "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    by_dim: List[Dict[str, Any]] = []
    async for doc in db.doc_chunks.aggregate(dim_pipeline):
        key = doc.get("_id") or {}
        by_dim.append({
            "embedding_dim": key.get("dim"),
            "embedding_model": key.get("model"),
            "count": doc.get("count"),
        })

    return {
        "target_model": NEW_MODEL,
        "target_dim": NEW_DIM,
        "total_chunks": total_chunks,
        "chunks_on_target": chunks_on_target,
        "chunks_to_process_raw": len(needs_raw),
        "chunks_already_checkpointed": len(done_ids & {d["_id"] for d in needs_raw}),
        "chunks_to_process": len(needs),
        "estimated_tokens": token_est,
        "estimated_usd": usd_est,
        "by_dim": by_dim,
        "price_per_m_tokens_usd": PRICE_PER_M_TOKENS,
        "checkpoint_completed": bool(ckpt.get("completed")),
    }


async def _embed_batch_hub(http: httpx.AsyncClient, key: str, base: str,
                           texts: List[str]) -> Tuple[List[List[float]], Dict[str, Any]]:
    """One Hub AI /embeddings call. Returns (vectors, usage_meta)."""
    resp = await http.post(
        f"{base}/embeddings",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": NEW_MODEL, "input": texts},
        timeout=60.0,
    )
    resp.raise_for_status()
    body = resp.json()
    items = body.get("data") or []
    vecs = [it["embedding"] for it in items]
    usage = body.get("usage") or {}
    return vecs, {"prompt_tokens": usage.get("prompt_tokens"),
                  "total_tokens": usage.get("total_tokens")}


async def _set_job(db, job_id: str, **fields) -> None:
    fields["last_updated_at"] = _now_iso()
    await db.reembed_jobs.update_one(
        {"job_id": job_id},
        {"$set": fields},
        upsert=True,
    )


async def get_status(db, job_id: str) -> Optional[Dict[str, Any]]:
    return await db.reembed_jobs.find_one({"job_id": job_id}, {"_id": 0})


async def list_jobs(db, limit: int = 20) -> List[Dict[str, Any]]:
    cursor = db.reembed_jobs.find({}, {"_id": 0}).sort([("started_at", -1)]).limit(limit)
    return [d async for d in cursor]


async def run_job(db, job_id: str, *, dry_run: bool, batch_size: int,
                  max_chunks: Optional[int], purge_legacy: bool) -> None:
    """Background runner. Updates `reembed_jobs[job_id]` as it progresses.
    Never raises — catches everything and records `status="failed"` instead.
    """
    try:
        key = os.environ.get("LLMHUB_API_KEY", "")
        base = (os.environ.get("LLMHUB_BASE_URL") or "").rstrip("/")
        if not (key and base):
            await _set_job(db, job_id, status="failed",
                           finished_at=_now_iso(),
                           error="LLMHUB_API_KEY or LLMHUB_BASE_URL missing")
            return

        # Re-read checkpoint (lets the CLI + endpoint share progress).
        ckpt = await db.reembed_progress.find_one(
            {"_id": "doc_chunks_to_3large"}) or {}
        done_ids = set(ckpt.get("done_ids") or [])

        needs_cursor = db.doc_chunks.find(_query_filter(), {"_id": 1, "text": 1})
        needs_all = await needs_cursor.to_list(length=None)
        needs = [d for d in needs_all if d["_id"] not in done_ids]
        if max_chunks is not None and max_chunks > 0:
            needs = needs[:max_chunks]
        total = len(needs)
        await _set_job(db, job_id,
                       chunks_total=total,
                       chunks_processed=0,
                       status="running")

        if dry_run:
            token_est = sum(_estimate_tokens(d.get("text") or "") for d in needs)
            usd_est = round(token_est / 1_000_000 * PRICE_PER_M_TOKENS, 4)
            purge_count = 0
            if purge_legacy:
                purge_count = await db.doc_chunks.count_documents({
                    "$or": [{"embedding_dim": {"$ne": NEW_DIM}},
                            {"embedding_model": {"$ne": NEW_MODEL}}],
                })
            await _set_job(db, job_id,
                           status="completed",
                           finished_at=_now_iso(),
                           dry_run=True,
                           estimated_tokens=token_est,
                           estimated_usd=usd_est,
                           would_purge_count=purge_count,
                           tokens_used=0,
                           usd_spent=0.0)
            return

        processed = 0
        tokens_used = 0
        failures = 0
        if total > 0:
            async with httpx.AsyncClient() as http:
                for start in range(0, total, batch_size):
                    batch = needs[start:start + batch_size]
                    texts = [d.get("text") or "" for d in batch]
                    try:
                        vecs, usage = await _embed_batch_hub(http, key, base, texts)
                    except Exception as e:
                        failures += 1
                        logger.exception("reembed batch %d-%d failed",
                                         start, start + len(batch))
                        await _set_job(db, job_id, last_error=str(e)[:400],
                                       failures=failures)
                        await asyncio.sleep(2.0)
                        continue
                    now_iso = _now_iso()
                    for d, v in zip(batch, vecs):
                        await db.doc_chunks.update_one(
                            {"_id": d["_id"]},
                            {"$set": {
                                "embedding": v,
                                "embedding_dim": len(v),
                                "embedding_model": NEW_MODEL,
                                "embedded_at": now_iso,
                            }},
                        )
                    done_ids.update(d["_id"] for d in batch)
                    processed += len(batch)
                    tokens_used += int(usage.get("total_tokens") or 0)
                    # Checkpoint every batch.
                    await db.reembed_progress.update_one(
                        {"_id": "doc_chunks_to_3large"},
                        {"$set": {"done_ids": list(done_ids),
                                  "last_at": now_iso,
                                  "total_processed": processed}},
                        upsert=True,
                    )
                    await _set_job(db, job_id,
                                   chunks_processed=processed,
                                   tokens_used=tokens_used,
                                   usd_spent=round(tokens_used / 1_000_000 * PRICE_PER_M_TOKENS, 4))

            # Final checkpoint
            await db.reembed_progress.update_one(
                {"_id": "doc_chunks_to_3large"},
                {"$set": {"done_ids": list(done_ids),
                          "last_at": _now_iso(),
                          "total_processed": len(done_ids),
                          "completed": True}},
                upsert=True,
            )

        purged = 0
        if purge_legacy:
            q = {"$or": [{"embedding_dim": {"$ne": NEW_DIM}},
                         {"embedding_model": {"$ne": NEW_MODEL}}]}
            res = await db.doc_chunks.delete_many(q)
            purged = res.deleted_count

        # Reload the RAG in-memory index + clear the dim-mismatch flag.
        reload_meta: Dict[str, Any] = {"reloaded": False}
        try:
            import rag as _rag
            await _rag.refresh_index_from_db(db)  # type: ignore[attr-defined]
            _rag.clear_dim_mismatch_log()
            reload_meta = {"reloaded": True}
        except AttributeError:
            # Older rag.py without refresh_index_from_db: do a soft reload.
            try:
                import rag as _rag
                _rag._index_matrix = None  # type: ignore[attr-defined]
                _rag._index_meta = []       # type: ignore[attr-defined]
                await _rag.ensure_index_loaded(db)
                _rag.clear_dim_mismatch_log()
                reload_meta = {"reloaded": True, "method": "soft"}
            except Exception as e:
                reload_meta = {"reloaded": False, "error": str(e)[:200]}
        except Exception as e:
            reload_meta = {"reloaded": False, "error": str(e)[:200]}

        await _set_job(db, job_id,
                       status="completed",
                       finished_at=_now_iso(),
                       dry_run=False,
                       chunks_processed=processed,
                       chunks_total=total,
                       tokens_used=tokens_used,
                       usd_spent=round(tokens_used / 1_000_000 * PRICE_PER_M_TOKENS, 4),
                       failures=failures,
                       purged_count=purged,
                       index_reload=reload_meta)
    except Exception as e:
        logger.exception("reembed job %s crashed", job_id)
        await _set_job(db, job_id, status="failed",
                       finished_at=_now_iso(),
                       error=str(e)[:600])


async def kickoff(db, *, dry_run: bool, batch_size: int,
                  max_chunks: Optional[int], purge_legacy: bool) -> Dict[str, Any]:
    """Insert a job row, schedule the background task, return the handle."""
    est = await estimate(db)
    job_id = "reembed_" + uuid.uuid4().hex[:12]
    started_at = _now_iso()
    await db.reembed_jobs.insert_one({
        "job_id": job_id,
        "started_at": started_at,
        "last_updated_at": started_at,
        "dry_run": dry_run,
        "batch_size": batch_size,
        "max_chunks": max_chunks,
        "purge_legacy": purge_legacy,
        "target_model": NEW_MODEL,
        "target_dim": NEW_DIM,
        "status": "starting",
        "chunks_total": est["chunks_to_process"]
        if max_chunks is None else min(est["chunks_to_process"], max_chunks),
        "chunks_processed": 0,
        "estimated_tokens": est["estimated_tokens"],
        "estimated_usd": est["estimated_usd"],
        "tokens_used": 0,
        "usd_spent": 0.0,
        "failures": 0,
    })
    # Fire-and-forget background task. We deliberately do NOT await — the
    # endpoint returns immediately so the admin can poll status.
    asyncio.create_task(run_job(db, job_id, dry_run=dry_run,
                                batch_size=batch_size,
                                max_chunks=max_chunks,
                                purge_legacy=purge_legacy))
    return {
        "job_id": job_id,
        "status": "started",
        "started_at": started_at,
        "dry_run": dry_run,
        "chunks_to_process": est["chunks_to_process"]
        if max_chunks is None else min(est["chunks_to_process"], max_chunks),
        "estimated_tokens": est["estimated_tokens"],
        "estimated_usd": est["estimated_usd"],
        "target_model": NEW_MODEL,
        "target_dim": NEW_DIM,
    }
