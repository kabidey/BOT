"""RAG pipeline for SMIFS Wealth-Engagement Agent.

- Chunker: splits markdown by H2 sections, then by ~400-token windows with 50-token overlap.
- Embedder: tries Hub AI /embeddings first; falls back to local sentence-transformers
  (all-MiniLM-L6-v2). Records which path is active in EMBEDDER_KIND.
- Vector store: chunks + 384-dim embeddings persisted in MongoDB collection `doc_chunks`.
  In-memory numpy matrix is built once (per process) for cosine similarity search.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------- Config ----------------------
SEED_DIR = Path(__file__).parent / "seed_docs"
APPROX_TOKENS_PER_CHUNK = 400
APPROX_OVERLAP_TOKENS = 50
# 1 token ≈ 4 chars for English markdown — good enough for chunk sizing.
CHARS_PER_TOKEN = 4
CHUNK_CHAR_TARGET = APPROX_TOKENS_PER_CHUNK * CHARS_PER_TOKEN
CHUNK_CHAR_OVERLAP = APPROX_OVERLAP_TOKENS * CHARS_PER_TOKEN

LLMHUB_API_KEY = os.environ.get("LLMHUB_API_KEY", "")
LLMHUB_BASE_URL = os.environ.get("LLMHUB_BASE_URL", "").rstrip("/")

# Module-level state populated by ingest()
EMBEDDER_KIND: Optional[str] = None  # "hub_ai" or "local"
_local_model = None  # lazy-loaded SentenceTransformer
_index_lock = asyncio.Lock()
_index_matrix: Optional[np.ndarray] = None  # shape (N, dim) float32, L2-normalised
_index_meta: List[Dict[str, Any]] = []  # parallel list of {doc_title, section, text, doc_id}


# ---------------------- Embedder ----------------------
async def _try_hub_ai_embed(texts: List[str]) -> Optional[np.ndarray]:
    """Try Hub AI /embeddings. Returns float32 array (N, dim) or None if unavailable."""
    if not LLMHUB_API_KEY or not LLMHUB_BASE_URL:
        return None
    url = f"{LLMHUB_BASE_URL}/embeddings"
    payload = {"model": "auto", "input": texts}
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                url,
                headers={
                    "Authorization": f"Bearer {LLMHUB_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            vecs = [item["embedding"] for item in data["data"]]
            return np.asarray(vecs, dtype=np.float32)
    except Exception as e:
        logger.warning("Hub AI embeddings probe failed: %s", e)
        return None


def _get_local_model():
    """Lazy-load the sentence-transformers model (one-time ~10s)."""
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading local embedder all-MiniLM-L6-v2 (one-time)…")
        _local_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _local_model


def _local_embed(texts: List[str]) -> np.ndarray:
    model = _get_local_model()
    vecs = model.encode(texts, convert_to_numpy=True, normalize_embeddings=False)
    return vecs.astype(np.float32)


async def embed_texts(texts: List[str]) -> Tuple[np.ndarray, str]:
    """Returns (embeddings (N, dim) float32, embedder_kind 'hub_ai'|'local').
    Caches the choice in EMBEDDER_KIND to avoid probing on every call."""
    global EMBEDDER_KIND
    if EMBEDDER_KIND is None:
        # First-call probe
        hub_vecs = await _try_hub_ai_embed(texts[:1])
        if hub_vecs is not None:
            EMBEDDER_KIND = "hub_ai"
        else:
            EMBEDDER_KIND = "local"
            logger.info("Hub AI embeddings unavailable; using local sentence-transformers.")
    if EMBEDDER_KIND == "hub_ai":
        vecs = await _try_hub_ai_embed(texts)
        if vecs is not None:
            return vecs, "hub_ai"
        # If a subsequent call fails, switch to local for the rest of the process lifetime.
        EMBEDDER_KIND = "local"
        logger.warning("Hub AI embeddings became unavailable; switching to local.")
    # Run blocking encode in a thread.
    vecs = await asyncio.to_thread(_local_embed, texts)
    return vecs, "local"


# ---------------------- Chunker ----------------------
def _split_section(section_text: str) -> List[str]:
    """Window a section into ~CHUNK_CHAR_TARGET chunks with CHUNK_CHAR_OVERLAP overlap."""
    section_text = section_text.strip()
    if len(section_text) <= CHUNK_CHAR_TARGET:
        return [section_text] if section_text else []
    chunks: List[str] = []
    start = 0
    while start < len(section_text):
        end = min(start + CHUNK_CHAR_TARGET, len(section_text))
        # Try to break on paragraph or sentence boundary
        if end < len(section_text):
            for boundary in ("\n\n", ". ", " "):
                cut = section_text.rfind(boundary, start, end)
                if cut > start + CHUNK_CHAR_TARGET // 2:
                    end = cut + len(boundary)
                    break
        chunks.append(section_text[start:end].strip())
        if end == len(section_text):
            break
        start = max(end - CHUNK_CHAR_OVERLAP, start + 1)
    return [c for c in chunks if c]


def chunk_markdown(doc_id: str, doc_title: str, body: str) -> List[Dict[str, Any]]:
    """Split a markdown doc by ## headings, then window each section."""
    body = body.strip()
    # Strip the H1 heading from the body if present (we use doc_title for that)
    body = re.sub(r"^#\s+.*\n+", "", body, count=1)

    parts = re.split(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE)
    chunks: List[Dict[str, Any]] = []
    # parts pattern: [pre-section-text, h2, section_body, h2, section_body, ...]
    if parts and parts[0].strip():
        for piece in _split_section(parts[0]):
            chunks.append({"doc_id": doc_id, "doc_title": doc_title, "section": "Overview", "text": piece})
    i = 1
    while i < len(parts):
        section = parts[i].strip()
        section_body = parts[i + 1] if i + 1 < len(parts) else ""
        # Re-split nested ### headings into the section text (kept inline)
        for piece in _split_section(section_body):
            chunks.append({"doc_id": doc_id, "doc_title": doc_title, "section": section, "text": piece})
        i += 2
    return chunks


