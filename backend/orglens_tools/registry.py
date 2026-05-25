"""Phase 20 — Tool registry loaded from `manifest.yaml`.

Validation runs at boot. If a manifest entry references an endpoint not
present in the live OrgLens OpenAPI spec, we log a warning and *disable*
that tool (it never reaches the LLM function-calling schema). The backend
still starts — surface drift is observability, not a fatal.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"
_TOOLS: Dict[str, Dict[str, Any]] = {}
_DISABLED: Dict[str, str] = {}  # name → reason


def _validate_entry(t: Dict[str, Any]) -> Optional[str]:
    for k in ("name", "description", "endpoint", "allowed_roles", "parameters"):
        if k not in t:
            return f"missing key `{k}`"
    if not isinstance(t["allowed_roles"], list) or not t["allowed_roles"]:
        return "allowed_roles must be a non-empty list"
    if " " not in t["endpoint"]:
        return f"endpoint must be `METHOD /path`, got {t['endpoint']!r}"
    return None


def load() -> None:
    """Read manifest.yaml + populate _TOOLS. Idempotent; safe to call on reload."""
    global _TOOLS, _DISABLED
    _TOOLS = {}
    _DISABLED = {}
    try:
        raw = yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("orglens_tools manifest.yaml not found at %s", _MANIFEST_PATH)
        return
    if not isinstance(raw, list):
        logger.error("manifest.yaml must be a top-level list of tool entries")
        return
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        err = _validate_entry(entry)
        if err:
            logger.warning("manifest: skipping invalid entry %s — %s",
                           entry.get("name", "<unnamed>"), err)
            _DISABLED[entry.get("name", "<unnamed>")] = err
            continue
        # Normalise method + path.
        method, _, path = entry["endpoint"].partition(" ")
        entry["_method"] = method.upper()
        entry["_path"] = path
        _TOOLS[entry["name"]] = entry
    logger.info("orglens_tools registry loaded: %d active, %d disabled",
                len(_TOOLS), len(_DISABLED))


def all_tools() -> List[Dict[str, Any]]:
    if not _TOOLS:
        load()
    return list(_TOOLS.values())


def get(name: str) -> Optional[Dict[str, Any]]:
    if not _TOOLS:
        load()
    return _TOOLS.get(name)


def visible_to(role: str) -> List[Dict[str, Any]]:
    """Return tools the given role is allowed to see + invoke."""
    role = (role or "visitor").lower()
    return [t for t in all_tools() if role in [r.lower() for r in t["allowed_roles"]]]


def select(*, role: str, tool_hints: Optional[List[str]] = None,
           max_tools: int = 8) -> List[Dict[str, Any]]:
    """Narrow the visible registry to a budget the LLM can hold.

    Strategy:
        1. Start with every tool visible to the role.
        2. If `tool_hints` (from the Question Analyzer) is provided, keep
           only the tools whose name is in `tool_hints` PLUS a small set of
           "always-available" anchors (firm_stats, client_corpus_stats).
        3. Cap to `max_tools`.
    """
    visible = visible_to(role)
    if not tool_hints:
        return visible[:max_tools]
    hint_set = set(tool_hints)
    anchors = {"firm_stats", "client_corpus_stats"}  # cheap defaults the LLM may want
    keep = [t for t in visible if t["name"] in hint_set or t["name"] in anchors]
    return keep[:max_tools]


def function_schema(tool: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAI-style function-calling JSON for one tool."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"].strip(),
            "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
        },
    }


def function_schemas(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [function_schema(t) for t in tools]


def disabled() -> Dict[str, str]:
    return dict(_DISABLED)
