"""Phase 24a.2 — RAG reranker.

Two-stage reranker:
  1. **Primary**: claude-haiku-4-5-20251001 via Hub AI. JSON-strict prompt
     asks Haiku to return `[{"index": int, "score": float}, …]` sorted desc.
  2. **Fallback**: local `cross-encoder/ms-marco-MiniLM-L-6-v2` (lazy
     singleton). Used when Haiku times out / 5xx, OR when `RERANKER_OFFLINE=1`.

Both stages are wrapped in defensive timeouts and a 5-min in-memory cache
keyed by `hash(query + sorted candidate ids)`. If both stages fail, the
caller's input candidates are returned UNCHANGED — never block the answer.

`RERANKER_ENABLED=false` (env) is the global kill-switch.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

LLMHUB_API_KEY = os.environ.get("LLMHUB_API_KEY", "")
LLMHUB_BASE_URL = os.environ.get("LLMHUB_BASE_URL", "").rstrip("/")

RERANKER_ENABLED = os.environ.get("RERANKER_ENABLED", "true").lower() == "true"
RERANKER_OFFLINE = os.environ.get("RERANKER_OFFLINE", "false").lower() == "true"
RERANKER_MODEL = os.environ.get("RERANKER_MODEL", "claude-haiku-4-5-20251001")
RERANKER_HAIKU_TIMEOUT_SEC = float(os.environ.get("RERANKER_HAIKU_TIMEOUT_SEC", "8.0"))

# ----- 5-minute in-memory cache -----
_cache: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_TTL_SEC = 300
_CACHE_MAX = 128

# ----- Local cross-encoder lazy singleton -----
_local_xenc = None
_local_xenc_lock = asyncio.Lock()


def _cache_key(query: str, candidates: List[Dict[str, Any]], top_k: int) -> str:
    ids = [c.get("doc_id") or c.get("id") or hashlib.sha1((c.get("text") or "")[:48].encode()).hexdigest()[:8]
           for c in candidates]
    raw = query + "|" + ",".join(sorted(ids)) + f"|k={top_k}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _cache_get(key: str) -> Optional[List[Dict[str, Any]]]:
    row = _cache.get(key)
    if not row:
        return None
    exp, val = row
    if exp < time.time():
        _cache.pop(key, None)
        return None
    return val


def _cache_put(key: str, val: List[Dict[str, Any]]) -> None:
    if len(_cache) >= _CACHE_MAX:
        # Drop oldest insertion (FIFO is fine).
        _cache.pop(next(iter(_cache)), None)
    _cache[key] = (time.time() + _CACHE_TTL_SEC, val)


# ============================================================
# Stage 1 — Haiku JSON-strict rerank
# ============================================================
_RERANK_SYSTEM = (
    "You are a relevance ranker. The user gives you a QUERY and a list of CANDIDATE passages. "
    "Re-rank the candidates by how directly they answer the QUERY. Return ONLY a JSON array of "
    "objects with the exact shape [{\"index\": <int>, \"score\": <float 0-1>}, ...], sorted by "
    "score descending. Indices refer to the candidate list (0-based). Be strict: passages that "
    "mention the topic but don't answer the QUERY get low scores. No prose, no commentary."
)


def _build_user_prompt(query: str, candidates: List[Dict[str, Any]]) -> str:
    lines = [f"QUERY: {query}\n\nCANDIDATES:"]
    for i, c in enumerate(candidates):
        snippet = (c.get("text") or c.get("text_chunk") or "")[:500].replace("\n", " ")
        title = c.get("doc_title") or c.get("title") or ""
        section = c.get("section") or c.get("section_heading") or ""
        head = f"[{i}] {title}".strip()
        if section:
            head += f" — {section}"
        lines.append(f"{head}\n{snippet}")
    lines.append("\nReturn the JSON array now.")
    return "\n\n".join(lines)


async def _rerank_via_haiku(query: str, candidates: List[Dict[str, Any]],
                              top_k: int) -> Optional[List[Dict[str, Any]]]:
    if not LLMHUB_API_KEY or not LLMHUB_BASE_URL:
        return None
    user_prompt = _build_user_prompt(query, candidates)
    payload: Dict[str, Any] = {
        "model": RERANKER_MODEL,
        "messages": [
            {"role": "system", "content": _RERANK_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 800,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {LLMHUB_API_KEY}",
               "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=RERANKER_HAIKU_TIMEOUT_SEC) as http:
            r = await http.post(f"{LLMHUB_BASE_URL}/chat/completions", headers=headers, json=payload)
        if r.status_code != 200:
            logger.warning("rerank.haiku non-200: %s — %s", r.status_code, r.text[:160])
            return None
        msg = (r.json().get("choices") or [{}])[0].get("message") or {}
        raw = msg.get("content") or ""
        # Haiku may return `{"rankings":[...]}` or a bare list — handle both.
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if isinstance(parsed, dict):
            for k in ("rankings", "results", "ranked", "data"):
                if isinstance(parsed.get(k), list):
                    parsed = parsed[k]
                    break
        if not isinstance(parsed, list):
            return None
    except Exception as e:
        logger.info("rerank.haiku failed: %s", e)
        return None

    # Reorder candidates per Haiku's ranking. Items not mentioned by Haiku
    # are appended at the end in original order.
    seen = set()
    out: List[Dict[str, Any]] = []
    for r in parsed:
        if not isinstance(r, dict):
            continue
        idx = r.get("index")
        score = r.get("score")
        if not isinstance(idx, int) or idx < 0 or idx >= len(candidates) or idx in seen:
            continue
        seen.add(idx)
        c = dict(candidates[idx])
        c["rerank_score"] = float(score) if isinstance(score, (int, float)) else 0.0
        c["rerank_source"] = "haiku"
        out.append(c)
    for i, c in enumerate(candidates):
        if i in seen:
            continue
        c2 = dict(c)
        c2.setdefault("rerank_score", 0.0)
        c2["rerank_source"] = "haiku_unranked"
        out.append(c2)
    return out[:top_k]


# ============================================================
# Stage 2 — Local cross-encoder fallback
# ============================================================
async def _get_local_xenc():
    global _local_xenc
    if _local_xenc is not None:
        return _local_xenc
    async with _local_xenc_lock:
        if _local_xenc is not None:
            return _local_xenc
        try:
            def _load():
                from sentence_transformers import CrossEncoder
                return CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
            _local_xenc = await asyncio.to_thread(_load)
            logger.info("rerank.local cross-encoder ms-marco-MiniLM-L-6-v2 loaded")
        except Exception as e:
            logger.warning("rerank.local cross-encoder unavailable: %s", e)
            _local_xenc = None
    return _local_xenc


async def _rerank_via_local(query: str, candidates: List[Dict[str, Any]],
                              top_k: int) -> Optional[List[Dict[str, Any]]]:
    model = await _get_local_xenc()
    if model is None:
        return None
    try:
        pairs = [(query, (c.get("text") or c.get("text_chunk") or "")[:500])
                 for c in candidates]
        def _score():
            return model.predict(pairs).tolist()
        scores = await asyncio.to_thread(_score)
    except Exception as e:
        logger.info("rerank.local scoring failed: %s", e)
        return None
    indexed = sorted(enumerate(scores), key=lambda t: -t[1])
    out: List[Dict[str, Any]] = []
    for i, s in indexed[:top_k]:
        c = dict(candidates[i])
        c["rerank_score"] = float(s)
        c["rerank_source"] = "local"
        out.append(c)
    return out


# ============================================================
# Public entrypoint
# ============================================================
async def rerank(query: str, candidates: List[Dict[str, Any]],
                  top_k: int = 5) -> List[Dict[str, Any]]:
    """Re-rank `candidates` (list of dicts with at least `text` field) by
    semantic relevance to `query`. Returns top-`top_k`.

    Graceful degradation order:
      1. Disabled flag → return original candidates[:top_k] (no work).
      2. Try Haiku, cache result.
      3. Try local cross-encoder.
      4. Return original order if both fail.
    """
    if not RERANKER_ENABLED:
        logger.debug("rerank.disabled")
        return candidates[:top_k]
    if not candidates:
        return []
    if len(candidates) <= 1:
        return candidates[:top_k]

    key = _cache_key(query, candidates, top_k)
    cached = _cache_get(key)
    if cached is not None:
        logger.debug("rerank.cache_hit")
        return cached

    out: Optional[List[Dict[str, Any]]] = None

    if not RERANKER_OFFLINE:
        out = await _rerank_via_haiku(query, candidates, top_k)
        if out is not None:
            logger.info("rerank.haiku.ok n=%d→%d", len(candidates), len(out))
            _cache_put(key, out)
            return out

    out = await _rerank_via_local(query, candidates, top_k)
    if out is not None:
        logger.info("rerank.local.ok n=%d→%d", len(candidates), len(out))
        _cache_put(key, out)
        return out

    logger.info("rerank.fallthrough — returning original order n=%d", len(candidates))
    return candidates[:top_k]
