#!/usr/bin/env python3
"""Phase 24a.3 — Re-embed `doc_chunks` collection to `text-embedding-3-large`.

Run manually after deployment:
    cd /app/backend && python -m scripts.reembed_doc_chunks --dry-run
    cd /app/backend && python -m scripts.reembed_doc_chunks --confirm
    cd /app/backend && python -m scripts.reembed_doc_chunks --confirm --purge-legacy

Idempotent + resumable. Progress is checkpointed to `_reembed_progress`.

Cost: text-embedding-3-large is $0.13 / 1M tokens. For ~5000 chunks at ~400
tokens each = 2M tokens ≈ $0.26. The --dry-run flag reports this estimate
without spending tokens.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `backend/` importable when invoked from inside `backend/scripts`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reembed")

NEW_MODEL = "text-embedding-3-large"
NEW_DIM = 3072
BATCH_SIZE = 50
CHECKPOINT_EVERY = 200
PRICE_PER_M_TOKENS = 0.13  # USD


async def _embed_batch(http: httpx.AsyncClient, key: str, base: str,
                        texts: list[str]) -> list[list[float]]:
    r = await http.post(
        f"{base}/embeddings",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": NEW_MODEL, "input": texts},
        timeout=60.0,
    )
    r.raise_for_status()
    items = r.json().get("data") or []
    return [it["embedding"] for it in items]


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


async def run(dry_run: bool, purge_legacy: bool) -> None:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    key = os.environ.get("LLMHUB_API_KEY", "")
    base = os.environ.get("LLMHUB_BASE_URL", "").rstrip("/")
    if not (mongo_url and db_name and key and base):
        raise RuntimeError("MONGO_URL / DB_NAME / LLMHUB_API_KEY / LLMHUB_BASE_URL all required")

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    # Read checkpoint (`reembed_progress` — no leading underscore, Motor rule)
    ckpt = await db.reembed_progress.find_one({"_id": "doc_chunks_to_3large"}) or {}
    done_ids = set(ckpt.get("done_ids") or [])
    logger.info("checkpoint: %d chunks already re-embedded", len(done_ids))

    # Find chunks needing re-embed (no embedding_model field, or set to legacy).
    needs = await db.doc_chunks.find(
        {"$or": [
            {"embedding_model": {"$exists": False}},
            {"embedding_model": {"$ne": NEW_MODEL}},
        ]},
        {"_id": 1, "text": 1},
    ).to_list(length=None)
    needs = [d for d in needs if d["_id"] not in done_ids]
    total = len(needs)
    if total == 0:
        logger.info("nothing to re-embed; %d chunks already on %s", await db.doc_chunks.count_documents({"embedding_model": NEW_MODEL}), NEW_MODEL)
    else:
        token_est = sum(_estimate_tokens(d.get("text") or "") for d in needs)
        cost_est = token_est / 1_000_000 * PRICE_PER_M_TOKENS
        logger.info("queue: %d chunks ≈ %d tokens ≈ $%.4f USD", total, token_est, cost_est)

    if dry_run:
        logger.info("--dry-run: not making any Hub AI calls.")
        await _maybe_purge(db, purge_legacy, dry_run=True)
        return

    if total > 0:
        async with httpx.AsyncClient() as http:
            processed = 0
            for start in range(0, total, BATCH_SIZE):
                batch = needs[start:start + BATCH_SIZE]
                texts = [d.get("text") or "" for d in batch]
                try:
                    vecs = await _embed_batch(http, key, base, texts)
                except Exception:
                    logger.exception("batch %d-%d failed; sleeping 5s then continuing", start, start + len(batch))
                    await asyncio.sleep(5.0)
                    continue
                now_iso = datetime.now(timezone.utc).isoformat()
                # Bulk-update each chunk.
                ops = []
                for d, v in zip(batch, vecs):
                    ops.append({
                        "filter": {"_id": d["_id"]},
                        "update": {"$set": {
                            "embedding": v,
                            "embedding_dim": len(v),
                            "embedding_model": NEW_MODEL,
                            "embedded_at": now_iso,
                        }},
                    })
                # Motor bulk_write is overkill for 50 — sequential is fine.
                for op in ops:
                    await db.doc_chunks.update_one(op["filter"], op["update"])
                done_ids.update(d["_id"] for d in batch)
                processed += len(batch)
                if processed % CHECKPOINT_EVERY < BATCH_SIZE:
                    await db.reembed_progress.update_one(
                        {"_id": "doc_chunks_to_3large"},
                        {"$set": {"done_ids": list(done_ids),
                                   "last_at": now_iso, "total_processed": processed}},
                        upsert=True,
                    )
                    logger.info("checkpoint: %d / %d", processed, total)
        # Final checkpoint
        await db.reembed_progress.update_one(
            {"_id": "doc_chunks_to_3large"},
            {"$set": {"done_ids": list(done_ids),
                       "last_at": datetime.now(timezone.utc).isoformat(),
                       "total_processed": len(done_ids),
                       "completed": True}},
            upsert=True,
        )
        logger.info("re-embed complete: %d chunks now on %s", len(done_ids), NEW_MODEL)

    await _maybe_purge(db, purge_legacy, dry_run=False)


async def _maybe_purge(db, purge_legacy: bool, dry_run: bool) -> None:
    if not purge_legacy:
        return
    q = {"$or": [
        {"embedding_dim": {"$ne": NEW_DIM}},
        {"embedding_model": {"$ne": NEW_MODEL}},
    ]}
    if dry_run:
        n = await db.doc_chunks.count_documents(q)
        logger.info("--purge-legacy dry-run: WOULD delete %d legacy-dim chunks", n)
        return
    res = await db.doc_chunks.delete_many(q)
    logger.info("--purge-legacy: deleted %d legacy chunks", res.deleted_count)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true",
                     help="Actually run the re-embed (without this, --dry-run is implied).")
    ap.add_argument("--dry-run", action="store_true",
                     help="Report token + cost estimate without making Hub calls.")
    ap.add_argument("--purge-legacy", action="store_true",
                     help="After re-embed, DELETE all chunks NOT on the new model.")
    args = ap.parse_args()
    dry = args.dry_run or not args.confirm
    asyncio.run(run(dry_run=dry, purge_legacy=args.purge_legacy))
