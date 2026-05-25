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


# ============================================================================
#  HARD GATES — second line of defence after the LLM's JSON output
# ============================================================================
#
# The LLM is asked to obey a format decision tree in the system prompt, but
# even with explicit rules and few-shot examples it occasionally emits a text
# block when the Question Analyzer said `output_hint=table` and the tool data
# is clearly list-shaped. We add a deterministic gate here that:
#
#   1. Detects the missing structured block.
#   2. Signals the orchestrator to re-prompt the LLM ONCE.
#   3. If the re-prompt still misses, synthesises the block programmatically
#      from the tool payloads (column inference, type hints).
#
# We also enforce the CLAMP RULE at this layer: if ANY tool payload returned
# `clamped:true`, the LLM's blocks are replaced wholesale by a localised
# refusal text block. The clamping itself happened at the adapter (first line
# of defence). This layer makes sure the UX never leaks the clamped data.

_CLAMP_REFUSAL = {
    "en": ("I can only access data for your own account. I'm not able to look up "
           "another UCC. If you'd like, I can pull your own portfolio or "
           "transactions instead — or you can reach your relationship manager "
           "for help with other clients."),
    "hi": ("मैं केवल आपके अपने खाते का डेटा देख सकता हूँ — किसी अन्य UCC की जानकारी "
           "साझा नहीं कर सकता। यदि आप चाहें तो मैं आपका अपना पोर्टफोलियो या लेन-देन "
           "दिखा सकता हूँ; अन्य ग्राहक की जानकारी के लिए कृपया अपने रिलेशनशिप मैनेजर "
           "से संपर्क करें।"),
    "ta": ("உங்கள் சொந்தக் கணக்கின் தரவை மட்டுமே பகிர முடியும். வேறு ஒரு UCC-யின் "
           "தகவலைப் பகிர முடியாது. உங்கள் சொந்த போர்ட்ஃபோலியோ அல்லது பரிவர்த்தனைகள் "
           "வேண்டுமா? மற்ற வாடிக்கையாளர் தரவுக்கு உங்கள் RM-ஐ தொடர்பு கொள்ளுங்கள்."),
}

_INR_KEY_PATTERN = re.compile(
    r"(amount|price|aum|balance|charges|deposit|withdraw|cost|value|brokerage|"
    r"market_value|unrealised_pl|nav|investment|principal|salary|ctc)",
    re.IGNORECASE,
)
_DATE_KEY_PATTERN = re.compile(
    r"(date|debit|created_at|updated_at|joining|onboard|maturity|trade_date|"
    r"settle|next_)",
    re.IGNORECASE,
)


