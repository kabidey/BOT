"""Cost ledger — captures every Hub AI call into the `llm_calls` collection.

Hub AI's response shape (observed Feb 2026):
{
  "id": "chatcmpl-...",
  "model": "<resolved-model-name>",
  "provider": "<provider>",
  "choices": [...],
  "usage": {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...},
  "cost": {"input_inr": ..., "output_inr": ..., "cost_inr": ..., "exchange_rate": ...},
  "balance_inr": 9982.41,
  "latency_ms": 760
}
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _safe_int(v: Any) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def extract_metrics(data: Dict[str, Any], request_model: str) -> Dict[str, Any]:
    """Pull cost/usage/latency fields from a Hub AI chat-completion response."""
    usage = data.get("usage") or {}
    cost = data.get("cost") or {}
    return {
        "model_requested": request_model,
        "model_resolved": data.get("model"),
        "provider": data.get("provider"),
        "input_tokens": _safe_int(usage.get("prompt_tokens")),
        "output_tokens": _safe_int(usage.get("completion_tokens")),
        "total_tokens": _safe_int(usage.get("total_tokens")),
        "cost_inr": _safe_float(cost.get("cost_inr")),
        "input_inr": _safe_float(cost.get("input_inr")),
        "output_inr": _safe_float(cost.get("output_inr")),
        "balance_inr_after": _safe_float(data.get("balance_inr")),
        "latency_ms": _safe_int(data.get("latency_ms")),
    }


async def record_call(db, *, task: str, session_id: Optional[str], intent: Optional[str],
                      data: Dict[str, Any], request_model: str, local_latency_ms: int) -> None:
    """Insert a single llm_calls row. Fire-and-forget — failures are logged, not raised."""
    try:
        m = extract_metrics(data, request_model)
        latency_ms = m["latency_ms"] or local_latency_ms
        now_dt = datetime.now(timezone.utc)
        doc = {
            "_id": str(uuid.uuid4()),
            "session_id": session_id,
            "intent": intent,
            "task": task,
            "latency_ms": latency_ms,
            "created_at": now_dt.isoformat(),
            "created_at_dt": now_dt,  # real ISODate for TTL
            **m,
        }
        await db.llm_calls.insert_one(doc)
    except Exception:
        logger.exception("cost_ledger.record_call failed")


def fire_and_forget_record(db, **kwargs) -> None:
    """Schedule record_call without awaiting; safe inside hot paths."""
    try:
        asyncio.create_task(record_call(db, **kwargs))
    except RuntimeError:
        # No running loop (e.g. tests outside an event loop) — drop silently
        pass
