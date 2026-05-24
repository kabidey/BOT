"""Phase 17 — Deck-pegged Sales-Ops catalog.

Source of truth: `doc_chunks` rows where `subsource == "vehicle"`. Each chunk is
projected to a sellable inventory row {vehicle_id, vehicle_name, vehicle_type,
is_focused, is_active, updated_at_iso}. The Sales-Ops picker (stage 2) reads
this catalog grouped by product bucket so an employee can only log a sale
against a vehicle currently in the SMIFS deck.

User decisions (Phase 17 brief):
  * All deck vehicles are sellable → NO `is_active` filter on the picker.
  * Deck is canonical for all 6 product types — no free-text fallback path.
  * Sales reporting stays verified-employee-only (gate is enforced by the
    `sales_api` router; this module only owns the catalog projection).

In-process cache: 60s TTL. The deck doesn't change second-to-second; we'd
rather hand stale-by-a-minute rows than thrash the Mongo aggregation on every
form open.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------- vehicle_type → product_type mapping ----------
# The SMIFS Knowledge API emits a small enum of vehicle types. We collapse
# those into the 6 product buckets the bot's Sales-Ops flow already supports.
# `Mediclaim` is the health-insurance variant — folds into `insurance` so the
# existing insurance form handles it. If the API ever ships a value not in
# this table the catalog logs a `security_events` row of kind
# `unmapped_vehicle_type` and drops the row from the picker (never crashes).
VEHICLE_TYPE_TO_PRODUCT: Dict[str, str] = {
    "MF":         "mutual_fund",
    "AIF":        "aif",
    "PMS":        "pms",
    "FD":         "fd",
    "Insurance":  "insurance",
    "Mediclaim":  "insurance",
    "NCD":        "ncd_primary",
}

# Allowed product buckets (must match `sales_api.PRODUCTS`)
PRODUCT_BUCKETS = ("mutual_fund", "aif", "pms", "fd", "insurance", "ncd_primary")

CACHE_TTL_SECONDS = 60

# Cache shape: (snapshot_time_ts, payload)
_cache: Dict[str, Any] = {"ts": 0.0, "data": None}


async def _log_unmapped(db, vehicle_type: str, vehicle_id: str, vehicle_name: str) -> None:
    """Telemetry breadcrumb when the API ships a vehicle_type we don't know how to bucket."""
    try:
        await db.security_events.insert_one({
            "kind": "unmapped_vehicle_type",
            "vehicle_type": vehicle_type,
            "vehicle_id": vehicle_id,
            "vehicle_name": vehicle_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "severity": "info",
        })
    except Exception:
        logger.exception("Failed to log unmapped_vehicle_type (non-fatal)")


async def _fresh_catalog(db) -> Dict[str, Any]:
    """Build the catalog from Mongo. Caller wraps with cache."""
    cur = db.doc_chunks.aggregate([
        {"$match": {"source": "smifs_knowledge", "subsource": "vehicle"}},
        # Distinct on vehicle_id; the same vehicle can have multiple chunks
        # but stage 2 only needs one row per saleable inventory item.
        {"$group": {
            "_id": "$vehicle_id",
            "vehicle_name": {"$first": "$vehicle_name"},
            "vehicle_type": {"$first": "$vehicle_type"},
            "is_focused":   {"$max":   "$is_focused"},
            "is_active":    {"$max":   "$is_active"},
            "updated_at_iso": {"$max": "$updated_at_iso"},
        }},
    ])
    rows: List[Dict[str, Any]] = await cur.to_list(length=2000)

    by_product: Dict[str, List[Dict[str, Any]]] = {p: [] for p in PRODUCT_BUCKETS}
    unmapped: List[Dict[str, Any]] = []
    for r in rows:
        vid = r.get("_id")
        vname = (r.get("vehicle_name") or "").strip()
        vtype = (r.get("vehicle_type") or "").strip()
        if not vid or not vname:
            continue
        bucket = VEHICLE_TYPE_TO_PRODUCT.get(vtype)
        if bucket is None:
            unmapped.append({"vehicle_id": vid, "vehicle_name": vname, "vehicle_type": vtype})
            continue
        by_product[bucket].append({
            "vehicle_id": vid,
            "vehicle_name": vname,
            "vehicle_type": vtype,
            "is_focused": bool(r.get("is_focused")),
            "is_active":  bool(r.get("is_active")) if r.get("is_active") is not None else True,
            "updated_at_iso": r.get("updated_at_iso"),
        })

    # Sort each bucket: focused first, then alphabetical by name.
    for p in by_product:
        by_product[p].sort(key=lambda v: (0 if v["is_focused"] else 1, v["vehicle_name"].lower()))

    # Telemetry for unmapped values (one row per discovery — quiet on the steady-state).
    for u in unmapped:
        await _log_unmapped(db, u["vehicle_type"], u["vehicle_id"], u["vehicle_name"])

    totals = {p: len(v) for p, v in by_product.items()}
    # Phase 17.1 — `focused_by_bucket` so the picker (and admin) can render
    # "no house-view picks in <product> this period" instead of silently
    # showing zero stars and leaving employees wondering whether it's a UI
    # bug or a real deck reality. Same shape as `totals`.
    focused_by_bucket = {
        p: sum(1 for v in rows if v["is_focused"]) for p, rows in by_product.items()
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "buckets": by_product,
        "totals": totals,
        "focused_by_bucket": focused_by_bucket,
        "total_vehicles": sum(totals.values()),
        "total_focused": sum(focused_by_bucket.values()),
        "unmapped_count": len(unmapped),
    }


async def catalog(db) -> Dict[str, Any]:
    """Cached catalog reader. Returns the same shape `_fresh_catalog` does."""
    now = time.monotonic()
    if _cache["data"] and (now - _cache["ts"] < CACHE_TTL_SECONDS):
        return _cache["data"]
    data = await _fresh_catalog(db)
    _cache["ts"] = now
    _cache["data"] = data
    return data


def invalidate_cache() -> None:
    """For tests + explicit refreshes (e.g. after a fresh KB sync)."""
    _cache["ts"] = 0.0
    _cache["data"] = None


async def find_vehicle(db, vehicle_id: str) -> Optional[Dict[str, Any]]:
    """Look up one row by vehicle_id across all buckets. Used by the sales
    endpoint to (a) confirm the vehicle exists in the current deck and (b)
    enforce cross-type matching (e.g. an NCD vehicle on the FD form path is
    rejected at the API)."""
    if not vehicle_id:
        return None
    data = await catalog(db)
    for bucket, rows in data["buckets"].items():
        for v in rows:
            if v["vehicle_id"] == vehicle_id:
                return {**v, "product_type": bucket}
    return None