def _doc_id_from_path(p: Path) -> str:
    return p.stem


def _doc_title_from_path(p: Path, body: str) -> str:
    m = re.match(r"^#\s+(.+?)\s*$", body, flags=re.MULTILINE)
    if m:
        return m.group(1).strip()
    return p.stem.replace("_", " ").title()


def load_seed_chunks() -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    if not SEED_DIR.exists():
        return chunks
    for md in sorted(SEED_DIR.glob("*.md")):
        body = md.read_text(encoding="utf-8")
        doc_id = _doc_id_from_path(md)
        doc_title = _doc_title_from_path(md, body)
        chunks.extend(chunk_markdown(doc_id, doc_title, body))
    return chunks


# ---------------------- Persistence + Index ----------------------
def _normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return (mat / norms).astype(np.float32)


async def _persist_chunks(db, chunks: List[Dict[str, Any]], embeddings: np.ndarray) -> None:
    now = datetime.now(timezone.utc).isoformat()
    docs = []
    for chunk, vec in zip(chunks, embeddings):
        chunk_id = hashlib.sha1(
            f"{chunk['doc_id']}::{chunk['section']}::{chunk['text'][:64]}".encode()
        ).hexdigest()
        docs.append({
            "_id": chunk_id,
            "doc_id": chunk["doc_id"],
            "doc_title": chunk["doc_title"],
            "section": chunk["section"],
            "text": chunk["text"],
            "embedding": vec.tolist(),
            "created_at": now,
            "source": "seed",
        })
    # Only delete seed chunks — preserve any uploads
    await db.doc_chunks.delete_many({"source": {"$ne": "upload"}})
    if docs:
        await db.doc_chunks.insert_many(docs)


async def _load_index_from_db(db) -> Tuple[Optional[np.ndarray], List[Dict[str, Any]]]:
    cursor = db.doc_chunks.find({}, {"embedding": 1, "doc_id": 1, "doc_title": 1, "section": 1, "text": 1})
    rows = await cursor.to_list(length=10000)
    if not rows:
        return None, []
    vecs = np.asarray([r["embedding"] for r in rows], dtype=np.float32)
    meta = [{"doc_id": r["doc_id"], "doc_title": r["doc_title"], "section": r["section"], "text": r["text"]} for r in rows]
    return _normalize(vecs), meta


async def ensure_index_loaded(db) -> int:
    """Load the in-memory index from DB if not already loaded. Returns chunk count."""
    global _index_matrix, _index_meta, EMBEDDER_KIND
    async with _index_lock:
        if _index_matrix is not None:
            return len(_index_meta)
        mat, meta = await _load_index_from_db(db)
        if mat is None:
            return 0
        _index_matrix = mat
        _index_meta = meta
        # Embedding dim 384 = local MiniLM; 1536+ would be hub_ai. Heuristic for status display.
        if EMBEDDER_KIND is None:
            EMBEDDER_KIND = "local" if mat.shape[1] == 384 else "hub_ai"
        logger.info("RAG index loaded: %d chunks (embedder=%s)", len(meta), EMBEDDER_KIND)
        return len(meta)


