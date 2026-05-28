"""RAG pipeline for Mackertich ONE Advisor (wealth-management vertical of SMIFS Ltd).

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
# Phase 24a.3 — upgrade default embedding model. New chunks embed at 3072-dim.
# Legacy 1536-dim chunks remain queryable until the migration purges them.
HUB_EMBED_MODEL = os.environ.get("HUB_EMBED_MODEL", "text-embedding-3-large")
HUB_EMBED_MODEL_LEGACY = "text-embedding-3-small"
HUB_EMBED_BATCH = int(os.environ.get("HUB_EMBED_BATCH", "32"))
# When set, retrieval ONLY uses chunks with this embedding_dim (post-migration).
RAG_DIM_FILTER = int(os.environ.get("RAG_DIM_FILTER", "0")) or None

# Module-level state populated by ingest()
EMBEDDER_KIND: Optional[str] = None  # "hub_ai" or "local"
_local_model = None  # lazy-loaded SentenceTransformer
_index_lock = asyncio.Lock()
_index_matrix: Optional[np.ndarray] = None  # shape (N, dim) float32, L2-normalised
_index_meta: List[Dict[str, Any]] = []  # parallel list of {doc_title, section, text, doc_id}


# ---------------------- Embedder ----------------------
async def _try_hub_ai_embed(texts: List[str], retries: int = 2) -> Optional[np.ndarray]:
    """Try Hub AI /embeddings. Returns float32 array (N, dim) or None if unavailable.
    Sends in batches of HUB_EMBED_BATCH. On transient 429s, retries with exponential
    backoff up to `retries` times before giving up."""
    if not LLMHUB_API_KEY or not LLMHUB_BASE_URL:
        return None
    url = f"{LLMHUB_BASE_URL}/embeddings"
    headers = {
        "Authorization": f"Bearer {LLMHUB_API_KEY}",
        "Content-Type": "application/json",
    }
    out_vecs: List[List[float]] = []
    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            for start in range(0, len(texts), HUB_EMBED_BATCH):
                batch = texts[start:start + HUB_EMBED_BATCH]
                payload = {"model": HUB_EMBED_MODEL, "input": batch}
                last_status: Optional[int] = None
                last_body: Optional[str] = None
                for attempt in range(retries + 1):
                    resp = await http.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        last_status = 200
                        break
                    last_status = resp.status_code
                    last_body = resp.text[:200]
                    # Retry only transient errors (rate-limit / upstream 5xx)
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                        await asyncio.sleep(0.4 * (2 ** attempt))
                        continue
                    break
                if last_status != 200:
                    logger.warning("Hub AI embeddings non-200: %s — %s", last_status, last_body)
                    return None
                data = resp.json()
                items = data.get("data") or []
                if len(items) != len(batch):
                    logger.warning("Hub AI embeddings batch size mismatch: %d != %d", len(items), len(batch))
                    return None
                for item in items:
                    out_vecs.append(item["embedding"])
        return np.asarray(out_vecs, dtype=np.float32)
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

    Concurrency-critical: once an index has been built with one embedder, the SAME
    embedder MUST be used for query-time vectors. Otherwise dim-mismatches cause
    matmul errors during search. We therefore pin to whichever embedder the
    persisted index was built with (probed at startup) and only fall back to local
    if Hub itself is permanently unreachable AND the index dim is also 384."""
    global EMBEDDER_KIND
    if EMBEDDER_KIND is None:
        # First-call probe — only happens before any index exists.
        hub_vecs = await _try_hub_ai_embed(texts[:1])
        if hub_vecs is not None:
            EMBEDDER_KIND = "hub_ai"
        else:
            EMBEDDER_KIND = "local"
            logger.info("Hub AI embeddings unavailable; using local sentence-transformers.")
    if EMBEDDER_KIND == "hub_ai":
        # Query-time: retry Hub up to 4x to ride out transient 429s. Do NOT silently
        # downgrade to 384-dim local — the index is 1536-dim and matmul would crash.
        vecs = await _try_hub_ai_embed(texts, retries=4)
        if vecs is not None:
            return vecs, "hub_ai"
        # All retries exhausted. Surface the failure rather than corrupt the search.
        raise RuntimeError(
            "Hub AI embeddings unavailable and the index is Hub-embedded (1536-dim). "
            "Cannot fall back to local 384-dim without rebuilding the index — please retry."
        )
    # EMBEDDER_KIND == "local" — index is 384-dim, local embedder is correct dim.
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
    # Phase 24a.3 — optionally restrict the index to chunks at a specific
    # embedding dim (post-migration safety net).
    query_filter: Dict[str, Any] = {}
    if RAG_DIM_FILTER:
        query_filter["embedding_dim"] = RAG_DIM_FILTER
    cursor = db.doc_chunks.find(query_filter, {
        "embedding": 1, "doc_id": 1, "doc_title": 1, "section": 1, "text": 1,
        "source": 1, "subsource": 1, "smifs_metadata": 1, "smifs_id": 1,
        # Phase 16 — projected metadata
        "doc_type": 1, "vehicle_id": 1, "vehicle_name": 1, "vehicle_type": 1,
        "is_active": 1, "is_focused": 1, "sales_pitch_ready": 1,
        "version_no": 1, "collateral_no": 1, "kind": 1, "language": 1,
        "provider": 1, "category": 1, "vertical": 1,
        "updated_at_iso": 1, "audience": 1, "version_major": 1,
        # Phase 24d — web_ingest fields (regulator + investor-education sites)
        "source_url": 1, "source_domain": 1, "source_title": 1, "source_section": 1,
    })
    rows = await cursor.to_list(length=5000)
    if not rows:
        return None, []
    vecs = np.asarray([r["embedding"] for r in rows], dtype=np.float32)
    meta = [{
        "doc_id": r["doc_id"], "doc_title": r["doc_title"],
        "section": r["section"], "text": r["text"],
        "source": r.get("source") or "seed",
        "subsource": r.get("subsource"),
        "smifs_metadata": r.get("smifs_metadata") or {},
        "smifs_id": r.get("smifs_id"),
        # Phase 16 — projected metadata (may be missing on legacy chunks)
        "doc_type": r.get("doc_type"),
        "vehicle_id": r.get("vehicle_id"),
        "vehicle_name": r.get("vehicle_name"),
        "vehicle_type": r.get("vehicle_type"),
        "is_active": r.get("is_active"),
        "is_focused": r.get("is_focused"),
        "sales_pitch_ready": r.get("sales_pitch_ready"),
        "version_no": r.get("version_no"),
        "version_major": r.get("version_major"),
        "collateral_no": r.get("collateral_no"),
        "kind": r.get("kind"),
        "language": r.get("language"),
        "provider": r.get("provider"),
        "category": r.get("category"),
        "vertical": r.get("vertical"),
        "updated_at_iso": r.get("updated_at_iso"),
        "audience": r.get("audience") or "all",
        # Phase 24d — web_ingest projection (regulator / investor-education sites)
        "source_url": r.get("source_url"),
        "source_domain": r.get("source_domain"),
        "source_title": r.get("source_title"),
        "source_section": r.get("source_section"),
    } for r in rows]
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
        # Embedding dim 384 = local MiniLM; >= 768 (1536 for OpenAI text-embedding-3-small) = hub_ai.
        if EMBEDDER_KIND is None:
            EMBEDDER_KIND = "local" if mat.shape[1] == 384 else "hub_ai"
        # Phase 27 — fresh index, fresh dim-guard log state.
        global _DIM_MISMATCH_LOGGED
        _DIM_MISMATCH_LOGGED = False
        logger.info("RAG index loaded: %d chunks (embedder=%s, dim=%d)", len(meta), EMBEDDER_KIND, mat.shape[1])
        return len(meta)


