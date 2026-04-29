"""Phase 7 — session lifecycle: 2-minute idle expiry + identity-keyed rehydration.

Design notes
============
A session is "frozen" (`lifecycle="expired"`) after 120s of inactivity rather
than deleted. If the same identity shows up later (same employee, client, or
lead email/phone) we offer a resume. Cross-user resume is impossible because
we match on HMAC-SHA256 hashes of identifiers — the new session must carry at
least one hash that equals a hash on the prior session.

Public API
----------
* `maybe_expire_and_mint(db, session_id)` — call at the start of every turn
* `rehydration_candidates_for_session(db, sid)` — compute offers for the FE
* `resume(db, current_id, prior_session_id)` — identity-checked merge
* `decline_all_priors(db, current_id)` — mark matching priors as ended
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

IDLE_TIMEOUT_SECONDS = 120
IDENTITY_HASH_FIELDS = ("emp_id_hash", "ucc_hash", "pan_hash", "email_hash", "phone_hash")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def bump_session_activity(db, session_id: str) -> None:
    """Mark the session as active and stamp updated_at_dt. Safe to call
    idempotently at the end of every turn."""
    now_dt = _now()
    await db.sessions.update_one(
        {"_id": session_id},
        {"$set": {
            "updated_at": now_dt.isoformat(),
            "updated_at_dt": now_dt,
            "lifecycle": "active",
        }},
    )


async def maybe_expire_and_mint(db, session_id: Optional[str]) -> Dict[str, Any]:
    """Decide whether the incoming session_id has idled past the threshold.

    Returns::
        {
          "session_id":        <effective session id to use for this turn>,
          "prior_session_id":  <the expired id, or None>,
          "expired":           True | False,
          "resume_offer":      [ ... ] | None,  # 1-3 offers based on prior hashes
        }
    """
    result: Dict[str, Any] = {
        "session_id": session_id or str(uuid.uuid4()),
        "prior_session_id": None,
        "expired": False,
        "resume_offer": None,
    }
    if not session_id:
        return result

    sess = await db.sessions.find_one({"_id": session_id}, {"_id": 0})
    if not sess:
        # Client sent a stale/unknown session_id — just honour it as-is;
        # get_or_create_session_row will create a fresh row on first use.
        return result

    lifecycle = sess.get("lifecycle") or "active"

    # Determine idle age.
    updated = sess.get("updated_at_dt")
    idle_expired = False
    if isinstance(updated, datetime):
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = (_now() - updated).total_seconds()
        idle_expired = age > IDLE_TIMEOUT_SECONDS

    if lifecycle == "ended":
        # Never resume an explicitly ended session; just mint fresh.
        new_id = str(uuid.uuid4())
        result.update({"session_id": new_id, "prior_session_id": session_id, "expired": True})
        return result

    if lifecycle == "expired" or idle_expired:
        # Freeze the old row (idempotent), mint a new id.
        if lifecycle != "expired":
            await db.sessions.update_one(
                {"_id": session_id, "lifecycle": {"$ne": "ended"}},
                {"$set": {"lifecycle": "expired"}},
            )
        new_id = str(uuid.uuid4())
        result["session_id"] = new_id
        result["prior_session_id"] = session_id
        result["expired"] = True
        offers = await _candidates_from_hashes(db, sess, exclude_id=session_id,
                                               include_expired=True)
        # Also include the just-expired session itself as the top candidate
        # (it has the richest continuity), if it has any identity hash.
        if _any_hash(sess):
            offers = [_offer_from_session(db, session_id, sess, await _summary_for(db, session_id))] + offers
            # De-dupe by prior_session_id
            seen = set()
            deduped = []
            for o in offers:
                pid = o["prior_session_id"]
                if pid in seen:
                    continue
                seen.add(pid)
                deduped.append(o)
            offers = deduped[:3]
        if offers:
            result["resume_offer"] = offers
        return result

    # Active — no change.
    return result


def _any_hash(sess: Dict[str, Any]) -> bool:
    return any(sess.get(f) for f in IDENTITY_HASH_FIELDS)


async def _summary_for(db, sid: str) -> Dict[str, Any]:
    convo = await db.conversations.find_one({"session_id": sid}, {"_id": 0, "messages": 1})
    msgs = (convo or {}).get("messages", [])
    last_assistant = ""
    for m in reversed(msgs):
        if m.get("role") == "assistant":
            c = (m.get("content") or "").strip()
            if c:
                last_assistant = c.split("\n")[0][:200]
                break
    return {"last_assistant": last_assistant, "count": len(msgs)}


def _offer_from_session(db, sid: str, sess: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    # `db` unused here but kept for signature symmetry.
    return {
        "prior_session_id": sid,
        "ended_at": sess.get("updated_at"),
        "summary": summary.get("last_assistant") or "(no prior assistant reply)",
        "message_count": summary.get("count", 0),
        "session_type": sess.get("session_type", "visitor"),
    }


async def _candidates_from_hashes(db, reference_sess: Dict[str, Any], exclude_id: Optional[str] = None,
                                  include_expired: bool = True, limit: int = 3) -> List[Dict[str, Any]]:
    or_clauses = [{f: v} for f in IDENTITY_HASH_FIELDS if (v := reference_sess.get(f))]
    if not or_clauses:
        return []
    q: Dict[str, Any] = {"$or": or_clauses}
    if include_expired:
        q["lifecycle"] = {"$ne": "ended"}
    else:
        q["lifecycle"] = "active"
    if exclude_id:
        q["_id"] = {"$ne": exclude_id}
    cursor = db.sessions.find(q, {"_id": 1, "updated_at": 1, "updated_at_dt": 1,
                                  "session_type": 1, "lifecycle": 1}).sort("updated_at_dt", -1).limit(limit * 2)
    rows = await cursor.to_list(length=limit * 2)
    offers: List[Dict[str, Any]] = []
    for r in rows:
        sid = r.get("_id")
        if not sid:
            continue
        summary = await _summary_for(db, sid)
        if summary["count"] == 0:
            continue  # Skip empty shells
        offers.append(_offer_from_session(db, sid, r, summary))
        if len(offers) >= limit:
            break
    return offers


async def rehydration_candidates_for_session(db, session_id: str) -> List[Dict[str, Any]]:
    sess = await db.sessions.find_one({"_id": session_id}, {"_id": 0})
    if not sess:
        return []
    return await _candidates_from_hashes(db, sess, exclude_id=session_id, include_expired=True)


async def resume(db, current_id: str, prior_session_id: str) -> Dict[str, Any]:
    """Copy prior conversation's messages into the current conversation and
    mark the prior session as ended. Cross-user resume is denied by comparing
    identity hash overlap between current and prior session rows.

    Raises:
        PermissionError — identity hashes don't overlap
        ValueError — unknown session id
    """
    if current_id == prior_session_id:
        raise ValueError("Current and prior session ids are the same")
    current = await db.sessions.find_one({"_id": current_id}, {"_id": 0})
    prior = await db.sessions.find_one({"_id": prior_session_id}, {"_id": 0})
    if not prior:
        raise ValueError("Prior session not found")

    current_hashes = {f: (current or {}).get(f) for f in IDENTITY_HASH_FIELDS if (current or {}).get(f)}
    prior_hashes = {f: prior.get(f) for f in IDENTITY_HASH_FIELDS if prior.get(f)}
    overlap = any(current_hashes.get(f) == prior_hashes.get(f) and current_hashes.get(f) for f in IDENTITY_HASH_FIELDS)
    if not overlap:
        raise PermissionError("Identity keys do not match — cross-user resume denied")

    prior_convo = await db.conversations.find_one({"session_id": prior_session_id}, {"_id": 0, "messages": 1})
    current_convo = await db.conversations.find_one({"session_id": current_id}, {"_id": 0, "messages": 1})
    prior_msgs = (prior_convo or {}).get("messages", [])
    current_msgs = (current_convo or {}).get("messages", [])

    # Tag each prior message with an origin marker so the FE can visually
    # separate the rehydrated context (optional — cosmetic).
    for m in prior_msgs:
        m.setdefault("rehydrated_from", prior_session_id)
    merged = prior_msgs + current_msgs
    await db.conversations.update_one(
        {"session_id": current_id},
        {"$set": {"messages": merged, "updated_at": _now().isoformat()}},
        upsert=True,
    )
    await db.sessions.update_one(
        {"_id": prior_session_id},
        {"$set": {"lifecycle": "ended", "ended_at": _now().isoformat()}},
    )
    # Best-effort archive the prior session if it was verified
    try:
        if prior.get("auth_state") == "verified":
            from archives import snapshot_on_verify
            await snapshot_on_verify(db, prior_session_id)
    except Exception:
        logger.exception("archive on resume (non-fatal)")
    return {
        "ok": True,
        "prior_session_id": prior_session_id,
        "current_session_id": current_id,
        "merged_message_count": len(merged),
        "rehydrated_message_count": len(prior_msgs),
    }


async def decline_all_priors(db, current_id: str) -> int:
    """Mark any prior session with overlapping identity as ended."""
    current = await db.sessions.find_one({"_id": current_id}, {"_id": 0})
    if not current:
        return 0
    or_clauses = [{f: v} for f in IDENTITY_HASH_FIELDS if (v := current.get(f))]
    if not or_clauses:
        return 0
    res = await db.sessions.update_many(
        {"$or": or_clauses, "_id": {"$ne": current_id}, "lifecycle": {"$ne": "ended"}},
        {"$set": {"lifecycle": "ended", "ended_at": _now().isoformat()}},
    )
    return res.modified_count
