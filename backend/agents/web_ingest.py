"""Phase 24d — Website Ingestion Crawler.

Ingest 6 financial/regulator sites into `doc_chunks` so the bot has broad
domain context beyond the internal SMIFS corpus.

Rules:
- Same-origin only. Strict robots.txt compliance.
- 1 site at a time globally (module-level lock).
- 4 concurrent fetches per site, polite delay 0.5–1.5s.
- User-Agent: MackertichONE-Bot/24 (+https://smifs.com/bot-info).
- Skip query-heavy URLs (>2 query combinations on same base path).
- HTML: trafilatura. PDF: pypdf.
- SHA-256 dedup BEFORE embedding.
- Embed with Hub AI text-embedding-3-large (3072 dim).
- Defensive guards: per-crawl token budget, wall-time, min chars, ASCII ratio.
"""
from __future__ import annotations
import asyncio
import hashlib
import logging
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from bs4 import BeautifulSoup

import rag

logger = logging.getLogger(__name__)

USER_AGENT = "MackertichONE-Bot/24 (+https://smifs.com/bot-info)"
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=8.0)

# Token-budget defaults
TOKEN_BUDGET_DEFAULT = int(os.environ.get("WEB_INGEST_TOKEN_BUDGET", "5000000"))
WALL_TIME_SEC_DEFAULT = int(os.environ.get("WEB_INGEST_WALL_TIME_SEC", "1800"))

# Chunking config (~800 token target with 100 token overlap; 1 token ≈ 4 chars)
CHUNK_CHARS = 3200
OVERLAP_CHARS = 400
MIN_CLEAN_CHARS = 200
MAX_NON_ASCII_RATIO = 0.80

# Concurrency
_GLOBAL_CRAWL_LOCK = asyncio.Lock()
PER_SITE_CONCURRENCY = 4

# ---- Domain → human-readable badge ----
_DOMAIN_BADGES = {
    "sebi.gov.in":     "SEBI",
    "rbi.org.in":      "RBI",
    "amfi.in":         "AMFI",
    "nseindia.com":    "NSE",
    "bseindia.com":    "BSE",
    "smifs.com":       "SMIFS",
    "irdai.gov.in":    "IRDAI",
}


def domain_badge(domain: str) -> str:
    d = (domain or "").lower().lstrip("www.")
    for key, badge in _DOMAIN_BADGES.items():
        if d.endswith(key):
            return badge
    return d.split(".")[0].upper() if d else "WEB"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _ascii_ratio(text: str) -> float:
    if not text:
        return 1.0
    ascii_n = sum(1 for c in text if ord(c) < 128)
    return ascii_n / max(1, len(text))


def _normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    return url.rstrip("/")


def _chunk_text(text: str) -> List[str]:
    text = (text or "").strip()
    if len(text) <= CHUNK_CHARS:
        return [text] if text else []
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            for boundary in ("\n\n", ". ", " "):
                cut = text.rfind(boundary, start, end)
                if cut > start + CHUNK_CHARS // 2:
                    end = cut + len(boundary)
                    break
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - OVERLAP_CHARS, start + 1)
    return [c for c in chunks if c]


def _extract_html(html: str, url: str) -> Tuple[str, str]:
    """Returns (title, main_text). Uses trafilatura for body extraction."""
    title = ""
    try:
        soup = BeautifulSoup(html, "lxml")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
    except Exception:
        pass
    body = trafilatura.extract(html, url=url, include_comments=False, include_tables=True,
                               favor_recall=True) or ""
    return title, body.strip()


