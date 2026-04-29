"""Phase 4 admin router — leads, cost ledger, insights, KB uploads."""
from __future__ import annotations
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
UPLOAD_DIR = Path(__file__).parent / "seed_docs" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTS = {".pdf", ".docx", ".md", ".txt"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


def require_admin(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")
    return True


admin_router_module_marker = True  # placeholder so this file is importable as a module


# ---------- Pydantic models ----------
class LeadStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(new|contacted|qualified|closed)$")
    notes: Optional[str] = None


# We use a factory pattern below so server.py can inject the live `db` handle.
def build_admin_router(db) -> APIRouter:
    """Return an APIRouter with all admin endpoints bound to the given db."""

    # ---------- Pydantic models (in-scope) ----------
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _start_of_day_utc() -> datetime:
        n = _now()
        return n.replace(hour=0, minute=0, second=0, microsecond=0)

    def _range_to_since(range_str: str) -> datetime:
        if range_str == "1d":
            return _now() - timedelta(days=1)
        if range_str == "30d":
            return _now() - timedelta(days=30)
        return _now() - timedelta(days=7)  # default 7d

    router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])

    # ---------------- Cost ----------------
    @router.get("/cost")
    async def get_cost():
        # Latest balance (most recent llm_calls row with non-zero balance)
        latest_row = await db.llm_calls.find_one(
            {"balance_inr_after": {"$gt": 0}},
            sort=[("created_at", -1)],
            projection={"_id": 0, "balance_inr_after": 1, "created_at": 1},
        )
        balance_inr = (latest_row or {}).get("balance_inr_after", 0.0)

        now = _now()
        sod = _start_of_day_utc().isoformat()
        sow = (now - timedelta(days=7)).isoformat()
        som = (now - timedelta(days=30)).isoformat()

        async def _sum_cost(since_iso: str) -> Dict[str, Any]:
            pipe = [
                {"$match": {"created_at": {"$gte": since_iso}}},
                {"$group": {"_id": None, "cost": {"$sum": "$cost_inr"}, "n": {"$sum": 1},
                            "lat": {"$avg": "$latency_ms"}}},
            ]
            cur = db.llm_calls.aggregate(pipe)
            rows = await cur.to_list(length=1)
            if rows:
                return {"cost": float(rows[0].get("cost") or 0), "calls": int(rows[0].get("n") or 0),
                        "avg_latency_ms": int(rows[0].get("lat") or 0)}
            return {"cost": 0.0, "calls": 0, "avg_latency_ms": 0}

        today, week, month = await _sum_cost(sod), await _sum_cost(sow), await _sum_cost(som)

        async def _group_by(field: str) -> List[Dict[str, Any]]:
            pipe = [
                {"$match": {"created_at": {"$gte": som}}},
                {"$group": {"_id": f"${field}", "calls": {"$sum": 1}, "cost_inr": {"$sum": "$cost_inr"}}},
                {"$project": {"_id": 0, field: "$_id", "calls": 1, "cost_inr": 1}},
                {"$sort": {"cost_inr": -1}},
                {"$limit": 20},
            ]
            return await db.llm_calls.aggregate(pipe).to_list(length=20)

        by_model = await _group_by("model_resolved")
        by_task = await _group_by("task")

        # Cost-per-day for last 7 days for sparkline
        seven_days = []
        for i in range(6, -1, -1):
            day_start = (_start_of_day_utc() - timedelta(days=i))
            day_end = day_start + timedelta(days=1)
            row = await db.llm_calls.aggregate([
                {"$match": {"created_at": {"$gte": day_start.isoformat(), "$lt": day_end.isoformat()}}},
                {"$group": {"_id": None, "cost": {"$sum": "$cost_inr"}, "calls": {"$sum": 1}}},
            ]).to_list(length=1)
            cost = float(row[0]["cost"]) if row else 0.0
            calls = int(row[0]["calls"]) if row else 0
            seven_days.append({"date": day_start.strftime("%Y-%m-%d"), "cost_inr": cost, "calls": calls})

        return {
            "balance_inr": balance_inr,
            "today_inr": today["cost"],
            "week_inr": week["cost"],
            "month_inr": month["cost"],
            "calls_today": today["calls"],
            "calls_week": week["calls"],
            "avg_latency_ms": today["avg_latency_ms"] or week["avg_latency_ms"],
            "by_model": [{**r, "model": r.get("model_resolved")} for r in by_model],
            "by_task": by_task,
            "daily_series": seven_days,
            "balance_as_of": (latest_row or {}).get("created_at"),
        }

    # ---------------- Insights ----------------
    @router.get("/insights")
    async def get_insights(range: str = "7d"):
        since = _range_to_since(range).isoformat()

        # Sessions / messages / verified clients
        active_sessions_pipe = [
            {"$match": {"updated_at": {"$gte": since}}},
            {"$count": "n"},
        ]
        active_sessions = await db.conversations.aggregate(active_sessions_pipe).to_list(length=1)
        sessions = int(active_sessions[0]["n"]) if active_sessions else 0

        msg_pipe = [
            {"$match": {"updated_at": {"$gte": since}}},
            {"$project": {"messages": 1}},
            {"$unwind": "$messages"},
            {"$match": {"messages.ts": {"$gte": since}}},
            {"$count": "n"},
        ]
        msg_rows = await db.conversations.aggregate(msg_pipe).to_list(length=1)
        messages = int(msg_rows[0]["n"]) if msg_rows else 0

        verified = await db.sessions.count_documents({
            "auth_state": "verified", "verified_at": {"$gte": since}
        })

        # Intent distribution (assistant turns only)
        intent_pipe = [
            {"$match": {"updated_at": {"$gte": since}}},
            {"$project": {"messages": 1}},
            {"$unwind": "$messages"},
            {"$match": {"messages.role": "assistant", "messages.intent": {"$ne": None},
                        "messages.ts": {"$gte": since}}},
            {"$group": {"_id": "$messages.intent", "count": {"$sum": 1}}},
            {"$project": {"_id": 0, "intent": "$_id", "count": 1}},
            {"$sort": {"count": -1}},
        ]
        intent_dist = await db.conversations.aggregate(intent_pipe).to_list(length=50)

        # Lead asset classes (in `leads` collection)
        lead_classes_pipe = [
            {"$match": {"created_at": {"$gte": since}, "form_type": "lead_capture"}},
            {"$group": {"_id": "$context.asset_class", "count": {"$sum": 1}}},
            {"$project": {"_id": 0, "asset_class": "$_id", "count": 1}},
            {"$sort": {"count": -1}},
        ]
        lead_classes = await db.leads.aggregate(lead_classes_pipe).to_list(length=20)

        # Router confidence per intent — the router writes router rows to llm_calls
        # but we don't currently capture per-call confidence there. Confidence lives
        # in conversations.messages[*].trace[*]; surfacing that is a P2 enhancement.
        low_conf: List[Dict[str, Any]] = []

        # Escalation rate
        total_assist = sum(i["count"] for i in intent_dist) or 1
        esc = next((i["count"] for i in intent_dist if i["intent"] == "ESCALATION"), 0)
        escalation_rate = round(esc / total_assist, 4)

        return {
            "range": range,
            "totals": {"sessions": sessions, "messages": messages, "verified_clients": verified},
            "intent_distribution": intent_dist,
            "lead_asset_classes": lead_classes,
            "low_confidence_intents": low_conf,
            "escalation_rate": escalation_rate,
        }

    # ---------------- Leads ----------------
    @router.get("/leads")
    async def list_leads(status: str = "all", limit: int = 50):
        q: Dict[str, Any] = {}
        if status != "all":
            q["status"] = status
        cur = db.leads.find(q, {"_id": 0}).sort("created_at", -1).limit(min(limit, 200))
        rows = await cur.to_list(length=limit)
        return {"leads": rows, "count": len(rows)}

    @router.get("/leads/{lead_id}")
    async def get_lead(lead_id: str):
        doc = await db.leads.find_one({"lead_id": lead_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Lead not found")
        # Attach last 10 turns from the same session_id
        transcript: List[Dict[str, Any]] = []
        sid = doc.get("session_id")
        if sid:
            convo = await db.conversations.find_one({"session_id": sid}, {"_id": 0, "messages": 1})
            if convo:
                msgs = convo.get("messages", [])[-20:]  # last 10 turns ≈ 20 messages
                for m in msgs:
                    entry = {"role": m.get("role"), "ts": m.get("ts")}
                    if m.get("role") == "user":
                        entry["text"] = m.get("content", "")
                    else:
                        entry["text"] = m.get("content", "")
                        entry["intent"] = m.get("intent")
                    transcript.append(entry)
        return {**doc, "transcript": transcript}

    @router.patch("/leads/{lead_id}")
    async def update_lead(lead_id: str, payload: LeadStatusUpdate):
        update_doc: Dict[str, Any] = {"status": payload.status, "updated_at": _now().isoformat()}
        if payload.notes is not None:
            update_doc["notes"] = payload.notes
        res = await db.leads.update_one({"lead_id": lead_id}, {"$set": update_doc})
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Lead not found")
        doc = await db.leads.find_one({"lead_id": lead_id}, {"_id": 0})
        return doc

    # ---------------- Knowledge base ----------------
    @router.get("/docs")
    async def list_docs():
        pipe = [
            {"$group": {
                "_id": "$doc_id",
                "doc_title": {"$first": "$doc_title"},
                "chunks": {"$sum": 1},
                "source": {"$first": "$source"},
                "filename": {"$first": "$filename"},
                "uploaded_at": {"$first": "$uploaded_at"},
                "created_at": {"$first": "$created_at"},
            }},
            {"$project": {"_id": 0, "doc_id": "$_id", "doc_title": 1, "chunks": 1,
                          "source": {"$ifNull": ["$source", "seed"]}, "filename": 1,
                          "uploaded_at": 1, "created_at": 1}},
            {"$sort": {"uploaded_at": -1, "doc_id": 1}},
        ]
        rows = await db.doc_chunks.aggregate(pipe).to_list(length=500)
        return {"docs": rows, "count": len(rows)}

    @router.delete("/docs/{doc_id}")
    async def delete_doc(doc_id: str):
        # Refuse to delete seed docs
        sample = await db.doc_chunks.find_one({"doc_id": doc_id}, {"_id": 0, "source": 1, "filename": 1})
        if not sample:
            raise HTTPException(status_code=404, detail="Doc not found")
        if sample.get("source") != "upload":
            raise HTTPException(status_code=400, detail="Cannot delete seed documents — upload to override.")
        # Remove chunks
        await db.doc_chunks.delete_many({"doc_id": doc_id})
        # Remove file if present
        filename = sample.get("filename")
        if filename:
            fp = UPLOAD_DIR / filename
            try:
                if fp.exists():
                    fp.unlink()
            except OSError:
                logger.warning("Failed to delete upload file %s", fp)
        # Rebuild in-memory index
        import rag as _rag
        await _rag.reload_index_from_db(db)
        return {"deleted": doc_id, "ok": True}

    @router.post("/reingest")
    async def admin_reingest(files: List[UploadFile] = File(default=[]),
                             reset_seeds: bool = Form(default=False)):
        """Re-ingest seed docs. If files are uploaded, also extract + ingest them.
        With reset_seeds=true (default false), re-ingest the seed_docs/ folder too."""
        import rag as _rag
        result = {"docs_added": 0, "chunks_added": 0, "files": [], "embedder": _rag.EMBEDDER_KIND or "local"}
        # 1. Optional: re-ingest seeds
        if reset_seeds:
            seed_res = await _rag.reingest(db)
            result.update({"seed_reingest": seed_res})

        # 2. Process uploaded files
        for upload in files:
            try:
                fname = upload.filename or "uploaded"
                ext = Path(fname).suffix.lower()
                if ext not in ALLOWED_EXTS:
                    result["files"].append({"filename": fname, "status": "skipped",
                                            "reason": f"Unsupported extension {ext}"})
                    continue
                contents = await upload.read()
                if len(contents) > MAX_UPLOAD_BYTES:
                    result["files"].append({"filename": fname, "status": "skipped",
                                            "reason": f"File too large (>{MAX_UPLOAD_BYTES//1024//1024} MB)"})
                    continue
                # Save to disk (for traceability + delete-by-doc-id)
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", fname)
                disk_path = UPLOAD_DIR / safe_name
                disk_path.write_bytes(contents)

                text = _extract_text(disk_path)
                if not text.strip():
                    result["files"].append({"filename": fname, "status": "skipped",
                                            "reason": "No extractable text"})
                    continue

                doc_id = "upload_" + Path(safe_name).stem.lower()
                doc_title = _derive_title(text, fname)
                chunks = _rag.chunk_markdown(doc_id, doc_title, text)
                if not chunks:
                    result["files"].append({"filename": fname, "status": "skipped", "reason": "No chunks"})
                    continue
                added = await _rag.ingest_extra_chunks(
                    db, chunks, source="upload", filename=safe_name,
                )
                result["docs_added"] += 1
                result["chunks_added"] += added
                result["files"].append({"filename": safe_name, "doc_id": doc_id,
                                        "chunks": added, "status": "ok"})
            except Exception as e:
                logger.exception("Upload processing failed for %s", upload.filename)
                result["files"].append({"filename": upload.filename, "status": "error",
                                        "reason": str(e)[:200]})

        return result

    # ---------- Phase 5 widget config (admin-gated) ----------
    @router.get("/widget/config")
    async def admin_get_widget_config():
        import widget_config as _wc
        cfg = await _wc.get(force_refresh=True)
        return _wc._strip_id(cfg)

    @router.put("/widget/config")
    async def admin_put_widget_config(payload: Dict[str, Any],
                                      x_admin_token: str = Header(default="")):
        import widget_config as _wc
        try:
            updated = await _wc.update(payload, updated_by=_wc.admin_token_fingerprint(x_admin_token))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return _wc._strip_id(updated)

    @router.post("/widget/reset")
    async def admin_reset_widget_config(x_admin_token: str = Header(default="")):
        import widget_config as _wc
        fresh = await _wc.reset(updated_by=_wc.admin_token_fingerprint(x_admin_token))
        return _wc._strip_id(fresh)

    return router


# ---------- text extraction helpers ----------
def _extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".md", ".txt"):
        return path.read_text(encoding="utf-8", errors="ignore")
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return ""
        reader = PdfReader(str(path))
        parts: List[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n\n".join(parts)
    if ext == ".docx":
        try:
            from docx import Document
        except ImportError:
            return ""
        doc = Document(str(path))
        return "\n\n".join(p.text for p in doc.paragraphs if p.text)
    return ""


def _derive_title(text: str, fallback_name: str) -> str:
    # First H1 markdown line, else first non-empty line, else filename stem
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s.lstrip("# ").strip()
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s[:100]
    return Path(fallback_name).stem.replace("_", " ").title()