def _find_list_field(payload: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Inside a tool's `value` dict, find the FIRST field whose value is a
    list of dicts with >=1 entry. Heuristic but deterministic."""
    if not isinstance(payload, dict):
        return None
    if not payload.get("ok"):
        return None
    v = payload.get("value")
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return v
    if not isinstance(v, dict):
        return None
    # Prefer commonly named lists first.
    preferred = ("clients", "employees", "transactions", "sips", "folios",
                 "trades", "deposits", "withdrawals", "charges", "holdings",
                 "portfolio", "ledger", "buckets", "rows", "data", "items",
                 "results", "departments", "locations", "designations")
    for k in preferred:
        vv = v.get(k)
        if isinstance(vv, list) and vv and isinstance(vv[0], dict):
            return vv
    for vv in v.values():
        if isinstance(vv, list) and vv and isinstance(vv[0], dict):
            return vv
    return None


def _infer_columns(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pick a stable column ordering from the first 3 rows; infer type
    hints from key names + value patterns."""
    seen: List[str] = []
    for row in rows[:3]:
        for k in row.keys():
            if k.startswith("_"):
                continue  # internal/meta key
            if k not in seen:
                seen.append(k)
    keep = seen[:8]  # cap visible columns

    def _label(k: str) -> str:
        return k.replace("_", " ").strip().title()

    def _type_for(k: str, sample: Any) -> str:
        if _INR_KEY_PATTERN.search(k) and isinstance(sample, (int, float)):
            return "inr"
        if _DATE_KEY_PATTERN.search(k):
            return "date_relative"
        if isinstance(sample, (int, float)):
            return "num"
        return "text"

    cols = []
    first_row = rows[0] if rows else {}
    for k in keep:
        cols.append({"key": k, "label": _label(k),
                     "type": _type_for(k, first_row.get(k))})
    return cols


def _detect_clamped(tool_payloads: List[Dict[str, Any]]) -> bool:
    for p in tool_payloads:
        if isinstance(p, dict) and p.get("clamped") is True:
            return True
    return False


def _has_block(blocks: List[Dict[str, Any]], *kinds: str) -> bool:
    return any(isinstance(b, dict) and b.get("type") in kinds for b in (blocks or []))


def _any_list_data_available(tool_payloads: List[Dict[str, Any]]) -> bool:
    return any(_find_list_field(p) for p in tool_payloads)


def enforce_hard_gates(blocks: List[Dict[str, Any]], *,
                       output_hint: str,
                       tool_payloads: List[Dict[str, Any]],
                       language: str) -> tuple:
    """Returns (possibly-rewritten blocks, needs_reprompt, reason).

    Order matters:
      1. CLAMP — if any tool payload was clamped, override blocks with the
         localised refusal text. NEVER asks for a reprompt.
      2. SHAPE — if output_hint requires a structured block and the LLM
         didn't produce one but list-data is available, ask for a reprompt.
    """
    # 1. Clamp override
    if _detect_clamped(tool_payloads):
        refusal = _CLAMP_REFUSAL.get((language or "en").lower(), _CLAMP_REFUSAL["en"])
        return ([{"type": "text", "text": refusal}], False, None)

    # 2. Shape gate
    hint = (output_hint or "").lower()
    if hint == "table":
        if not _has_block(blocks, "table") and _any_list_data_available(tool_payloads):
            return (blocks, True, "table")
    elif hint == "chart":
        if not _has_block(blocks, "chart", "image") and _any_list_data_available(tool_payloads):
            return (blocks, True, "chart")
    elif hint == "card":
        if not _has_block(blocks, "employee_card", "client_card"):
            # A card needs single-entity data — only reprompt if SOME single
            # ok=true payload exists.
            for p in tool_payloads:
                if isinstance(p, dict) and p.get("ok") and isinstance(p.get("value"), dict):
                    return (blocks, True, "card")
    return (blocks, False, None)


def programmatic_fallback(blocks: List[Dict[str, Any]], *,
                          output_hint: str,
                          tool_payloads: List[Dict[str, Any]],
                          language: str) -> List[Dict[str, Any]]:
    """Best-effort: build a structured block ourselves from the first
    tool payload that has list-shaped data. Keeps the LLM's text block
    (if any) as the lead-in."""
    hint = (output_hint or "").lower()
    list_field = next((_find_list_field(p) for p in tool_payloads
                        if _find_list_field(p)), None)
    if list_field is None:
        return blocks
    text_lead = next((b for b in (blocks or []) if isinstance(b, dict)
                      and b.get("type") == "text"), None)
    out: List[Dict[str, Any]] = []
    if text_lead:
        out.append(text_lead)
    rows = list_field[:50]
    if hint == "table":
        out.append({
            "type": "table",
            "title": "Results",
            "columns": _infer_columns(rows),
            "rows": rows,
            "row_total": len(list_field),
            "fallback_synthesised": True,
        })
    elif hint == "chart":
        # Pick first numeric column as y, first non-numeric as x.
        cols = _infer_columns(rows)
        x_key = next((c["key"] for c in cols if c["type"] == "text"), cols[0]["key"])
        y_keys = [c["key"] for c in cols if c["type"] in ("inr", "num")][:2]
        if y_keys:
            out.append({
                "type": "chart",
                "kind": "bar",
                "title": "Results",
                "x_key": x_key,
                "y_keys": y_keys,
                "data": rows,
                "fallback_synthesised": True,
            })
        else:
            # No numeric — fall through to a table instead.
            out.append({
                "type": "table",
                "title": "Results",
                "columns": cols,
                "rows": rows,
                "row_total": len(list_field),
                "fallback_synthesised": True,
            })
    elif hint == "card":
        # Take the first ok=true single-dict payload and shape a card.
        for p in tool_payloads:
            if (isinstance(p, dict) and p.get("ok")
                    and isinstance(p.get("value"), dict)):
                v = p["value"]
                if "employee_code" in v or "designation" in v:
                    out.append({"type": "employee_card", **{k: v.get(k) for k in
                                                              ("employee_code", "employee_id",
                                                               "name", "designation", "department",
                                                               "email", "location", "manager")
                                                              if v.get(k) is not None}})
                elif "ucc" in v or "client_name" in v:
                    out.append({"type": "client_card", **{k: v.get(k) for k in
                                                            ("ucc", "client_name", "pan",
                                                             "branch", "state")
                                                            if v.get(k) is not None}})
                break
    return out or blocks


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
