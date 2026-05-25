"""Phase 20 — Response builder.

Takes the LLM's final JSON answer (or fallback text), parses it into the
renderer's `blocks` array, and inserts any PNG ImageBlock when the question
matches one of our two approved generators.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None
    # Common cases: pure JSON, json-fenced, or partial.
    try:
        return json.loads(text)
    except Exception:
        pass
    m = _FENCED_JSON.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # Last resort — find the first {...} balanced span.
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except Exception:
                    return None
    return None


_ALLOWED_TYPES = {"text", "table", "chart", "image", "download",
                  "employee_card", "client_card", "citations"}


def _coerce_blocks(parsed: Dict[str, Any], output_hint: str) -> List[Dict[str, Any]]:
    blocks = parsed.get("blocks")
    if not isinstance(blocks, list):
        return []
    out: List[Dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t not in _ALLOWED_TYPES:
            continue
        # Enforce row caps for inline tables.
        if t == "table":
            rows = b.get("rows") or []
            if not isinstance(rows, list):
                rows = []
            if len(rows) > 50:
                # Convert to a DownloadBlock + a 50-row preview table.
                truncated = rows[:50]
                b = {**b, "rows": truncated,
                     "footnote": f"Showing 50 of {len(rows)} rows. Full dataset available via download."}
                out.append(b)
                out.append({"type": "download",
                             "title": (b.get("title") or "Dataset") + " (full)",
                             "format": "json",
                             "url": "",  # filled by the chat endpoint when needed
                             "row_count": len(rows),
                             "size_bytes": len(json.dumps(rows, default=str).encode("utf-8"))})
                continue
        out.append(b)
    return out


def build_blocks(llm_text: str, *, output_hint: str, language: str) -> List[Dict[str, Any]]:
    parsed = _extract_json(llm_text)
    if parsed and isinstance(parsed, dict):
        blocks = _coerce_blocks(parsed, output_hint)
        if blocks:
            return blocks
    # Fallback — wrap whatever text we got into a TextBlock.
    return [{"type": "text", "text": (llm_text or "I couldn't compose an answer.").strip()[:4000]}]


# ---------------- Image-generator hooks (the 2 approved use cases) ---------

_ORG_TREE_TRIGGERS = re.compile(
    r"\b(org\s*tree|reporting\s*structure|team\s*structure|hierarchy|"
    r"who\s+reports\s+to|reporting\s*line|chain\s+of\s+command)\b",
    re.IGNORECASE,
)
_PORTFOLIO_DOUGHNUT_TRIGGERS = re.compile(
    r"\b(portfolio\s*(split|breakup|allocation|composition|mix|breakdown)|"
    r"asset\s*allocation|equity\s+vs\s+debt|debt\s+equity\s*split)\b",
    re.IGNORECASE,
)


async def maybe_generate_image_blocks(
    db, *, session: Dict[str, Any], identity: Dict[str, Any],
    blocks: List[Dict[str, Any]], user_message: str,
    classification: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Insert an ImageBlock when the question matches one of the two approved
    generators. We always APPEND — never replace — the LLM's blocks."""
    msg = user_message or ""
    if _ORG_TREE_TRIGGERS.search(msg):
        try:
            from charts import org_tree as _gen
            png_id = await _gen.render(db, identity=identity, user_message=msg)
            if png_id:
                blocks.append({"type": "image",
                               "src": f"/api/charts/{png_id}.png",
                               "alt": "Reporting hierarchy diagram",
                               "width": 1200, "height": 800,
                               "download_filename": "smifs-org-tree.png"})
        except Exception:
            logger.exception("org_tree generator failed (non-fatal)")
    elif _PORTFOLIO_DOUGHNUT_TRIGGERS.search(msg):
        try:
            from charts import portfolio_doughnut as _gen
            png_id = await _gen.render(db, identity=identity, user_message=msg)
            if png_id:
                blocks.append({"type": "image",
                               "src": f"/api/charts/{png_id}.png",
                               "alt": "Portfolio asset-allocation doughnut",
                               "width": 800, "height": 800,
                               "download_filename": "portfolio-split.png"})
        except Exception:
            logger.exception("portfolio_doughnut generator failed (non-fatal)")
    return blocks