async def detect_active_embedder() -> str:
    """Probe Hub AI /embeddings once to decide which backend will be used for future encodes.
    Sets EMBEDDER_KIND. Returns the kind ('hub_ai' or 'local')."""
    global EMBEDDER_KIND
    probe = await _try_hub_ai_embed(["probe"])
    EMBEDDER_KIND = "hub_ai" if probe is not None else "local"
    logger.info("Active embedder selected: %s", EMBEDDER_KIND)
    return EMBEDDER_KIND


async def persisted_dim(db) -> Optional[int]:
    """Inspect one persisted chunk to learn the embedding dim already on disk, if any."""
    row = await db.doc_chunks.find_one({}, {"embedding": 1})
    if not row or "embedding" not in row:
        return None
    return len(row["embedding"])


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


# ---- Phase 27 — defensive dim guard ----
# Tracks whether we've already warned about a dim mismatch so we don't spam
# logs on every chat turn. Reset by `clear_dim_mismatch_log()` after a
# successful re-embed migration.
_DIM_MISMATCH_LOGGED: bool = False


def _dims_compatible(mat, q, where: str) -> bool:
    """Return True iff `mat @ q` is valid. On mismatch, log CRITICAL once and
    return False so callers can degrade gracefully (empty hits → upstream
    falls through to the no-relevant-content / anti-bluff path).

    The in-memory `_index_matrix` is a single ndarray, so all stored chunks
    share the same dim by construction. The mismatch we defend against is
    between the *query* embedding dim (set by HUB_EMBED_MODEL env var) and
    the *index* embedding dim (set by whatever model the chunks were stored
    with). Repair: re-embed via POST /api/admin/reembed/run so both sides
    agree at 3072. NEVER raises ValueError to the orchestrator from here.
    """
    global _DIM_MISMATCH_LOGGED
    try:
        index_dim = int(mat.shape[1])
        query_dim = int(q.shape[0])
    except Exception:
        if not _DIM_MISMATCH_LOGGED:
            logger.critical("RAG dim-guard (%s): malformed shapes mat=%r q=%r",
                            where, getattr(mat, "shape", None),
                            getattr(q, "shape", None))
            _DIM_MISMATCH_LOGGED = True
        return False
    if index_dim != query_dim:
        if not _DIM_MISMATCH_LOGGED:
            logger.critical(
                "RAG dim-guard (%s): index_dim=%d != query_dim=%d. "
                "Embeddings are stale (active model=%s). Run "
                "POST /api/admin/reembed/run to migrate. Falling back to "
                "empty results to avoid ValueError.",
                where, index_dim, query_dim, HUB_EMBED_MODEL,
            )
            _DIM_MISMATCH_LOGGED = True
        else:
            logger.warning("RAG dim-guard (%s): mismatch %d vs %d — skipping",
                           where, index_dim, query_dim)
        return False
    return True


