"""Phase 20 — Reporting-hierarchy org-tree PNG generator.

Inputs:
    identity:      session identity dict (employee_id, name).
    user_message:  the raw user message; we extract a referenced employee_id
                   if present, otherwise use the caller's own.

Output:
    A 1200x800 PNG written to `/app/uploads/charts/<uuid>.png`. Returns the
    UUID id (no extension) — the chat endpoint serves it under
    `/api/charts/<id>.png`.

Implementation notes:
    * Walks UP via `reports_to_employee_id` and DOWN via OrgLens org-tree
      slice. Capped at 3 ancestors + 12 direct reports for legibility.
    * Pure matplotlib — no NetworkX dependency; we draw boxes + lines on
      a manual layout. Theme matches SMIFS dark UI.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

import directory as _dir

from . import UPLOAD_DIR

logger = logging.getLogger(__name__)

_EMPLOYEE_ID_RE = re.compile(r"\bSM[A-Z]{2}-\d{8}\b")
_MAX_ANCESTORS = 3
_MAX_REPORTS = 12

# SMIFS palette — pulled from CSS variables.
_BG = "#0B1B2B"
_FG = "#F4E6CB"
_GOLD = "#C9A86A"
_GREEN = "#14a47a"
_LINE = "#2A3F58"


async def _fetch(code: str) -> Optional[Dict[str, Any]]:
    try:
        data = await _dir._get(f"/employee/by-code/{code}")
        return data.get("employee") if isinstance(data, dict) else None
    except Exception:
        return None


def _label(emp: Dict[str, Any]) -> str:
    name = (emp.get("name") or emp.get("first_name") or "").strip()
    desig = (emp.get("designation") or "").strip()
    if len(name) > 28:
        name = name[:26] + "…"
    if len(desig) > 30:
        desig = desig[:28] + "…"
    return f"{name}\n{desig}"


async def render(db, *, identity: Dict[str, Any], user_message: str) -> Optional[str]:
    """Returns the PNG id (uuid hex) on success, None on failure."""
    started_at = datetime.now(timezone.utc).isoformat()
    # 1. Pick the focal employee.
    m = _EMPLOYEE_ID_RE.search(user_message or "")
    focal_code = m.group(0) if m else (identity.get("employee_id") or identity.get("employee_code"))
    if not focal_code:
        return None
    focal = await _fetch(focal_code)
    if not focal:
        return None

    # 2. Walk up at most _MAX_ANCESTORS.
    ancestors: List[Dict[str, Any]] = []
    cur = focal
    for _ in range(_MAX_ANCESTORS):
        nxt = cur.get("reports_to_employee_id")
        if not nxt:
            break
        rec = await _fetch(nxt)
        if not rec:
            break
        ancestors.insert(0, rec)
        cur = rec

    # 3. Pull direct reports — only the count for now (full org-tree slice
    # is too heavyweight to surface live). Phase 20.1 can deepen this.
    drc = focal.get("direct_reports_count") or 0

    # 4. Render.
    fig, ax = plt.subplots(figsize=(12, 8), dpi=100, facecolor=_BG)
    ax.set_facecolor(_BG)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 8)
    ax.axis("off")

    chain = ancestors + [focal]
    n = len(chain)
    y_top = 7
    y_step = 1.8

    def _draw_box(ax, x, y, w, h, text, *, accent=False):
        bg = _GREEN if accent else _LINE
        edge = _GOLD if accent else _GOLD
        ax.add_patch(FancyBboxPatch((x - w/2, y - h/2), w, h,
                                       boxstyle="round,pad=0.08,rounding_size=0.18",
                                       facecolor=bg, edgecolor=edge, linewidth=1.4))
        ax.text(x, y, text, ha="center", va="center", color=_FG, fontsize=9.5,
                weight="bold" if accent else "normal")

    # Vertical chain on the left half
    cx = 4
    for i, emp in enumerate(chain):
        y = y_top - i * y_step
        is_focal = (i == len(chain) - 1)
        _draw_box(ax, cx, y, 4.5, 1.1, _label(emp), accent=is_focal)
        if i > 0:
            ax.plot([cx, cx], [y_top - (i-1) * y_step - 0.55, y + 0.55],
                    color=_GOLD, linewidth=1.0, alpha=0.7)

    # Direct-reports note on the right
    rx = 9.5
    ry = y_top - (n - 1) * y_step
    if drc > 0:
        _draw_box(ax, rx, ry, 3.5, 1.1,
                  f"Direct reports\n{drc}",
                  accent=False)
        ax.annotate("", xy=(rx - 1.7, ry), xytext=(cx + 2.3, ry),
                    arrowprops=dict(arrowstyle="-|>", color=_GOLD, lw=1.0, alpha=0.7))

    # Header + footer
    ax.text(6, 7.7, "SMIFS Reporting Hierarchy", ha="center", va="center",
            color=_GOLD, fontsize=14, weight="bold")
    ax.text(0.4, 0.25, f"Focal employee: {focal.get('name') or focal_code}  ·  "
            f"Ancestors shown: {len(ancestors)}/{_MAX_ANCESTORS}  ·  "
            f"Generated: {started_at[:19]}Z",
            ha="left", va="center", color="#8FA4BD", fontsize=7.5)

    png_id = uuid.uuid4().hex
    out_path: Path = UPLOAD_DIR / f"{png_id}.png"
    plt.tight_layout(pad=0.8)
    plt.savefig(out_path, dpi=110, facecolor=_BG, edgecolor=_BG)
    plt.close(fig)

    # Telemetry.
    if db is not None:
        try:
            await db.charts_generated.insert_one({
                "id": png_id,
                "generator": "org_tree",
                "focal_employee_code": focal_code,
                "ancestors_count": len(ancestors),
                "direct_reports_count": drc,
                "byte_size": out_path.stat().st_size,
                "created_at": started_at,
                "expires_at": (datetime.now(timezone.utc).timestamp() + 86400),
            })
        except Exception:
            logger.exception("charts_generated insert failed (non-fatal)")
    return png_id
