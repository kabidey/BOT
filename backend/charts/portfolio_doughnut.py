"""Phase 20 — Portfolio asset-allocation doughnut.

Pulls the MF investor record (for the verified client) and renders a
doughnut showing the current debt/equity/FD/other split alongside the
target allocation. Falls back to the BO-side portfolio totals if MF data
isn't available.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import directory as _dir
from . import UPLOAD_DIR

logger = logging.getLogger(__name__)

_BG = "#0B1B2B"
_FG = "#F4E6CB"
_PALETTE = ["#14a47a", "#C9A86A", "#5B7CC9", "#E07A6E", "#9C7CD2", "#6BC9C9"]


async def _fetch_mf_by_pan(pan: str) -> Optional[Dict[str, Any]]:
    try:
        data = await _dir._get(f"/mf/client/by-pan/{pan}")
        return data.get("client") if isinstance(data, dict) else None
    except Exception:
        return None


async def render(db, *, identity: Dict[str, Any], user_message: str) -> Optional[str]:
    started_at = datetime.now(timezone.utc).isoformat()
    pan = (identity.get("pan") or identity.get("verified_pan") or "").upper()
    if not pan:
        return None
    inv = await _fetch_mf_by_pan(pan)
    if not inv:
        return None

    # Pull asset buckets (MF returns these in INR).
    buckets: Dict[str, float] = {}
    for label, key in [
        ("Equity (MF)", "mfEquity"),
        ("Debt (MF)", "mfDebt"),
        ("FD/Debt", "fdDebt"),
        ("Insurance", "lifeInsurance"),
        ("Other", "oaEquity"),
        ("Shares (Other)", "sbEquity"),
    ]:
        v = inv.get(key)
        try:
            f = float(v) if v is not None else 0.0
        except Exception:
            f = 0.0
        if f > 0:
            buckets[label] = f

    if not buckets:
        return None

    fig, ax = plt.subplots(figsize=(8, 8), dpi=100, facecolor=_BG)
    ax.set_facecolor(_BG)

    sizes = list(buckets.values())
    labels = list(buckets.keys())
    colors = _PALETTE[: len(sizes)]
    total = sum(sizes)

    def autopct(pct):
        return f"{pct:.1f}%" if pct >= 4 else ""

    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, autopct=autopct, startangle=110,
        colors=colors, pctdistance=0.78,
        wedgeprops=dict(width=0.38, edgecolor=_BG, linewidth=2),
        textprops=dict(color=_FG, fontsize=10),
    )
    for t in autotexts:
        t.set_color("#0B1B2B")
        t.set_weight("bold")
        t.set_fontsize(9.5)

    # Centre text — total AUM.
    if total >= 1e7:
        amt = f"₹{total/1e7:.2f} Cr"
    elif total >= 1e5:
        amt = f"₹{total/1e5:.2f} L"
    else:
        amt = f"₹{total:,.0f}"
    ax.text(0, 0.1, "Total AUM", ha="center", va="center", color="#8FA4BD", fontsize=10)
    ax.text(0, -0.1, amt, ha="center", va="center", color="#C9A86A", fontsize=18, weight="bold")

    # Header
    ax.set_title("Portfolio asset allocation", color="#C9A86A", fontsize=14, pad=18, weight="bold")

    # Footer with target split if present.
    teq = inv.get("targetEquityAllocation")
    tdb = inv.get("targetDebtAllocation")
    if teq is not None and tdb is not None:
        ax.text(0, -1.35, f"Target split:  Equity {teq}%  ·  Debt {tdb}%",
                ha="center", va="center", color="#8FA4BD", fontsize=10)

    png_id = uuid.uuid4().hex
    out_path: Path = UPLOAD_DIR / f"{png_id}.png"
    plt.tight_layout(pad=1.0)
    plt.savefig(out_path, dpi=110, facecolor=_BG, edgecolor=_BG)
    plt.close(fig)

    if db is not None:
        try:
            await db.charts_generated.insert_one({
                "id": png_id,
                "generator": "portfolio_doughnut",
                "focal_pan_masked": (pan[:5] + "***" + pan[-1]) if len(pan) >= 10 else "***",
                "bucket_count": len(buckets),
                "total_aum": total,
                "byte_size": out_path.stat().st_size,
                "created_at": started_at,
                "expires_at": (datetime.now(timezone.utc).timestamp() + 86400),
            })
        except Exception:
            logger.exception("charts_generated insert failed (non-fatal)")
    return png_id