async def reingest(db) -> Dict[str, Any]:
    """Re-chunk + re-embed seed docs and rebuild the in-memory index."""
    global _index_matrix, _index_meta
    chunks = load_seed_chunks()
    if not chunks:
        return {"docs": 0, "chunks": 0, "embedder": EMBEDDER_KIND or "none"}

    texts = [c["text"] for c in chunks]
    vecs, kind = await embed_texts(texts)
    await _persist_chunks(db, chunks, vecs)

    async with _index_lock:
        _index_matrix = _normalize(vecs)
        _index_meta = [
            {"doc_id": c["doc_id"], "doc_title": c["doc_title"], "section": c["section"], "text": c["text"]}
            for c in chunks
        ]
        _query_cache.clear()

    doc_count = len({c["doc_id"] for c in chunks})
    logger.info("RAG reingest done: docs=%d chunks=%d embedder=%s", doc_count, len(chunks), kind)
    return {"docs": doc_count, "chunks": len(chunks), "embedder": kind}


async def ingest_extra_chunks(db, chunks: List[Dict[str, Any]], *, source: str = "upload",
                              filename: Optional[str] = None) -> int:
    """Embed and persist additional chunks (e.g. uploaded files) without wiping
    the seed corpus. Updates the in-memory index in-place. Returns chunk count."""
    if not chunks:
        return 0
    texts = [c["text"] for c in chunks]
    vecs, _ = await embed_texts(texts)
    now = datetime.now(timezone.utc).isoformat()
    docs = []
    for chunk, vec in zip(chunks, vecs):
        chunk_id = hashlib.sha1(
            f"{chunk['doc_id']}::{chunk['section']}::{chunk['text'][:64]}".encode()
        ).hexdigest()
        docs.append({
            "_id": chunk_id,
            "doc_id": chunk["doc_id"],
            "doc_title": chunk["doc_title"],
            "section": chunk["section"],
            "text": chunk["text"],
            "embedding": vec.tolist(),
            "created_at": now,
            "uploaded_at": now,
            "source": source,
            "filename": filename,
        })
    # Upsert by id so re-uploads of the same file overwrite cleanly
    if docs:
        # Remove existing chunks for the same doc_id (handles re-upload)
        doc_id_set = {c["doc_id"] for c in chunks}
        await db.doc_chunks.delete_many({"doc_id": {"$in": list(doc_id_set)}})
        await db.doc_chunks.insert_many(docs)
    # Rebuild in-memory index from DB (simpler than incremental)
    await reload_index_from_db(db)
    return len(docs)


async def reload_index_from_db(db) -> int:
    """Rebuild the in-memory index from the persisted doc_chunks collection."""
    global _index_matrix, _index_meta, EMBEDDER_KIND
    mat, meta = await _load_index_from_db(db)
    async with _index_lock:
        if mat is None:
            _index_matrix = None
            _index_meta = []
        else:
            _index_matrix = mat
            _index_meta = meta
            if EMBEDDER_KIND is None:
                EMBEDDER_KIND = "local" if mat.shape[1] == 384 else "hub_ai"
        _query_cache.clear()
    return len(_index_meta)


async def search(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Cosine similarity search. Returns [{text, doc_title, section, doc_id, score}]."""
    if _index_matrix is None or not _index_meta:
        return []
    q = await _embed_query_cached(query)
    scores = (_index_matrix @ q).astype(float)
    top = np.argsort(-scores)[:top_k]
    out: List[Dict[str, Any]] = []
    for i in top:
        m = _index_meta[int(i)]
        out.append({**m, "score": float(scores[int(i)])})
    return out


# Tiny LRU for query embeddings — landing-page suggestion chips and repeat questions
# re-encode identical strings; cache cuts ~50-150ms off p50 on local embedder.
_query_cache: "Dict[str, np.ndarray]" = {}
_QUERY_CACHE_MAX = 256


async def _embed_query_cached(query: str) -> np.ndarray:
    cached = _query_cache.get(query)
    if cached is not None:
        return cached
    vecs, _ = await embed_texts([query])
    q = _normalize(vecs)[0]
    if len(_query_cache) >= _QUERY_CACHE_MAX:
        # Drop oldest insertion (FIFO is fine for our scale)
        _query_cache.pop(next(iter(_query_cache)))
    _query_cache[query] = q
    return q
