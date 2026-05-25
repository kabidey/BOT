"""Phase 22 — Device-fingerprint fraud detection.

Detects client-data harvesting attacks where a single device sequentially
verifies as multiple distinct clients (PAN-by-PAN scraping) by tracking
per-fingerprint identity bindings + an exponentially-decayed score across
several signal axes (rapid burst, daily saturation, lifetime saturation
without RM linkage, IP-network jumps, UA rotation). Designed to be silent:
the middleware ((`check_fingerprint_block`)) returns benign soft-error
responses on blocked fingerprints — no 403, no error envelope, no banner.
False positives are recovered via the admin Fraud Watch tab.

See `SECURITY_FINGERPRINTING.md` for the threat model + tuning guidance.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("fingerprint_guard")

# ──────────────────────────────────────────────────────────────────────────────
#  Thresholds (env-controlled — tune in prod without redeploy)
# ──────────────────────────────────────────────────────────────────────────────
def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default

def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default

def BLOCK_SCORE() -> float:    return _f("FPRINT_BLOCK_SCORE", 75)
def FLAG_SCORE() -> float:     return _f("FPRINT_FLAG_SCORE", 40)
def RAPID_WINDOW_MIN() -> int: return _i("FPRINT_RAPID_WINDOW_MIN", 120)
def RAPID_LIMIT() -> int:      return _i("FPRINT_RAPID_CLIENT_LIMIT", 3)
def DAILY_LIMIT() -> int:      return _i("FPRINT_DAILY_CLIENT_LIMIT", 5)
def LIFETIME_LIMIT() -> int:   return _i("FPRINT_LIFETIME_CLIENT_LIMIT_NO_RM", 10)
HALF_LIFE_DAYS = 7.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _decay_weight(when: Optional[datetime]) -> float:
    """Exponential time-decay weight (half-life 7 days)."""
    if not when:
        return 0.0
    age_days = max(0.0, (_now() - when).total_seconds() / 86400.0)
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def _ip_network_prefix(ip: str) -> str:
    """Coarse "network" identifier — first two octets of an IPv4. Used as a
    cheap geolocation proxy: IPs from completely different /16 subnets seen
    within minutes is a strong "impossible without VPN" signal.

    We deliberately avoid bundling MaxMind GeoLite2 (binary blob, license
    headaches) and instead lean on network-prefix variance plus user-agent
    rotation — both detect the same attacker behaviour."""
    if not ip:
        return ""
    parts = ip.split(".")
    if len(parts) >= 2 and all(p.isdigit() for p in parts[:2]):
        return f"{parts[0]}.{parts[1]}.0.0/16"
    # IPv6 — first 32 bits
    if ":" in ip:
        chunks = ip.split(":")
        return ":".join(chunks[:2]) + "::/32"
    return ip


# ──────────────────────────────────────────────────────────────────────────────
#  Mongo schema bootstrap
# ──────────────────────────────────────────────────────────────────────────────
async def ensure_indexes(db) -> None:
    try:
        await db.device_fingerprints.create_index("fingerprint_hash", unique=True)
        await db.device_fingerprints.create_index("blocked")
        await db.device_fingerprints.create_index("last_seen")
        await db.device_fingerprint_audit.create_index([("fingerprint_hash", 1),
                                                         ("ts", -1)])
        # 180-day TTL on the audit trail
        await db.device_fingerprint_audit.create_index(
            "ts_dt", expireAfterSeconds=180 * 86400)
    except Exception:
        logger.exception("device_fingerprints index bootstrap failed (non-fatal)")


# ──────────────────────────────────────────────────────────────────────────────
#  Public API
# ──────────────────────────────────────────────────────────────────────────────
async def get_fingerprint(db, fp_hash: str) -> Optional[Dict[str, Any]]:
    if not fp_hash:
        return None
    return await db.device_fingerprints.find_one({"fingerprint_hash": fp_hash},
                                                   {"_id": 0})


async def is_blocked(db, fp_hash: str) -> bool:
    """Fast hot-path check used by the silent-block middleware. Trusted
    fingerprints are NEVER reported as blocked even if `blocked:true` lingers
    on the row (defence-in-depth against operator error)."""
    if not fp_hash:
        return False
    row = await db.device_fingerprints.find_one(
        {"fingerprint_hash": fp_hash},
        {"blocked": 1, "admin_trusted": 1, "_id": 0},
    )
    if not row:
        return False
    if row.get("admin_trusted"):
        return False
    return bool(row.get("blocked"))


async def record_request_signal(db, fp_hash: str, *, ip: str = "", ua: str = "",
                                tz: str = "", screen: str = "") -> None:
    """Cheap upsert called from the middleware on every authenticated request.
    Just tracks IP/UA/tz/screen variety + bumps `last_seen` — does NOT score
    or block. Identity bindings flow through `record_identity_binding` below.
    """
    if not fp_hash:
        return
    now_iso = _iso()
    update: Dict[str, Any] = {
        "$setOnInsert": {
            "fingerprint_hash": fp_hash,
            "first_seen": now_iso,
            "client_identities": [],
            "employee_identities": [],
            "ips_seen": [],
            "user_agents_seen": [],
            "timezones_seen": [],
            "screens_seen": [],
            "blocked": False,
            "admin_trusted": False,
            "blocked_at": None,
            "blocked_reason": None,
            "admin_trusted_by": None,
            "admin_trusted_at": None,
            "notes": [],
            "suspicious_score": 0.0,
            "score_breakdown": {},
        },
        "$set": {"last_seen": now_iso},
    }
    try:
        await db.device_fingerprints.update_one(
            {"fingerprint_hash": fp_hash}, update, upsert=True)
    except Exception:
        logger.exception("fingerprint upsert failed (non-fatal)")
        return
    # Side-channel updates (IP/UA/tz/screen variety) — best-effort, no await
    # of failure handling needed.
    try:
        await _bump_signal(db, fp_hash, "ips_seen", "ip", ip,
                            extra={"network_prefix": _ip_network_prefix(ip)})
        await _bump_signal(db, fp_hash, "user_agents_seen", "ua", (ua or "")[:240])
        await _bump_signal(db, fp_hash, "timezones_seen", "tz", tz)
        await _bump_signal(db, fp_hash, "screens_seen", "res", screen)
    except Exception:
        logger.debug("signal-array bump failed", exc_info=True)


async def _bump_signal(db, fp_hash: str, array: str, key: str, value: str,
                       *, extra: Optional[Dict[str, Any]] = None) -> None:
    if not value:
        return
    now_iso = _iso()
    # Try to update existing entry.
    res = await db.device_fingerprints.update_one(
        {"fingerprint_hash": fp_hash, f"{array}.{key}": value},
        {"$set": {f"{array}.$.last_at": now_iso},
         "$inc": {f"{array}.$.count": 1}},
    )
    if res.modified_count:
        return
    new_entry: Dict[str, Any] = {key: value, "first_at": now_iso, "last_at": now_iso, "count": 1}
    if extra:
        new_entry.update(extra)
    await db.device_fingerprints.update_one(
        {"fingerprint_hash": fp_hash},
        {"$push": {array: new_entry}},
    )


async def record_identity_binding(db, fp_hash: str, *, identity_type: str,
                                   identity_key: str,
                                   identity_rm_name: Optional[str] = None,
                                   ip: str = "", ua: str = "", tz: str = "",
                                   screen: str = "") -> Dict[str, Any]:
    """Called from `_finalise_verified` immediately after PAN matches OrgLens.
    Returns the updated fingerprint row (post-rebind) so the caller can pick up
    the new score without an extra round-trip.

    `identity_type`: "client" or "employee"
    `identity_key`: UCC (clients) or employee_id (employees)
    `identity_rm_name`: for client bindings, the assigned RM's name (used by
       the RM-linkage mitigator). Pass None for employee bindings.
    """
    if not fp_hash or not identity_type or not identity_key:
        return {}
    arr = "client_identities" if identity_type == "client" else "employee_identities"
    key_field = "ucc" if identity_type == "client" else "employee_id"
    now_iso = _iso()
    # Ensure row exists first.
    await record_request_signal(db, fp_hash, ip=ip, ua=ua, tz=tz, screen=screen)
    # Try update on existing entry.
    res = await db.device_fingerprints.update_one(
        {"fingerprint_hash": fp_hash, f"{arr}.{key_field}": identity_key},
        {"$set": {f"{arr}.$.last_at": now_iso},
         "$inc": {f"{arr}.$.verification_count": 1}},
    )
    if not res.modified_count:
        new_entry = {key_field: identity_key, "first_at": now_iso,
                      "last_at": now_iso, "verification_count": 1}
        if identity_type == "client" and identity_rm_name:
            new_entry["rm_name"] = identity_rm_name
        await db.device_fingerprints.update_one(
            {"fingerprint_hash": fp_hash},
            {"$push": {arr: new_entry}},
        )
    # Audit trail (append-only).
    try:
        await db.device_fingerprint_audit.insert_one({
            "fingerprint_hash": fp_hash,
            "ts": now_iso,
            "ts_dt": _now(),
            "kind": "identity_binding",
            "identity_type": identity_type,
            "identity_key_masked": _mask_identity(identity_type, identity_key),
            "ip": ip, "ua": (ua or "")[:240], "tz": tz, "screen": screen,
        })
    except Exception:
        logger.debug("audit insert failed", exc_info=True)
    # Recompute score + persist.
    row = await db.device_fingerprints.find_one(
        {"fingerprint_hash": fp_hash}, {"_id": 0})
    score, breakdown = compute_suspicious_score(row or {})
    update_fields = {"suspicious_score": score, "score_breakdown": breakdown,
                      "last_seen": now_iso}
    blocked_now = False
    if (not row.get("admin_trusted")) and (not row.get("blocked")) and score >= BLOCK_SCORE():
        update_fields["blocked"] = True
        update_fields["blocked_at"] = now_iso
        update_fields["blocked_reason"] = f"auto:score={score:.1f}"
        blocked_now = True
    await db.device_fingerprints.update_one(
        {"fingerprint_hash": fp_hash}, {"$set": update_fields})
    # Security events
    try:
        if blocked_now:
            await db.security_events.insert_one({
                "kind": "fingerprint_fraud_block",
                "session_id": None,
                "role_state_value": None,
                "user_message": f"score={score:.1f} bind={identity_type}:{_mask_identity(identity_type, identity_key)}",
                "action": "auto_block",
                "ts": now_iso,
                "ts_dt": _now(),
                "created_at": now_iso,
                "fingerprint_hash": fp_hash,
                "score": score, "breakdown": breakdown,
            })
        elif score >= FLAG_SCORE():
            await db.security_events.insert_one({
                "kind": "fingerprint_fraud_flag",
                "session_id": None,
                "role_state_value": None,
                "user_message": f"score={score:.1f}",
                "action": "flag",
                "ts": now_iso,
                "ts_dt": _now(),
                "created_at": now_iso,
                "fingerprint_hash": fp_hash,
                "score": score, "breakdown": breakdown,
            })
    except Exception:
        logger.debug("security_events write failed", exc_info=True)
    return (await db.device_fingerprints.find_one({"fingerprint_hash": fp_hash},
                                                    {"_id": 0})) or {}


def _mask_identity(identity_type: str, key: str) -> str:
    if not key:
        return ""
    if identity_type == "client":
        return key[:2] + "***" + key[-2:] if len(key) > 4 else "***"
    return key[:6] + "***" if len(key) > 6 else "***"


# ──────────────────────────────────────────────────────────────────────────────
#  Score computation (pure function)
# ──────────────────────────────────────────────────────────────────────────────
def compute_suspicious_score(row: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    """Compute a 0-100 suspicion score with decayed contributions.
    Returns (total_score, breakdown).
    """
    clients = row.get("client_identities") or []
    employees = row.get("employee_identities") or []
    ips = row.get("ips_seen") or []
    uas = row.get("user_agents_seen") or []
    now = _now()
    rapid_window_sec = RAPID_WINDOW_MIN() * 60

    # Rapid burst — count distinct client UCCs first-seen within the window.
    rapid = 0
    for c in clients:
        first = _parse_iso(c.get("first_at"))
        if first and (now - first).total_seconds() <= rapid_window_sec:
            rapid += 1
    rapid_score = max(0, rapid - 1) * 25  # 1st client is free

    # 24h saturation
    daily = 0
    for c in clients:
        first = _parse_iso(c.get("first_at"))
        if first and (now - first).total_seconds() <= 86400:
            daily += 1
    daily_score = max(0, daily - 2) * 15

    # Lifetime saturation with NO RM linkage
    employee_names = {(e.get("name") or "").strip().lower()
                       for e in employees if isinstance(e, dict)}
    # Lifetime client count, decayed
    client_total = 0.0
    rm_matched = 0
    for c in clients:
        w = _decay_weight(_parse_iso(c.get("first_at")))
        client_total += w
        rm = (c.get("rm_name") or "").strip().lower()
        if rm and rm in employee_names:
            rm_matched += 1
    no_rm_score = 0.0
    if client_total > LIFETIME_LIMIT():
        no_rm_score = (client_total - LIFETIME_LIMIT()) * 10

    # IP geographic impossibility — distinct /16 prefixes within 10 min.
    prefixes_recent: Dict[str, datetime] = {}
    for ip in ips:
        last = _parse_iso(ip.get("last_at"))
        if last and (now - last).total_seconds() <= 600:
            prefixes_recent[ip.get("network_prefix") or _ip_network_prefix(ip.get("ip", ""))] = last
    ip_jump_score = 50 if len({k for k in prefixes_recent.keys() if k}) >= 2 else 0

    # UA rotation in 24h
    ua_count_24h = sum(1 for u in uas
                        if (last := _parse_iso(u.get("last_at"))) and
                        (now - last).total_seconds() <= 86400)
    ua_rot_score = 10 if ua_count_24h >= 3 else 0

    raw_score = (rapid_score + daily_score + no_rm_score
                  + ip_jump_score + ua_rot_score)

    # Mitigators
    rm_mitigator = 0
    if clients and rm_matched / max(1, len(clients)) >= 0.5:
        rm_mitigator = 20
    asn_mitigator = 0
    if ips:
        all_prefixes = {ip.get("network_prefix") or _ip_network_prefix(ip.get("ip", ""))
                         for ip in ips}
        all_prefixes.discard("")
        if len(all_prefixes) == 1:
            asn_mitigator = 10

    total = max(0.0, raw_score - rm_mitigator - asn_mitigator)
    total = min(100.0, total)

    breakdown = {
        "rapid_burst": rapid_score,
        "daily_saturation": daily_score,
        "lifetime_no_rm": no_rm_score,
        "ip_jump": ip_jump_score,
        "ua_rotation": ua_rot_score,
        "rm_mitigator": -rm_mitigator,
        "single_network_mitigator": -asn_mitigator,
        "rapid_count": rapid,
        "daily_count": daily,
        "lifetime_decayed_count": round(client_total, 2),
        "rm_matched": rm_matched,
    }
    return round(total, 2), breakdown


# ──────────────────────────────────────────────────────────────────────────────
#  Admin actions
# ──────────────────────────────────────────────────────────────────────────────
async def block(db, fp_hash: str, *, reason: str, by_token_hash: str) -> bool:
    if not fp_hash:
        return False
    now_iso = _iso()
    res = await db.device_fingerprints.update_one(
        {"fingerprint_hash": fp_hash},
        {"$set": {"blocked": True, "blocked_at": now_iso,
                   "blocked_reason": reason}},
        upsert=False,
    )
    await db.device_fingerprint_audit.insert_one({
        "fingerprint_hash": fp_hash, "ts": now_iso, "ts_dt": _now(),
        "kind": "admin_block", "reason": reason, "by_token_hash": by_token_hash,
    })
    return res.modified_count > 0


async def unblock(db, fp_hash: str, *, reason: str, by_token_hash: str) -> bool:
    if not fp_hash:
        return False
    now_iso = _iso()
    res = await db.device_fingerprints.update_one(
        {"fingerprint_hash": fp_hash},
        {"$set": {"blocked": False, "blocked_at": None,
                   "blocked_reason": None}},
        upsert=False,
    )
    await db.device_fingerprint_audit.insert_one({
        "fingerprint_hash": fp_hash, "ts": now_iso, "ts_dt": _now(),
        "kind": "admin_unblock", "reason": reason, "by_token_hash": by_token_hash,
    })
    return res.modified_count > 0


async def set_trust(db, fp_hash: str, *, trusted: bool, reason: str,
                     by_token_hash: str) -> bool:
    if not fp_hash:
        return False
    now_iso = _iso()
    update: Dict[str, Any] = {"admin_trusted": trusted}
    if trusted:
        update["admin_trusted_by"] = by_token_hash
        update["admin_trusted_at"] = now_iso
        update["blocked"] = False  # trust implies unblock
        update["blocked_at"] = None
        update["blocked_reason"] = None
    else:
        update["admin_trusted_by"] = None
        update["admin_trusted_at"] = None
    res = await db.device_fingerprints.update_one(
        {"fingerprint_hash": fp_hash}, {"$set": update}, upsert=False)
    await db.device_fingerprint_audit.insert_one({
        "fingerprint_hash": fp_hash, "ts": now_iso, "ts_dt": _now(),
        "kind": "admin_trust" if trusted else "admin_untrust",
        "reason": reason, "by_token_hash": by_token_hash,
    })
    return res.modified_count > 0


async def add_note(db, fp_hash: str, *, note: str, by_token_hash: str) -> bool:
    if not fp_hash or not note:
        return False
    now_iso = _iso()
    entry = {"note": (note or "")[:1000], "ts": now_iso,
              "by_token_hash": by_token_hash}
    res = await db.device_fingerprints.update_one(
        {"fingerprint_hash": fp_hash},
        {"$push": {"notes": entry}},
        upsert=False,
    )
    await db.device_fingerprint_audit.insert_one({
        "fingerprint_hash": fp_hash, "ts": now_iso, "ts_dt": _now(),
        "kind": "admin_note", "note": entry["note"], "by_token_hash": by_token_hash,
    })
    return res.modified_count > 0


# ──────────────────────────────────────────────────────────────────────────────
#  Listings (for admin Fraud Watch tab)
# ──────────────────────────────────────────────────────────────────────────────
async def list_top_suspicious(db, *, limit: int = 50,
                                only_status: Optional[str] = None) -> List[Dict[str, Any]]:
    """`only_status` ∈ {"blocked","flagged","trusted","active",None}."""
    q: Dict[str, Any] = {}
    if only_status == "blocked":
        q["blocked"] = True
        q["admin_trusted"] = {"$ne": True}
    elif only_status == "flagged":
        q["suspicious_score"] = {"$gte": FLAG_SCORE()}
        q["blocked"] = {"$ne": True}
        q["admin_trusted"] = {"$ne": True}
    elif only_status == "trusted":
        q["admin_trusted"] = True
    elif only_status == "active":
        q["blocked"] = {"$ne": True}
        q["admin_trusted"] = {"$ne": True}
    cursor = db.device_fingerprints.find(q, {"_id": 0}).sort(
        "suspicious_score", -1).limit(int(limit))
    out: List[Dict[str, Any]] = []
    async for row in cursor:
        out.append(_summarise_for_list(row))
    return out


def _summarise_for_list(row: Dict[str, Any]) -> Dict[str, Any]:
    fp = row.get("fingerprint_hash") or ""
    status = "trusted" if row.get("admin_trusted") else (
        "blocked" if row.get("blocked") else
        ("flagged" if float(row.get("suspicious_score") or 0) >= FLAG_SCORE() else "active"))
    return {
        "fingerprint_short": (fp[:8] + "…" + fp[-4:]) if len(fp) > 14 else fp,
        "fingerprint_hash": fp,
        "score": row.get("suspicious_score") or 0,
        "status": status,
        "client_count": len(row.get("client_identities") or []),
        "employee_count": len(row.get("employee_identities") or []),
        "ip_count": len(row.get("ips_seen") or []),
        "ua_count": len(row.get("user_agents_seen") or []),
        "first_seen": row.get("first_seen"),
        "last_seen": row.get("last_seen"),
        "blocked_at": row.get("blocked_at"),
        "blocked_reason": row.get("blocked_reason"),
        "admin_trusted": bool(row.get("admin_trusted")),
    }


async def counters_summary(db) -> Dict[str, Any]:
    """Returns aggregate counters for the Fraud Watch insights pane."""
    total = await db.device_fingerprints.count_documents({})
    blocked = await db.device_fingerprints.count_documents({"blocked": True, "admin_trusted": {"$ne": True}})
    trusted = await db.device_fingerprints.count_documents({"admin_trusted": True})
    flagged = await db.device_fingerprints.count_documents({
        "suspicious_score": {"$gte": FLAG_SCORE()},
        "blocked": {"$ne": True},
        "admin_trusted": {"$ne": True},
    })
    # Silent-block responses served today
    midnight = datetime(_now().year, _now().month, _now().day,
                         tzinfo=timezone.utc).isoformat()
    served_today = await db.security_events.count_documents({
        "kind": "fingerprint_silent_block_served",
        "created_at": {"$gte": midnight},
    })
    # Phase 22.1 — resolution-source distribution (24h). 100 % from
    # `header` is the healthy state; any `session` or `ip_ua` count > 0
    # means an FE call site is missing the header injection.
    resolution_24h = {"header": 0, "session": 0, "ip_ua": 0}
    try:
        pipe = [
            {"$match": {"kind": "fingerprint_resolution_source",
                         "created_at": {"$gte": midnight}}},
            {"$group": {"_id": "$source", "n": {"$sum": 1}}},
        ]
        async for row in db.security_events.aggregate(pipe):
            key = row.get("_id") or "unknown"
            if key in resolution_24h:
                resolution_24h[key] = int(row.get("n") or 0)
        # Header events are 1-in-50 sampled; un-discount for display.
        resolution_24h["header"] *= 50
    except Exception:
        pass
    return {
        "total_fingerprints": total,
        "blocked": blocked,
        "trusted": trusted,
        "flagged": flagged,
        "silent_blocks_served_today": served_today,
        "resolution_source_24h": resolution_24h,
        "thresholds": {
            "block_score": BLOCK_SCORE(),
            "flag_score": FLAG_SCORE(),
            "rapid_window_min": RAPID_WINDOW_MIN(),
            "rapid_client_limit": RAPID_LIMIT(),
            "daily_client_limit": DAILY_LIMIT(),
            "lifetime_client_limit_no_rm": LIFETIME_LIMIT(),
            "half_life_days": HALF_LIFE_DAYS,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
#  Silent-block response shapes
# ──────────────────────────────────────────────────────────────────────────────
def silent_block_chat_response(session_id: Optional[str]) -> Dict[str, Any]:
    """The exact payload returned for blocked /api/agent/turn (and friends).
    Shape matches a normal turn response — no `blocked:true`, no special
    code, no header. Looks like a routine soft failure."""
    return {
        "session_id": session_id,
        "blocks": [{
            "type": "text",
            "text": ("We're currently unable to process your request. "
                      "Please try again later or reach out to your "
                      "relationship manager."),
        }],
        "intent": "SOFT_ERROR",
        "model": None,
        "trace": [],
    }


def silent_block_legacy_chat() -> Dict[str, Any]:
    """For the legacy /api/chat endpoint (Phase-0 shim)."""
    return {
        "session_id": None,
        "reply": ("We're currently unable to process your request. "
                   "Please try again later or reach out to your "
                   "relationship manager."),
        "model": None,
        "grounded": False,
        "citations": [],
    }


def silent_block_verify_failed(session_type: str) -> Dict[str, Any]:
    """Identity-verification endpoints — indistinguishable from a wrong-PAN
    rejection."""
    return {
        "ok": False,
        "error": "verification_failed",
        "message": "We couldn't verify the details. Please try again.",
    }


def silent_block_empty_data() -> Dict[str, Any]:
    """Data/tool endpoints — empty result set."""
    return {"ok": True, "value": None, "results": [], "rows": []}


async def record_silent_block_served(db, fp_hash: str, path: str) -> None:
    try:
        now_iso = _iso()
        await db.security_events.insert_one({
            "kind": "fingerprint_silent_block_served",
            "session_id": None,
            "role_state_value": None,
            "user_message": f"path={path}",
            "user_message_excerpt": f"path={path}",
            "path": path,
            "action": "silent_block",
            "ts": now_iso,
            "ts_dt": _now(),
            "created_at": now_iso,
            "fingerprint_hash": fp_hash,
        })
    except Exception:
        logger.debug("silent_block_served audit failed", exc_info=True)