def _extract_pdf(blob: bytes) -> Tuple[str, str]:
    """Returns (title_hint, body_text). Uses pypdf for text extraction."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(blob))
        title = ""
        try:
            md = reader.metadata
            if md and md.title:
                title = str(md.title).strip()
        except Exception:
            pass
        parts: List[str] = []
        for page in reader.pages[:50]:  # cap at 50 pages to bound CPU
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            if txt.strip():
                parts.append(txt.strip())
        return title, "\n\n".join(parts).strip()
    except Exception as e:
        logger.warning("PDF extraction failed: %s", e)
        return "", ""


def _link_candidates(html: str, base_url: str, base_domain: str) -> List[str]:
    out: List[str] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            abs_url = urljoin(base_url, href)
            p = urlparse(abs_url)
            if p.scheme not in ("http", "https"):
                continue
            if p.netloc.lower().lstrip("www.").split(":")[0] != base_domain:
                continue
            out.append(_normalize_url(abs_url))
    except Exception:
        return out
    return out


# ---- robots.txt cache ----
_ROBOTS_CACHE: Dict[str, RobotFileParser] = {}


async def _load_robots(domain: str, client: httpx.AsyncClient) -> RobotFileParser:
    if domain in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[domain]
    rp = RobotFileParser()
    try:
        r = await client.get(f"https://{domain}/robots.txt")
        if r.status_code == 200:
            rp.parse(r.text.splitlines())
        else:
            rp.parse([])
    except Exception:
        rp.parse([])
    _ROBOTS_CACHE[domain] = rp
    return rp


def _seed_domain(seed_url: str) -> str:
    return urlparse(seed_url).netloc.lower().lstrip("www.").split(":")[0]


# ----- Core public API -----
async def crawl_site(db,
                     seed_url: str,
                     *,
                     max_depth: int = 3,
                     max_pages: int = 500,
                     allow_pdf: bool = True,
                     allowed_path_prefix: Optional[str] = None,
                     dry_run: bool = False,
                     token_budget: Optional[int] = None,
                     wall_time_sec: Optional[int] = None) -> Dict[str, Any]:
    """BFS crawl one site. Returns a summary dict suitable for storing in
    `crawl_events` AND for the admin tile.

    If `dry_run=True`, fetches pages but does NOT embed/insert chunks.

    `allowed_path_prefix` (e.g. "/learn/") restricts to a subtree.
    """
    token_budget = token_budget if token_budget is not None else TOKEN_BUDGET_DEFAULT
    wall_time_sec = wall_time_sec if wall_time_sec is not None else WALL_TIME_SEC_DEFAULT

    domain = _seed_domain(seed_url)
    summary: Dict[str, Any] = {
        "started_at": _now(),
        "seed_url": seed_url,
        "domain": domain,
        "allowed_path_prefix": allowed_path_prefix,
        "max_depth": max_depth,
        "max_pages": max_pages,
        "allow_pdf": allow_pdf,
        "dry_run": dry_run,
        "pages_fetched": 0,
        "pages_skipped_robots": 0,
        "pages_skipped_dedup": 0,
        "pages_skipped_junk": 0,
        "pages_failed": 0,
        "chunks_written": 0,
        "tokens_estimated": 0,
        "status": "running",
        "errors": [],
    }

    if _GLOBAL_CRAWL_LOCK.locked():
        summary["status"] = "skipped_global_lock"
        return summary

    async with _GLOBAL_CRAWL_LOCK:
        started = time.time()
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT,
                                     headers={"User-Agent": USER_AGENT},
                                     follow_redirects=True,
                                     verify=False) as client:
            rp = await _load_robots(domain, client)

            visited: Set[str] = set()
            query_combinations: Dict[str, Set[str]] = defaultdict(set)
            sem = asyncio.Semaphore(PER_SITE_CONCURRENCY)
            pending_chunk_batch: List[Dict[str, Any]] = []

            existing_hashes: Set[str] = set()
            if not dry_run:
                # Pre-load existing hashes for dedup
                cur = db.doc_chunks.find({"source_domain": domain}, {"text_hash": 1, "_id": 0})
                async for r in cur:
                    h = r.get("text_hash")
                    if h:
                        existing_hashes.add(h)

            async def process_url(url: str, depth: int) -> List[str]:
                if time.time() - started > wall_time_sec:
                    return []
                if summary["pages_fetched"] >= max_pages:
                    return []
                if summary["tokens_estimated"] >= token_budget:
                    return []
                if url in visited:
                    return []
                visited.add(url)
                # Path-prefix gate
                if allowed_path_prefix:
                    if not urlparse(url).path.startswith(allowed_path_prefix):
                        return []
                # robots.txt
                if rp and not rp.can_fetch(USER_AGENT, url):
                    summary["pages_skipped_robots"] += 1
                    return []
                # Query-heavy guard
                pp = urlparse(url)
                if pp.query:
                    base = f"{pp.scheme}://{pp.netloc}{pp.path}"
                    qset = query_combinations[base]
                    if len(qset) >= 2 and pp.query not in qset:
                        return []
                    qset.add(pp.query)

                async with sem:
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    try:
                        r = await client.get(url)
                    except Exception as e:
                        summary["pages_failed"] += 1
                        summary["errors"].append({"url": url[:200], "err": str(e)[:120]})
                        return []
                if r.status_code != 200:
                    summary["pages_failed"] += 1
                    return []
                ctype = (r.headers.get("content-type") or "").lower()
                title = ""
                body = ""
                section_paths: List[str] = []
                if "text/html" in ctype:
                    title, body = _extract_html(r.text, url)
                    links = _link_candidates(r.text, url, domain)
                    section_paths = [pp.path or "/"]
                elif allow_pdf and ("application/pdf" in ctype or url.lower().endswith(".pdf")):
                    title, body = _extract_pdf(r.content)
                    links = []
                    section_paths = [pp.path or "/"]
                else:
                    summary["pages_skipped_junk"] += 1
                    return []

                if not body or len(body) < MIN_CLEAN_CHARS:
                    summary["pages_skipped_junk"] += 1
                    return links
                if _ascii_ratio(body) < (1.0 - MAX_NON_ASCII_RATIO):
                    summary["pages_skipped_junk"] += 1
                    return links

                summary["pages_fetched"] += 1
                # Chunk + dedup
                for piece in _chunk_text(body):
                    h = _sha256(piece)
                    if h in existing_hashes:
                        summary["pages_skipped_dedup"] += 1
                        continue
                    existing_hashes.add(h)
                    section_label = " › ".join(section_paths[:2]) or "/"
                    pending_chunk_batch.append({
                        "title": title or domain,
                        "section": section_label,
                        "text": piece,
                        "url": url,
                        "domain": domain,
                        "hash": h,
                    })
                    summary["tokens_estimated"] += max(1, len(piece) // 4)
                    if summary["tokens_estimated"] >= token_budget:
                        break
                return links

            depth_links: Dict[int, List[str]] = defaultdict(list)
            depth_links[0].append(_normalize_url(seed_url))
            for depth in range(max_depth + 1):
                if not depth_links[depth]:
                    continue
                tasks = [process_url(u, depth) for u in depth_links[depth][:max_pages]]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                if depth + 1 <= max_depth:
                    for child_links in results:
                        if isinstance(child_links, list):
                            for nl in child_links:
                                if nl not in visited:
                                    depth_links[depth + 1].append(nl)
                # budget checks
                if time.time() - started > wall_time_sec:
                    summary["status"] = "partial_walltime"
                    break
                if summary["pages_fetched"] >= max_pages:
                    summary["status"] = "partial_maxpages"
                    break
                if summary["tokens_estimated"] >= token_budget:
                    summary["status"] = "partial_budget"
                    break

            # ---- Embed + persist ----
            if pending_chunk_batch and not dry_run:
                texts = [c["text"] for c in pending_chunk_batch]
                try:
                    vecs, _ = await rag.embed_texts(texts)
                except Exception as e:
                    summary["status"] = "embed_failed"
                    summary["errors"].append({"stage": "embed", "err": str(e)[:200]})
                    vecs = None
                if vecs is not None:
                    now = _now()
                    embed_model = os.environ.get("HUB_EMBED_MODEL", "text-embedding-3-large")
                    embed_dim = len(vecs[0]) if len(vecs) else 0
                    docs: List[Dict[str, Any]] = []
                    for c, v in zip(pending_chunk_batch, vecs):
                        slug = re.sub(r"[^a-z0-9]+", "-", c["url"].lower())[:64]
                        chunk_id = hashlib.sha1(f"web:{c['hash']}".encode()).hexdigest()
                        docs.append({
                            "_id": chunk_id,
                            "doc_id": f"web:{c['domain']}:{slug}",
                            "doc_title": c["title"][:200] or c["domain"],
                            "section": c["section"][:200],
                            "text": c["text"],
                            "embedding": v.tolist(),
                            "embedding_model": embed_model,
                            "embedding_dim": embed_dim,
                            "source": "web_ingest",
                            "source_url": c["url"],
                            "source_domain": c["domain"],
                            "source_title": c["title"][:200],
                            "source_section": c["section"][:200],
                            "text_hash": c["hash"],
                            "crawled_at": now,
                            "created_at": now,
                        })
                    try:
                        # bulk upsert; some hashes may exist due to race — ignore dup keys
                        from pymongo import InsertOne
                        ops = [InsertOne(d) for d in docs]
                        if ops:
                            res = await db.doc_chunks.bulk_write(ops, ordered=False)
                            summary["chunks_written"] = (res.inserted_count or 0)
                    except Exception as e:
                        msg = str(e)
                        if "duplicate key" in msg.lower():
                            # write what we can one by one
                            written = 0
                            for d in docs:
                                try:
                                    await db.doc_chunks.insert_one(d)
                                    written += 1
                                except Exception:
                                    pass
                            summary["chunks_written"] = written
                        else:
                            summary["status"] = "persist_failed"
                            summary["errors"].append({"stage": "persist", "err": msg[:200]})

                    # Re-load the index so new chunks are immediately searchable
                    try:
                        await rag.reload_index_from_db(db)
                    except Exception:
                        logger.exception("Failed to reload RAG index after web ingest")

            if summary["status"] == "running":
                summary["status"] = "ok"
        summary["finished_at"] = _now()
        summary["duration_sec"] = round(time.time() - started, 1)

        # Phase 24d.fix2 — always include cost_usd. Rate $0.13 per 1M tokens
        # for `text-embedding-3-large` (Hub AI public pricing). Dry-runs
        # report the estimated spend if executed.
        summary["cost_usd"] = round(summary.get("tokens_estimated", 0) / 1_000_000.0 * 0.13, 6)

        # Phase 24d.fix2 — dry-runs never report a "partial_*" status. The
        # partial-* states reserve themselves for live crawls that were
        # actually capped/budget-stopped. For a dry-run, hitting the cap is
        # the *successful* exit condition, so it's "ok".
        if dry_run and summary["status"].startswith("partial_"):
            summary["status"] = "ok"

        # Audit log
        try:
            await db.crawl_events.insert_one({**summary})
        except Exception:
            logger.exception("Failed to write crawl_events row")

    return summary


# ----- Seed registry -----
DEFAULT_SEEDS: List[Dict[str, Any]] = [
    {"site": "smifs.com",      "seed_url": "https://www.smifs.com/",                                                "max_depth": 3, "max_pages": 200, "allow_pdf": True,  "allowed_path_prefix": None},
    {"site": "sebi.gov.in",    "seed_url": "https://www.sebi.gov.in/",                                              "max_depth": 3, "max_pages": 400, "allow_pdf": True,  "allowed_path_prefix": None},
    {"site": "rbi.org.in",     "seed_url": "https://www.rbi.org.in/",                                               "max_depth": 3, "max_pages": 400, "allow_pdf": True,  "allowed_path_prefix": None},
    {"site": "amfi.in",        "seed_url": "https://www.amfi.in/",                                                  "max_depth": 3, "max_pages": 300, "allow_pdf": True,  "allowed_path_prefix": None},
    {"site": "nseindia.com",   "seed_url": "https://www.nseindia.com/learn/",                                       "max_depth": 3, "max_pages": 200, "allow_pdf": True,  "allowed_path_prefix": "/learn"},
    {"site": "bseindia.com",   "seed_url": "https://www.bseindia.com/static/investors/Investor_Education.aspx",      "max_depth": 3, "max_pages": 150, "allow_pdf": True,  "allowed_path_prefix": "/static/investors"},
]


def get_seed(site: str) -> Optional[Dict[str, Any]]:
    site = (site or "").lower().strip()
    if site == "all":
        return None
    for s in DEFAULT_SEEDS:
        if s["site"] == site:
            return s
    return None


async def ingest_url(db, url: str, *, dry_run: bool = False) -> Dict[str, Any]:
    """Ingest a single URL (no recursion). Useful for ad-hoc admin actions."""
    return await crawl_site(db, url, max_depth=0, max_pages=1, allow_pdf=True,
                            dry_run=dry_run)


async def ingest_status(db) -> Dict[str, Any]:
    """Aggregate per-domain stats for the admin Knowledge Ingestion tab."""
    rows: List[Dict[str, Any]] = []
    for s in DEFAULT_SEEDS:
        n = await db.doc_chunks.count_documents({"source": "web_ingest", "source_domain": s["site"]})
        last = await db.crawl_events.find_one({"domain": s["site"]}, sort=[("started_at", -1)])
        rows.append({
            "site_domain": s["site"],
            "seed_url": s["seed_url"],
            "chunks_count": n,
            "last_crawl_at": (last or {}).get("started_at"),
            "last_status": (last or {}).get("status"),
            "last_pages": (last or {}).get("pages_fetched", 0),
        })
    history_cur = db.crawl_events.find({}, {"_id": 0}).sort("started_at", -1).limit(20)
    history = await history_cur.to_list(length=20)
    # Strip massive `errors` arrays
    for h in history:
        if "errors" in h and isinstance(h["errors"], list):
            h["errors_count"] = len(h["errors"])
            h["errors"] = h["errors"][:3]
    total = await db.doc_chunks.count_documents({"source": "web_ingest"})
    return {
        "sources": rows,
        "history": history,
        "total_web_chunks": total,
        "generated_at": _now(),
    }
