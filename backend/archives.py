"""Phase 6 — Conversation archive collection.

When a session reaches `auth_state=verified`, we snapshot a sanitized
transcript into `session_archives` so the conversation survives the 24h TTL
on `sessions` and can later be selectively ingested into the RAG corpus.

Privacy
=======
* Transcripts are pulled from `conversations` which are already PAN-scrubbed
  (orchestrator runs identity.redact_pan_in_text on every message before
  persisting). The archive is therefore PAN-free by construction.
* `consent_to_ingest`: defaults to True for employees, False for clients.
  The admin-only `ingest_archives_to_rag` endpoint only consumes archives
  with consent_to_ingest=True.
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def snapshot_on_verify(db, session_id: str) -> Optional[Dict[str, Any]]:
    """Create or refresh the archive row for a verified session."""
    sess = await db.sessions.find_one({"_id": session_id}, {"_id": 0})
    if not sess:
        return None
    convo = await db.conversations.find_one({"session_id": session_id}, {"_id": 0})
    if not convo:
        return None
    ident = sess.get("identity") or {}
    role = ident.get("type") or sess.get("session_type") or "visitor"
    consent_default = True if role == "employee" else False

    archive: Dict[str, Any] = {
        "_id": session_id,
        "session_id": session_id,
        "session_type": role,
        "identity_summary": {
            "type": role,
            "first_name": ident.get("first_name"),
            "name": ident.get("name"),
            "employee_id": ident.get("employee_id"),
            "ucc": ident.get("ucc"),
            "designation": ident.get("designation"),
            "department": ident.get("department"),
            "branch_name": ident.get("branch_name"),
            "rm_name": ident.get("rm_name"),
        },
        "messages": convo.get("messages", []),
        "intents_used": _extract_intents(convo.get("messages", [])),
        "lead_id": await _find_lead_id(db, session_id),
        "verified_at": sess.get("verified_at"),
        "archived_at": _now_iso(),
        "consent_to_ingest": consent_default,
        "ingested_to_rag": False,
        "rag_chunks_added": 0,
    }
    # Upsert so re-verifications refresh the snapshot
    await db.session_archives.find_one_and_update(
        {"_id": session_id},
        {"$set": archive},
        upsert=True,
    )
    return archive


def _extract_intents(messages: List[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for m in messages:
        i = m.get("intent")
        if i and i not in seen:
            seen.append(i)
    return seen


async def _find_lead_id(db, session_id: str) -> Optional[str]:
    lead = await db.leads.find_one({"session_id": session_id}, {"_id": 0, "lead_id": 1})
    return (lead or {}).get("lead_id")


async def list_archives(db, role: str = "all", limit: int = 50,
                        q: Optional[str] = None,
                        date_from: Optional[str] = None,
                        date_to: Optional[str] = None,
                        offset: int = 0) -> Dict[str, Any]:
    """Phase 11 — filterable + searchable archive list.

    Search across identity_summary.name / first_name / ucc / employee_id /
    intents_used + free-text email-hash lookup when `q` looks like an email.
    """
    import identity as _id
    mongo_q: Dict[str, Any] = {}
    if role in ("employee", "client", "visitor"):
        mongo_q["session_type"] = role
    if date_from:
        mongo_q.setdefault("archived_at", {})["$gte"] = date_from
    if date_to:
        mongo_q.setdefault("archived_at", {})["$lte"] = date_to
    if q and q.strip():
        qs = q.strip()
        or_clauses: List[Dict[str, Any]] = [
            {"identity_summary.name": {"$regex": re.escape(qs), "$options": "i"}},
            {"identity_summary.first_name": {"$regex": re.escape(qs), "$options": "i"}},
            {"identity_summary.ucc": qs},
            {"identity_summary.employee_id": qs},
            {"intents_used": {"$regex": re.escape(qs), "$options": "i"}},
        ]
        # If the query looks like an email, add a hash match on the session row.
        if "@" in qs and "." in qs:
            try:
                eh = _id.email_hash(qs.lower())
                or_clauses.append({"email_hash": eh})
            except Exception:
                pass
        mongo_q["$or"] = or_clauses
    total = await db.session_archives.count_documents(mongo_q)
    cursor = db.session_archives.find(
        mongo_q, {"_id": 0, "messages": 0},  # exclude messages for the list view
    ).sort("archived_at", -1).skip(max(0, offset)).limit(min(limit, 200))
    rows = await cursor.to_list(length=limit)
    return {
        "archives": rows,
        "count": len(rows),
        "total": total,
        "offset": offset,
        "limit": limit,
        "filters": {"role": role, "q": q, "date_from": date_from, "date_to": date_to},
    }


async def get_archive(db, archive_id: str) -> Optional[Dict[str, Any]]:
    return await db.session_archives.find_one({"_id": archive_id}, {"_id": 0})


async def update_consent(db, archive_id: str, consent: bool) -> Optional[Dict[str, Any]]:
    await db.session_archives.update_one(
        {"_id": archive_id},
        {"$set": {"consent_to_ingest": bool(consent), "consent_updated_at": _now_iso()}},
    )
    return await get_archive(db, archive_id)


# ---------- ingest archives to RAG ----------
def _convo_pairs(messages: List[Dict[str, Any]]) -> List[Tuple[str, str, Optional[str]]]:
    """Walk the message list and return (question, answer, intent) tuples for
    each user→assistant turn."""
    pairs: List[Tuple[str, str, Optional[str]]] = []
    last_q: Optional[str] = None
    for m in messages:
        role = m.get("role")
        if role == "user":
            last_q = (m.get("content") or "").strip()
        elif role == "assistant" and last_q:
            ans = (m.get("content") or "").strip()
            intent = m.get("intent")
            # Skip auth/verification turns and short responses
            if intent and intent.startswith("AUTH_"):
                last_q = None
                continue
            if len(ans) >= 80 and len(last_q) >= 8:
                pairs.append((last_q, ans, intent))
            last_q = None
    return pairs


async def ingest_archives_to_rag(db, dry_run: bool = False, role: str = "all") -> Dict[str, Any]:
    """Pick archives with consent_to_ingest=True AND ingested_to_rag=False,
    convert each conversational pair to a doc chunk, embed via the active
    embedder, and add to `doc_chunks` with source='session_archive'."""
    import rag  # late import — rag pulls heavy deps

    q: Dict[str, Any] = {"consent_to_ingest": True, "ingested_to_rag": {"$ne": True}}
    if role in ("employee", "client"):
        q["session_type"] = role
    cursor = db.session_archives.find(q)
    archives = await cursor.to_list(length=500)

    summary: Dict[str, Any] = {
        "scanned": len(archives),
        "ingested": 0,
        "chunks_added": 0,
        "skipped": [],
        "dry_run": dry_run,
    }
    for arc in archives:
        sid = arc.get("_id") or arc.get("session_id")
        pairs = _convo_pairs(arc.get("messages", []))
        if not pairs:
            summary["skipped"].append({"session_id": sid, "reason": "no_qualifying_pairs"})
            continue
        chunks: List[Dict[str, Any]] = []
        for i, (q_text, a_text, intent) in enumerate(pairs):
            text = f"Q: {q_text}\nA: {a_text}"
            chunks.append({
                "doc_id": f"archive_{sid}",
                "doc_title": f"Conversation archive · {arc.get('session_type','session')} · {sid[:8]}",
                "section": f"qa_{i+1}",
                "text": text,
            })
        if dry_run:
            summary["chunks_added"] += len(chunks)
            summary["ingested"] += 1
            continue
        added = await rag.ingest_extra_chunks(
            db, chunks, source="session_archive", filename=f"archive_{sid}.md",
        )
        await db.session_archives.update_one(
            {"_id": sid},
            {"$set": {"ingested_to_rag": True, "rag_chunks_added": added,
                      "ingested_at": _now_iso()}},
        )
        summary["ingested"] += 1
        summary["chunks_added"] += added
    return summary
