"""Phase 11 — Knowledge Gaps aggregator.

Combines `hallucination_events` (Phase 9) with `conversations` messages
flagged `wm_fallback:true` (Phase 10) into a single "what couldn't we
answer" view for the admin content team.
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PRODUCT_HINTS = [
    ("AIF", re.compile(r"\b(aif|alternative\s+investment)\b", re.I)),
    ("PMS", re.compile(r"\b(pms|portfolio\s+management)\b", re.I)),
    ("IPO", re.compile(r"\bipo\b", re.I)),
    ("NCD", re.compile(r"\bncd\b", re.I)),
    ("MutualFund", re.compile(r"\b(mutual\s+fund|\bmf\b|\bsip\b|elss)\b", re.I)),
    ("SGB", re.compile(r"\b(sgb|sovereign\s+gold\s+bond)\b", re.I)),
    ("Demat", re.compile(r"\bdemat\b", re.I)),
    ("KYC", re.compile(r"\b(kyc|fatca)\b", re.I)),
    ("Tax", re.compile(r"\b(tax|ltcg|stcg|capital\s+gains)\b", re.I)),
]


def _asset_class(text: str) -> str:
    for label, pat in _PRODUCT_HINTS:
        if pat.search(text or ""):
            return label
    return "Other"


def normalize_question(q: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Used as a group-by key."""
    if not q:
        return ""
    s = q.lower().strip()
    s = re.sub(r"[^\w\s%₹]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:200]


def _since_iso(range_str: str) -> str:
    if range_str == "30d":
        delta = timedelta(days=30)
    elif range_str == "24h":
        delta = timedelta(days=1)
    else:
        delta = timedelta(days=7)  # default 7d
    return (datetime.now(timezone.utc) - delta).isoformat()


async def compute_gaps(db, *, range_str: str = "7d", role: str = "all",
                       limit: int = 100) -> Dict[str, Any]:
    """Return the full gap dashboard payload."""
    since = _since_iso(range_str)

    # 1) Hallucination events
    hal_q: Dict[str, Any] = {"created_at": {"$gte": since}}
    hal_cur = db.hallucination_events.find(hal_q, {"_id": 0})
    hal_events = await hal_cur.to_list(length=5000)

    # 2) WM-fallback messages from conversations — walk in Python to pick
    # the preceding user turn for each fallback assistant turn.
    conv_cur = db.conversations.find(
        {"messages.wm_fallback": True,
         "messages.ts": {"$gte": since}},
        {"_id": 0, "session_id": 1, "messages": 1},
    )
    wm_pairs: List[Dict[str, Any]] = []
    async for doc in conv_cur:
        msgs = doc.get("messages") or []
        for i, m in enumerate(msgs):
            if not m.get("wm_fallback"):
                continue
            if (m.get("ts") or "") < since:
                continue
            # find the immediately preceding user message
            prev_user = None
            for back in range(i - 1, -1, -1):
                if msgs[back].get("role") == "user":
                    prev_user = msgs[back].get("content") or ""
                    break
            wm_pairs.append({
                "session_id": doc.get("session_id"),
                "ts": m.get("ts"),
                "user_message": prev_user,
                "intent": m.get("intent"),
            })

    # 3) Optional role filter — look up session_type for each session_id
    if role in ("client", "employee", "visitor"):
        want_ids = {e.get("session_id") for e in hal_events if e.get("session_id")} \
                 | {p.get("session_id") for p in wm_pairs if p.get("session_id")}
        if want_ids:
            sess_cur = db.sessions.find(
                {"_id": {"$in": list(want_ids)}},
                {"_id": 1, "session_type": 1},
            )
            types = {s["_id"]: s.get("session_type") async for s in sess_cur}
            hal_events = [e for e in hal_events if types.get(e.get("session_id")) == role]
            wm_pairs = [p for p in wm_pairs if types.get(p.get("session_id")) == role]

    # 4) Aggregate + normalize
    totals = {
        "hallucination_events": len(hal_events),
        "wm_fallbacks": len(wm_pairs),
    }
    groups: Dict[str, Dict[str, Any]] = {}

    def _bucket(src_row: Dict[str, Any], source_kind: str) -> None:
        q = src_row.get("user_message") or ""
        key = normalize_question(q)
        if not key:
            return
        g = groups.setdefault(key, {
            "question_normalized": key,
            "sample_question": q[:200],
            "count": 0,
            "sources": {"hallucination_events": 0, "wm_fallbacks": 0},
            "roles": set(),
            "first_seen": src_row.get("ts") or src_row.get("created_at"),
            "last_seen": src_row.get("ts") or src_row.get("created_at"),
            "asset_class": _asset_class(q),
        })
        g["count"] += 1
        g["sources"][source_kind] = g["sources"].get(source_kind, 0) + 1
        ts = src_row.get("ts") or src_row.get("created_at") or ""
        if ts and (not g["first_seen"] or ts < g["first_seen"]):
            g["first_seen"] = ts
        if ts and (not g["last_seen"] or ts > g["last_seen"]):
            g["last_seen"] = ts

    for e in hal_events:
        _bucket({"user_message": e.get("user_message"), "created_at": e.get("created_at"),
                 "session_id": e.get("session_id")}, "hallucination_events")
    for p in wm_pairs:
        _bucket(p, "wm_fallbacks")

    # Resolve roles for each question by looking up session_type on the
    # sessions collection (we need it even without the top-level filter).
    session_ids = list({e.get("session_id") for e in hal_events if e.get("session_id")}
                       | {p.get("session_id") for p in wm_pairs if p.get("session_id")})
    types_map: Dict[str, str] = {}
    if session_ids:
        sess_cur = db.sessions.find(
            {"_id": {"$in": session_ids}}, {"_id": 1, "session_type": 1},
        )
        async for s in sess_cur:
            if s.get("session_type"):
                types_map[s["_id"]] = s["session_type"]
    # Re-walk to collect roles per question
    def _tag_roles(rows: List[Dict[str, Any]]) -> None:
        for r in rows:
            q = r.get("user_message") or ""
            k = normalize_question(q)
            if not k or k not in groups:
                continue
            t = types_map.get(r.get("session_id"))
            if t:
                groups[k]["roles"].add(t)
    _tag_roles(hal_events)
    _tag_roles([{"user_message": p.get("user_message"), "session_id": p.get("session_id")} for p in wm_pairs])

    # Apply resolved-status filter
    resolved = {r["question_normalized"] async for r in db.knowledge_gap_status.find({"resolved": True}, {"_id": 0, "question_normalized": 1})}
    for k, g in groups.items():
        g["resolved"] = k in resolved

    # Materialise
    rows = []
    for g in groups.values():
        rows.append({
            **g,
            "roles": sorted(list(g["roles"])),
        })
    rows.sort(key=lambda r: (r["count"], r.get("last_seen") or ""), reverse=True)

    # By-asset-class
    asset_counts: Dict[str, int] = {}
    for r in rows:
        asset_counts[r["asset_class"]] = asset_counts.get(r["asset_class"], 0) + r["count"]
    by_asset = sorted(
        [{"asset_class": k, "count": v} for k, v in asset_counts.items()],
        key=lambda x: x["count"], reverse=True,
    )

    totals["unique_questions"] = len(rows)
    totals["resolved_questions"] = sum(1 for r in rows if r["resolved"])
    return {
        "range": range_str,
        "role": role,
        "totals": totals,
        "top_questions": rows[:limit],
        "by_asset_class": by_asset,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def mark_resolved(db, *, question_normalized: str, resolved: bool,
                        actor: Optional[str] = None) -> Dict[str, Any]:
    q = (question_normalized or "").strip().lower()
    if not q:
        raise ValueError("question_normalized required")
    now = datetime.now(timezone.utc).isoformat()
    await db.knowledge_gap_status.update_one(
        {"_id": q},
        {"$set": {
            "_id": q,
            "question_normalized": q,
            "resolved": bool(resolved),
            "resolved_at": now if resolved else None,
            "resolved_by": actor,
            "updated_at": now,
        }},
        upsert=True,
    )
    return {"question_normalized": q, "resolved": bool(resolved), "updated_at": now}