def clear_dim_mismatch_log() -> None:
    """Test/admin hook — let the guard log again after a successful migration
    (called from the reembed job once the index has been rebuilt)."""
    global _DIM_MISMATCH_LOGGED
    _DIM_MISMATCH_LOGGED = False



async def search(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Cosine similarity search. Returns [{text, doc_title, section, doc_id, source, score}]."""
    if _index_matrix is None or not _index_meta:
        return []
    q = await _embed_query_cached(query)
    if not _dims_compatible(_index_matrix, q, where="search"):
        return []
    scores = (_index_matrix @ q).astype(float)
    top = np.argsort(-scores)[:top_k]
    out: List[Dict[str, Any]] = []
    for i in top:
        m = _index_meta[int(i)]
        out.append({**m, "score": float(scores[int(i)]), "raw_score": float(scores[int(i)])})
    return out


# Phase 9 — source-weighted retrieval.
# SMIFS official KB > seed docs > uploads > session archives.
SOURCE_WEIGHTS: Dict[str, float] = {
    "smifs_knowledge": 1.15,
    "seed":            1.00,
    "upload":          0.90,
    "session_archive": 0.80,
}

# Phase 16 — proxy-based ranking boosts (additive on cosine, applied after
# source weighting). Bedrock is the canonical authoritative subsource, focused
# vehicles are house-view picks, and recent updates get a small bonus.
PHASE16_BEDROCK_BOOST = 0.05
PHASE16_FOCUSED_BOOST = 0.03
PHASE16_RECENCY_BOOST = 0.02
PHASE16_RECENCY_WINDOW_DAYS = 90


def _phase16_proxy_bonus(m: Dict[str, Any]) -> float:
    """Bedrock canonical + focused house-views + recency proxy."""
    bonus = 0.0
    sub = m.get("subsource")
    if sub == "bedrock":
        bonus += PHASE16_BEDROCK_BOOST
    if m.get("is_focused") is True:
        bonus += PHASE16_FOCUSED_BOOST
    iso = m.get("updated_at_iso") or ""
    if iso:
        try:
            ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - ts).days
            if 0 <= age_days <= PHASE16_RECENCY_WINDOW_DAYS:
                bonus += PHASE16_RECENCY_BOOST
        except Exception:
            pass
    return bonus


async def search_weighted(query: str, top_k: int = 8,
                          restrict_sources: Optional[List[str]] = None,
                          restrict_audiences: Optional[List[str]] = None,
                          rerank_top_k: Optional[int] = None,
                          ) -> List[Dict[str, Any]]:
    """Phase 9 + Phase 16 + Phase 24a.2 retrieval.

    1. Cosine over the full in-memory index (cheap).
    2. Multiply each score by SOURCE_WEIGHTS[source] (SMIFS official wins ties).
    3. Apply Phase 16 proxy bonuses (bedrock canonical, focused house-views,
       recency window).
    4. Hard gates:
       - drop chunks where `is_active is False` (decommissioned vehicles).
       - if `restrict_sources` is given, drop everything else.
       - if `restrict_audiences` is given, drop chunks whose `audience`
         is not in the allow-list (Phase 16 employee-only gating).
    5. Return top_k with both `score` (post-weight) and `raw_score` (cosine).

    Phase 24a.2 — when `rerank_top_k` is set, retrieve a wider candidate pool
    (top_k * 4 capped at 24), then call the Haiku/local cross-encoder reranker
    and trim to `rerank_top_k`. Graceful degradation: if the reranker is
    disabled or fails, the cosine ordering is returned unchanged.
    """
    if _index_matrix is None or not _index_meta:
        return []
    q = await _embed_query_cached(query)
    if not _dims_compatible(_index_matrix, q, where="search_weighted"):
        return []
    raw = (_index_matrix @ q).astype(float)

    # Build per-row weighted scores.
    weighted = raw.copy()
    for i, m in enumerate(_index_meta):
        w = SOURCE_WEIGHTS.get(m.get("source") or "seed", 1.0)
        weighted[i] = raw[i] * w + _phase16_proxy_bonus(m)
        # Phase 16 hard gates
        if m.get("is_active") is False:
            weighted[i] = -1.0
        elif restrict_sources and (m.get("source") not in restrict_sources):
            weighted[i] = -1.0
        elif restrict_audiences and ((m.get("audience") or "all") not in restrict_audiences):
            weighted[i] = -1.0

    order = np.argsort(-weighted)
    out: List[Dict[str, Any]] = []
    # Phase 24a.2 — when rerank requested, gather a wider candidate pool to
    # give the reranker enough signal to work with.
    retrieval_target = max(top_k, (rerank_top_k or 0) * 4) if rerank_top_k else top_k
    retrieval_target = min(retrieval_target, 24)
    for i in order:
        if weighted[int(i)] < 0:
            continue
        m = _index_meta[int(i)]
        out.append({**m, "score": float(weighted[int(i)]), "raw_score": float(raw[int(i)])})
        if len(out) >= retrieval_target:
            break

    if rerank_top_k and out:
        try:
            from agents import reranker as _rr
            out = await _rr.rerank(query, out, top_k=rerank_top_k)
        except Exception as e:
            logger.info("reranker unavailable (non-fatal): %s", e)
            out = out[:rerank_top_k]
    return out


# Tiny LRU for query embeddings — landing-page suggestion chips and repeat questions
# re-encode identical strings; cache cuts ~50-150ms off p50 on local embedder.
_query_cache: "Dict[str, np.ndarray]" = {}
_QUERY_CACHE_MAX = 256


# Phase 26.1 — Acronym expansion for terse queries.
# When a short query (≤ 6 tokens) contains a bare acronym, append the full term
# so the embedding picks up the semantic signal of the expanded form. This
# rescues queries like "What are NCDs?" (top score 0.527 → low confidence) by
# rewriting them as "What are NCDs Non-Convertible Debentures?" (top score 0.77).
_ACRONYM_MAP: "Dict[str, str]" = {
    "NCD":  "Non-Convertible Debentures",
    "NCDS": "Non-Convertible Debentures",
    "AIF":  "Alternative Investment Fund",
    "AIFS": "Alternative Investment Funds",
    "PMS":  "Portfolio Management Services",
    "SIF":  "Specialised Investment Fund",
    "MF":   "Mutual Fund",
    "MFS":  "Mutual Funds",
    "SIP":  "Systematic Investment Plan",
    "SWP":  "Systematic Withdrawal Plan",
    "STP":  "Systematic Transfer Plan",
    "ELSS": "Equity-Linked Savings Scheme",
    "ULIP": "Unit-Linked Insurance Plan",
    "KYC":  "Know Your Customer",
    "CKYC": "Central KYC",
    "EKYC": "Electronic KYC",
    "UCC":  "Unique Client Code",
    "PAN":  "Permanent Account Number",
    "ASBA": "Application Supported by Blocked Amount",
    "IPO":  "Initial Public Offering",
    "FPO":  "Follow-on Public Offer",
    "OFS":  "Offer For Sale",
    "NAV":  "Net Asset Value",
    "ARN":  "AMFI Registration Number",
    "DEMAT": "Dematerialised account",
    "RM":   "Relationship Manager",
}
_ACRONYM_RE = re.compile(r"\b([A-Z]{2,6})s?\b")


def _expand_acronyms(query: str) -> str:
    """For short queries (≤ 6 tokens), append expansions for any acronyms
    we recognise. Leaves longer queries untouched (they already carry
    enough semantic context)."""
    if not query:
        return query
    if len(query.split()) > 6:
        return query
    matches = _ACRONYM_RE.findall(query)
    if not matches:
        return query
    expansions: List[str] = []
    seen: set = set()
    for tok in matches:
        key = tok.upper()
        exp = _ACRONYM_MAP.get(key)
        if exp and key not in seen:
            expansions.append(exp)
            seen.add(key)
    if not expansions:
        return query
    return f"{query} ({' / '.join(expansions)})"


async def _embed_query_cached(query: str) -> np.ndarray:
    expanded = _expand_acronyms(query)
    cached = _query_cache.get(expanded)
    if cached is not None:
        return cached
    vecs, _ = await embed_texts([expanded])
    q = _normalize(vecs)[0]
    if len(_query_cache) >= _QUERY_CACHE_MAX:
        # Drop oldest insertion (FIFO is fine for our scale)
        _query_cache.pop(next(iter(_query_cache)))
    _query_cache[expanded] = q
    return q
