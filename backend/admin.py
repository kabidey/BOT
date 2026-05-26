"""Phase 4/6 admin router — leads, cost ledger, insights, KB uploads, archives."""
from __future__ import annotations
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
UPLOAD_DIR = Path(__file__).parent / "seed_docs" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTS = {".pdf", ".docx", ".md", ".txt"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def require_admin(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")
    return True


class LeadStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(new|contacted|qualified|closed)$")
    notes: Optional[str] = None


class ArchiveConsentUpdate(BaseModel):
    consent_to_ingest: bool


class ArchiveIngestRequest(BaseModel):
    dry_run: bool = False
    role: str = "all"  # "all" | "employee" | "client"


class KBSyncPayload(BaseModel):
    mode: str = "delta"   # "full" | "delta"
    dry_run: bool = False


class GapResolvePayload(BaseModel):
    question_normalized: str = Field(..., min_length=1, max_length=400)
    resolved: bool = True


class IngestCrawlPayload(BaseModel):
    """Phase 24d — manual crawl trigger payload."""
    site: str = Field(..., min_length=2, max_length=80)
    dry_run: bool = False
    max_pages: Optional[int] = None
    max_depth: Optional[int] = None


def build_admin_router(db) -> APIRouter:
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _start_of_day_utc() -> datetime:
        return _now().replace(hour=0, minute=0, second=0, microsecond=0)

    def _range_to_since(range_str: str) -> datetime:
        if range_str == "1d":
            return _now() - timedelta(days=1)
        if range_str == "30d":
            return _now() - timedelta(days=30)
        return _now() - timedelta(days=7)

    router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])

    # ---------------- Cost ----------------
    @router.get("/cost")
    async def get_cost():
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

        active_sessions_pipe = [{"$match": {"updated_at": {"$gte": since}}}, {"$count": "n"}]
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

        lead_classes_pipe = [
            {"$match": {"created_at": {"$gte": since}, "form_type": "lead_capture"}},
            {"$group": {"_id": "$context.asset_class", "count": {"$sum": 1}}},
            {"$project": {"_id": 0, "asset_class": "$_id", "count": 1}},
            {"$sort": {"count": -1}},
        ]
        lead_classes = await db.leads.aggregate(lead_classes_pipe).to_list(length=20)

        total_assist = sum(i["count"] for i in intent_dist) or 1
        esc = next((i["count"] for i in intent_dist if i["intent"] == "ESCALATION"), 0)
        escalation_rate = round(esc / total_assist, 4)

        # Phase 13 — resilience + security signal counters
        sec_7d = await db.security_events.count_documents({"created_at": {"$gte": since}})
        err_7d = await db.errors.count_documents({"created_at": {"$gte": since}})

        return {
            "range": range,
            "totals": {"sessions": sessions, "messages": messages, "verified_clients": verified},
            "intent_distribution": intent_dist,
            "lead_asset_classes": lead_classes,
            "low_confidence_intents": [],
            "escalation_rate": escalation_rate,
            "security_events_7d": sec_7d,
            "errors_7d": err_7d,
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
        transcript: List[Dict[str, Any]] = []
        sid = doc.get("session_id")
        if sid:
            convo = await db.conversations.find_one({"session_id": sid}, {"_id": 0, "messages": 1})
            if convo:
                msgs = convo.get("messages", [])[-20:]
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
        sample = await db.doc_chunks.find_one({"doc_id": doc_id}, {"_id": 0, "source": 1, "filename": 1})
        if not sample:
            raise HTTPException(status_code=404, detail="Doc not found")
        if sample.get("source") not in ("upload", "session_archive"):
            raise HTTPException(status_code=400, detail="Cannot delete seed documents — upload to override.")
        await db.doc_chunks.delete_many({"doc_id": doc_id})
        filename = sample.get("filename")
        if filename:
            fp = UPLOAD_DIR / filename
            try:
                if fp.exists():
                    fp.unlink()
            except OSError:
                logger.warning("Failed to delete upload file %s", fp)
        import rag as _rag
        await _rag.reload_index_from_db(db)
        return {"deleted": doc_id, "ok": True}

    @router.post("/reingest")
    async def admin_reingest(files: List[UploadFile] = File(default=[]),
                             reset_seeds: bool = Form(default=False)):
        import rag as _rag
        result = {"docs_added": 0, "chunks_added": 0, "files": [], "embedder": _rag.EMBEDDER_KIND or "local"}
        if reset_seeds:
            seed_res = await _rag.reingest(db)
            result.update({"seed_reingest": seed_res})

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
    async def admin_put_widget_config(payload: Dict[str, Any], x_admin_token: str = Header(default="")):
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

    # ---------- Phase 6 archives ----------
    @router.get("/archives")
    async def list_archives(role: str = "all", limit: int = 50, q: Optional[str] = None,
                            date_from: Optional[str] = None, date_to: Optional[str] = None,
                            offset: int = 0):
        import archives as _arc
        return await _arc.list_archives(
            db, role=role, limit=limit, q=q,
            date_from=date_from, date_to=date_to, offset=offset,
        )

    @router.get("/archives/{archive_id}")
    async def get_archive(archive_id: str):
        import archives as _arc
        doc = await _arc.get_archive(db, archive_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Archive not found")
        return doc

    @router.patch("/archives/{archive_id}")
    async def update_archive_consent(archive_id: str, payload: ArchiveConsentUpdate):
        import archives as _arc
        doc = await _arc.update_consent(db, archive_id, payload.consent_to_ingest)
        if not doc:
            raise HTTPException(status_code=404, detail="Archive not found")
        return doc

    @router.post("/archives/ingest_to_rag")
    async def ingest_archives_to_rag(payload: ArchiveIngestRequest):
        import archives as _arc
        return await _arc.ingest_archives_to_rag(db, dry_run=payload.dry_run, role=payload.role)

    # ---------- Phase 9 — SMIFS Knowledge API ----------
    @router.post("/knowledge/sync")
    async def knowledge_sync_now(payload: KBSyncPayload = Body(default_factory=KBSyncPayload)):
        import knowledge_sync as _ks
        if payload.mode not in ("full", "delta"):
            raise HTTPException(status_code=400, detail="mode must be 'full' or 'delta'")
        return await _ks.run_sync(db, mode=payload.mode, dry_run=payload.dry_run, trigger="manual")

    @router.get("/knowledge/status")
    async def knowledge_status(include_runs: int = 5):
        import knowledge_sync as _ks
        import guardrails as _gd
        stat = await _ks.status(db, include_runs=include_runs)
        stat["hallucination_events_7d"] = await _gd.recent_count(db, days=7)
        return stat

    @router.get("/knowledge/hallucination_events")
    async def hallucination_events(limit: int = 50):
        limit = max(1, min(limit, 200))
        cursor = db.hallucination_events.find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
        return {"events": await cursor.to_list(length=limit)}

    @router.get("/rag/debug")
    async def rag_debug(q: str, top_k: int = 8, gate_product: bool = True):
        """Audit endpoint — see what the retriever actually returns for a query."""
        import rag as _rag
        import guardrails as _gd
        restrict = None
        is_prod = _gd.is_product_topic(q)
        if gate_product and is_prod:
            restrict = ["smifs_knowledge", "seed"]
        hits = await _rag.search_weighted(q, top_k=top_k, restrict_sources=restrict)
        return {
            "query": q,
            "is_product_topic": is_prod,
            "restrict_sources": restrict,
            "analysis": _gd.analyse_retrieval(hits),
            "hits": [{
                "doc_id": h["doc_id"], "doc_title": h["doc_title"],
                "section": h["section"], "source": h.get("source"),
                "score": round(h["score"], 4), "raw_score": round(h.get("raw_score", h["score"]), 4),
                "preview": (h["text"] or "")[:200],
            } for h in hits],
        }

    # ---------- Phase 11 — Knowledge Gaps ----------
    @router.get("/knowledge_gaps")
    async def knowledge_gaps(range: str = "7d", role: str = "all", limit: int = 100):
        import knowledge_gaps as _kg
        if range not in ("24h", "7d", "30d"):
            range = "7d"
        if role not in ("all", "client", "employee", "visitor"):
            role = "all"
        limit = max(1, min(limit, 500))
        return await _kg.compute_gaps(db, range_str=range, role=role, limit=limit)

    @router.post("/knowledge_gaps/resolve")
    async def knowledge_gaps_resolve(payload: GapResolvePayload, x_admin_token: str = Header(default="")):
        import knowledge_gaps as _kg
        import widget_config as _wc
        try:
            return await _kg.mark_resolved(
                db, question_normalized=payload.question_normalized,
                resolved=payload.resolved,
                actor=_wc.admin_token_fingerprint(x_admin_token),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ---------- Phase 11 — Handoffs list (ops visibility) ----------
    @router.get("/handoffs")
    async def list_handoffs_admin(limit: int = 50):
        import handoff as _h
        rows = await _h.list_handoffs(db, limit=limit)
        return {"handoffs": rows, "count": len(rows)}

    # ---------------- Phase 14 — Sales Pipeline ----------------
    @router.get("/sales")
    async def list_sales(limit: int = 50, since: Optional[str] = None,
                          product: Optional[str] = None,
                          status: Optional[str] = None,
                          subtype: Optional[str] = None):
        """List sales (newest first). Client PAN masked in this view."""
        limit = max(1, min(int(limit or 50), 200))
        q: Dict[str, Any] = {}
        if since:
            q["created_at"] = {"$gte": since}
        if product:
            q["product"] = product
        if status:
            q["status"] = status
        if subtype:
            # Phase 17 — `subtype=arn_transfer` toggles the ARN-only view.
            q["subtype"] = subtype
        total = await db.sales_entries.count_documents(q)
        cur = db.sales_entries.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
        rows = await cur.to_list(length=limit)
        items: List[Dict[str, Any]] = []
        for r in rows:
            cli = r.get("client") or {}
            emp = r.get("employee") or {}
            name = (cli.get("client_name") or "").strip()
            masked_name = name
            if " " in name:
                parts = name.split()
                masked_name = parts[0] + " " + (parts[-1][:1] + "***" if parts[-1] else "")
            items.append({
                "submission_id": r.get("submission_id"),
                "product": r.get("product"),
                "subtype": r.get("subtype"),
                "vehicle_id": r.get("vehicle_id"),
                "vehicle_name": r.get("vehicle_name"),
                "vehicle_type": r.get("vehicle_type"),
                "employee_name": emp.get("name"),
                "employee_id": emp.get("employee_id"),
                "client_name_masked": masked_name,
                "amount_inr": r.get("amount_inr"),
                "expected_login_date": r.get("expected_login_date"),
                "expected_payment_date": r.get("expected_payment_date"),
                "status": r.get("status"),
                "email_sent": bool(r.get("email_sent")),
                "email_status": r.get("email_status"),
                "created_at": r.get("created_at"),
            })
        # KPIs
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        now = _dt.now(_tz.utc)
        today_iso = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        week_iso = (now - _td(days=7)).isoformat()
        kpis = {
            "today_count": await db.sales_entries.count_documents({"created_at": {"$gte": today_iso}}),
            "week_count": await db.sales_entries.count_documents({"created_at": {"$gte": week_iso}}),
        }
        # by-product breakdown (last 7d)
        pipe = [
            {"$match": {"created_at": {"$gte": week_iso}}},
            {"$group": {"_id": "$product", "count": {"$sum": 1},
                        "total_inr": {"$sum": "$amount_inr"}}},
            {"$project": {"_id": 0, "product": "$_id", "count": 1, "total_inr": 1}},
        ]
        kpis["by_product_7d"] = await db.sales_entries.aggregate(pipe).to_list(length=10)
        # today + week total INR
        for label, gte in (("today_total_inr", today_iso), ("week_total_inr", week_iso)):
            agg = await db.sales_entries.aggregate([
                {"$match": {"created_at": {"$gte": gte}}},
                {"$group": {"_id": None, "total": {"$sum": "$amount_inr"}}},
            ]).to_list(length=1)
            kpis[label] = (agg[0]["total"] if agg else 0)
        return {"total": total, "items": items, "kpis": kpis}

    @router.get("/sales/{submission_id}")
    async def sale_detail(submission_id: str):
        row = await db.sales_entries.find_one({"submission_id": submission_id}, {"_id": 0})
        if not row:
            raise HTTPException(status_code=404, detail="Sale not found")
        return row  # full payload incl. plaintext PAN (admin-only)

    @router.patch("/sales/{submission_id}/status")
    async def update_sale_status(submission_id: str, payload: Dict[str, Any]):
        new_status = (payload.get("status") or "").strip().lower()
        allowed = {"submitted", "logged", "funded", "reconciled", "cancelled"}
        if new_status not in allowed:
            raise HTTPException(status_code=400, detail=f"status must be one of {sorted(allowed)}")
        res = await db.sales_entries.update_one(
            {"submission_id": submission_id},
            {"$set": {"status": new_status,
                      "status_updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Sale not found")
        return {"ok": True, "submission_id": submission_id, "status": new_status}

    @router.post("/sales/{submission_id}/resend_email")
    async def resend_sale_email(submission_id: str):
        row = await db.sales_entries.find_one({"submission_id": submission_id}, {"_id": 0})
        if not row:
            raise HTTPException(status_code=404, detail="Sale not found")
        import email_relay as _er
        result = await _er.send_sale_notification(row, db=db)
        await db.sales_entries.update_one(
            {"submission_id": submission_id},
            {"$set": {
                "email_sent": bool(result.get("ok")),
                "email_status": result.get("reason"),
                "email_recipients": result.get("recipients") or [],
                "email_routing": result.get("routing") or {},
                "email_sent_at": (datetime.now(timezone.utc).isoformat()
                                   if result.get("ok") else row.get("email_sent_at")),
            }},
        )
        return {"ok": result.get("ok"), "reason": result.get("reason"),
                "recipients": result.get("recipients"),
                "routing": result.get("routing")}

    # ---------------- Phase 19 — Email relay observability ----------------
    @router.get("/email_relay/status")
    async def email_relay_status():
        """Live status of the Office 365 SMTP relay + hierarchy cache.

        Surfaces configuration (with the password redacted), the in-process
        cache snapshot, and the last ~25 send attempts. For long-window
        history correlate with `/api/admin/security_events?kind=email_relay_*`.

        Phase 19.2 — also returns `source: "mongo" | "env" | "none"` so
        admin can see where the config is being read from.
        """
        import email_relay as _er
        snap = await _er.relay_status(db)
        # Counters of Phase 19 security events for the last 7 days.
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        since_iso = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
        kinds = ("email_relay_hierarchy_unresolved",
                 "email_relay_send_failed",
                 "email_relay_basic_auth_disabled")
        events_7d: Dict[str, int] = {}
        for k in kinds:
            try:
                events_7d[k] = await db.security_events.count_documents(
                    {"kind": k, "created_at": {"$gte": since_iso}},
                )
            except Exception:
                events_7d[k] = -1
        snap["events_7d"] = events_7d
        # Latest sends from `sales_entries` to power the admin row.
        try:
            cur = db.sales_entries.find(
                {"email_status": {"$ne": None}},
                {"_id": 0, "submission_id": 1, "email_status": 1,
                 "email_sent_at": 1, "email_sent": 1,
                 "email_recipients": 1, "created_at": 1},
            ).sort("created_at", -1).limit(10)
            snap["latest_sales_emails"] = await cur.to_list(length=10)
        except Exception:
            snap["latest_sales_emails"] = []
        return snap

    @router.get("/email_relay/resolve_chain/{employee_id}")
    async def email_relay_resolve_chain(employee_id: str, force: int = 0):
        """Token-gated chain walker — used by ops to validate that a given
        employee's CC chain is what we expect *before* a live send.

        Pass `?force=1` to bypass the 1-hour cache (useful when an OrgLens
        update has just shipped).
        """
        import email_relay as _er
        payload = await _er.resolve_recipient_chain(
            employee_id=employee_id, db=db, force_refresh=bool(force),
        )
        return payload

    @router.post("/email_relay/configure")
    async def email_relay_configure(payload: Dict[str, Any] = Body(...)):
        """Phase 19 — one-shot SMTP bootstrap endpoint.

        Accepts the seven SMTP env values and idempotently upserts them into
        `/app/backend/.env`, then reloads them into the running process via
        `os.environ` so no supervisor restart is required.

        The password is NEVER logged, never echoed back in the response, and
        is masked in any error excerpt persisted to `security_events`. The
        endpoint is admin-token-gated like the rest of `/api/admin/*`.

        Fixed CC ops list is also accepted (`cc_ops_fixed`, optional).
        """
        # 1. Sanity-validate payload (no schema dep on pydantic to keep the
        # endpoint hot-swappable without a restart).
        def _str(key: str, *, required: bool = True) -> Optional[str]:
            v = payload.get(key)
            if v is None or (isinstance(v, str) and not v.strip()):
                if required:
                    raise HTTPException(status_code=400,
                                        detail=f"`{key}` is required")
                return None
            return str(v).strip()

        smtp_host = _str("smtp_host")
        smtp_user = _str("smtp_user")
        smtp_password = _str("smtp_password")
        from_email = _str("from_email")
        smtp_port_raw = payload.get("smtp_port") or 587
        try:
            smtp_port = int(smtp_port_raw)
        except Exception:
            raise HTTPException(status_code=400,
                                detail="`smtp_port` must be an integer")
        smtp_starttls = bool(payload.get("smtp_starttls", True))
        from_name = _str("from_name", required=False) or "SMIFS Wealth Guidance"
        cc_ops_raw = payload.get("cc_ops_fixed")
        if isinstance(cc_ops_raw, list):
            cc_ops_fixed = ",".join(s.strip() for s in cc_ops_raw if s and s.strip())
        elif isinstance(cc_ops_raw, str):
            cc_ops_fixed = cc_ops_raw.strip()
        else:
            cc_ops_fixed = None  # leave the existing value untouched

        new_values: Dict[str, str] = {
            "SMTP_HOST": smtp_host,
            "SMTP_PORT": str(smtp_port),
            "SMTP_STARTTLS": "true" if smtp_starttls else "false",
            "SMTP_USER": smtp_user,
            "SMTP_PASSWORD": smtp_password,
            "FROM_EMAIL": from_email,
            "FROM_NAME": from_name,
        }
        if cc_ops_fixed is not None:
            new_values["CC_OPS_FIXED"] = cc_ops_fixed

        # 2. Upsert .env idempotently.
        from pathlib import Path as _Path
        env_path = _Path(__file__).parent / ".env"
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []

        keys_remaining = set(new_values.keys())
        out_lines: List[str] = []
        for line in lines:
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                out_lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in new_values:
                out_lines.append(f"{key}={new_values[key]}")
                keys_remaining.discard(key)
            else:
                out_lines.append(line)
        # Append any keys that weren't already present.
        if keys_remaining:
            if out_lines and out_lines[-1].strip():
                out_lines.append("")
            out_lines.append("# Phase 19 SMTP bootstrap — appended via /api/admin/email_relay/configure")
            for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_STARTTLS", "SMTP_USER",
                      "SMTP_PASSWORD", "FROM_EMAIL", "FROM_NAME", "CC_OPS_FIXED"):
                if k in keys_remaining:
                    out_lines.append(f"{k}={new_values[k]}")

        env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

        # 3. Push values into the live process so the *very next* send picks
        # them up — no supervisor restart needed.
        for k, v in new_values.items():
            os.environ[k] = v

        # 4. Drop the chain cache so the next send re-resolves with the fresh
        # config (helps when ops_cc_fixed was just changed).
        try:
            import email_relay as _er
            _er._CHAIN_CACHE.clear()
        except Exception:
            pass

        # 5. Log-safe summary (NO password, ever).
        logger.info(
            "email_relay configure: host=%s port=%s starttls=%s user=%s "
            "from=%s cc_ops_changed=%s",
            smtp_host, smtp_port, smtp_starttls, smtp_user, from_email,
            cc_ops_fixed is not None,
        )

        # 6. Return the new status snapshot (password redacted by relay_status).
        import email_relay as _er
        return {"ok": True, "applied": True,
                "keys_written": sorted(new_values.keys()),
                "status": await _er.relay_status(db)}

    # ---------------- Phase 19.2 — Mongo-backed SMTP config (canonical) ----------------
    @router.get("/email_relay/config")
    async def email_relay_get_config():
        """Returns the active SMTP config with the password masked. The
        `source` field tells you whether the relay is reading from Mongo or
        env (or is unconfigured). NEVER returns the plaintext password.
        """
        import email_relay_config as _cfg
        return await _cfg.get_masked_config_view(db)

    @router.put("/email_relay/config")
    async def email_relay_put_config(
        payload: Dict[str, Any] = Body(...),
        x_admin_token: str = Header(default=""),
    ):
        """Upsert the SMTP config into Mongo (Fernet-encrypted password).
        Drops the in-process cache so the very next send picks up the new
        creds. NEVER logs the password.
        """
        import widget_config as _wc
        # Mandatory fields
        def _str(key: str, *, required: bool = True):
            v = payload.get(key)
            if v is None or (isinstance(v, str) and not v.strip()):
                if required:
                    raise HTTPException(status_code=400, detail=f"`{key}` is required")
                return None
            return str(v).strip() if isinstance(v, str) else v

        host = _str("host")
        user = _str("user")
        password_in = payload.get("password")
        from_email = _str("from_email")
        port_raw = payload.get("port") or 587
        try:
            port = int(port_raw)
        except Exception:
            raise HTTPException(status_code=400, detail="`port` must be an integer")
        starttls = bool(payload.get("starttls", True))
        from_name = _str("from_name", required=False) or "SMIFS Wealth Guidance"
        cc_raw = payload.get("cc_ops_fixed") or []
        if isinstance(cc_raw, str):
            cc_list = [a.strip() for a in cc_raw.split(",") if a.strip()]
        elif isinstance(cc_raw, list):
            cc_list = [str(a).strip() for a in cc_raw if str(a).strip()]
        else:
            cc_list = []

        # Password handling — if the caller omitted the password (or sent the
        # masked placeholder), keep the existing one from the stored doc.
        existing = await db.app_config.find_one({"_id": "smtp_relay"}, {"password_encrypted": 1})
        if not password_in or (isinstance(password_in, str)
                                and password_in.startswith("***") and existing):
            import email_relay_config as _cfg
            password_plain = _cfg._decrypt_password(existing.get("password_encrypted", ""))
            if not password_plain:
                raise HTTPException(status_code=400,
                                    detail="`password` is required (no existing password to reuse)")
        else:
            password_plain = str(password_in)

        import email_relay_config as _cfg
        masked = await _cfg.put_smtp_config(
            db,
            host=host, port=port, starttls=starttls,
            user=user, password=password_plain,
            from_email=from_email, from_name=from_name,
            cc_ops_fixed=cc_list,
            token_hash=_wc.admin_token_fingerprint(x_admin_token),
        )
        return {"ok": True, "config": masked}

    @router.delete("/email_relay/config")
    async def email_relay_delete_config(x_admin_token: str = Header(default="")):
        """Clear the Mongo config doc. Relay falls back to env (or 'none')."""
        import email_relay_config as _cfg
        import widget_config as _wc
        deleted = await _cfg.delete_smtp_config(
            db, token_hash=_wc.admin_token_fingerprint(x_admin_token),
        )
        view = await _cfg.get_masked_config_view(db)
        return {"ok": True, "deleted": deleted, "config": view}

    @router.post("/email_relay/test_connection")
    async def email_relay_test_connection():
        """Open SMTP → STARTTLS → AUTH → QUIT against the active config.
        Returns a classified result. Never sends a message."""
        import email_relay_config as _cfg
        cfg = await _cfg.get_smtp_config(db)
        if cfg is None:
            return {"ok": False, "error_kind": "unknown_error",
                    "error_message": "SMTP not configured (Mongo + env both empty)"}
        result = await _cfg.test_connection(cfg)
        # Persist a calls-log row.
        from datetime import datetime as _dt, timezone as _tz
        try:
            await db.email_relay_calls.insert_one({
                "created_at": _dt.now(_tz.utc).isoformat(),
                "reason": "test_connection",
                "ok": bool(result.get("ok")),
                "error_kind": result.get("error_kind"),
                "host": cfg.get("host"), "user": _cfg.mask_email(cfg.get("user") or ""),
                "source": cfg.get("source"),
            })
        except Exception:
            logger.exception("email_relay_calls insert (test_connection) failed")
        logger.info(
            "email_relay test_connection ok=%s kind=%s host=%s user=%s",
            result.get("ok"), result.get("error_kind"), cfg.get("host"),
            _cfg.mask_email(cfg.get("user") or ""),
        )
        return result

    @router.post("/email_relay/test_send")
    async def email_relay_test_send(payload: Dict[str, Any] = Body(...)):
        """Send a single 1-paragraph branded test email to one recipient.
        No template, no CC. Logged to `email_relay_calls`."""
        recipient = (payload.get("recipient") or "").strip()
        if not recipient or "@" not in recipient:
            raise HTTPException(status_code=400,
                                detail="`recipient` must be a valid email")
        import email_relay_config as _cfg
        cfg = await _cfg.get_smtp_config(db)
        if cfg is None:
            return {"ok": False, "error_kind": "unknown_error",
                    "error_message": "SMTP not configured"}

        from datetime import datetime as _dt, timezone as _tz
        from email.message import EmailMessage
        from email.utils import formataddr
        ts_iso = _dt.now(_tz.utc).isoformat()
        msg = EmailMessage()
        msg["Subject"] = f"SMIFS Sales-Ops relay test · {ts_iso[:19].replace('T', ' ')} UTC"
        msg["From"] = formataddr(
            (cfg.get("from_name") or "SMIFS Sales-Ops", cfg["from_email"]),
        )
        msg["To"] = recipient
        msg.set_content(
            "This is a one-paragraph SMIFS Sales-Ops relay test message.\n\n"
            f"Configuration source: {cfg.get('source')}\n"
            f"SMTP host: {cfg.get('host')}:{cfg.get('port')}\n"
            f"From: {cfg.get('from_email')}\n\n"
            "If you received this message, the relay credentials are valid "
            "and Phase 19.2 is live."
        )

        try:
            import aiosmtplib
        except ImportError:
            return {"ok": False, "error_kind": "unknown_error",
                    "error_message": "aiosmtplib not installed"}

        try:
            await aiosmtplib.send(
                msg,
                hostname=cfg["host"], port=int(cfg.get("port") or 587),
                username=cfg["user"], password=cfg["password"],
                start_tls=bool(cfg.get("starttls", True)),
                timeout=30, recipients=[recipient],
            )
            logger.info(
                "email_relay test_send ok recipient=%s host=%s source=%s",
                _cfg.mask_email(recipient), cfg.get("host"), cfg.get("source"),
            )
            try:
                await db.email_relay_calls.insert_one({
                    "created_at": ts_iso, "reason": "test_send",
                    "ok": True, "recipient": recipient,
                    "host": cfg.get("host"), "source": cfg.get("source"),
                })
            except Exception:
                pass
            return {"ok": True, "recipient": recipient, "sent_at": ts_iso}
        except Exception as e:
            scrubbed = _cfg._scrub(str(e), cfg.get("password") or "")
            kind, msg_text = _cfg._classify_auth_exception(e, cfg.get("password") or "")
            # The classifier is biased toward AUTH errors; for non-auth
            # exceptions fall back to "unknown_error" unless explicitly auth.
            if "Connection" in type(e).__name__ or "Refused" in type(e).__name__:
                kind = "connection_refused"
            if "TLS" in type(e).__name__ or "SSL" in type(e).__name__:
                kind = "tls_failed"
            if "Timeout" in type(e).__name__ or "Timeout" in scrubbed:
                kind = "timeout"
            logger.warning(
                "email_relay test_send FAILED recipient=%s kind=%s exc=%s msg=%s",
                _cfg.mask_email(recipient), kind, type(e).__name__, scrubbed[:300],
            )
            try:
                await db.email_relay_calls.insert_one({
                    "created_at": ts_iso, "reason": "test_send",
                    "ok": False, "recipient": recipient,
                    "error_kind": kind, "exc": type(e).__name__,
                    "host": cfg.get("host"), "source": cfg.get("source"),
                })
            except Exception:
                pass
            return {"ok": False, "error_kind": kind,
                    "error_message": scrubbed[:300]}

    # ---------------- Phase 13 — errors + security_events read surface ----------------
    @router.get("/errors")
    async def list_errors(limit: int = 50, since: Optional[str] = None):
        """Newest-first window over the `errors` collection. PII-scrubbed at
        ingestion (see resilience.log_error). `since` is an ISO-8601 string."""
        limit = max(1, min(int(limit or 50), 200))
        q: Dict[str, Any] = {}
        if since:
            q["created_at"] = {"$gte": since}
        total = await db.errors.count_documents(q)
        cur = db.errors.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
        rows = await cur.to_list(length=limit)
        items: List[Dict[str, Any]] = []
        import hashlib
        for r in rows:
            tb = r.get("traceback") or ""
            tb_hash = hashlib.sha1(tb.encode("utf-8")).hexdigest()[:12] if tb else None
            items.append({
                "error_id": r.get("error_id"),
                "ts": r.get("created_at"),
                "path": r.get("endpoint"),
                "exception_class": r.get("exc_type"),
                "exception_message": (r.get("exc_message") or "")[:300],
                "message_excerpt": r.get("user_message_excerpt") or "",
                "session_role": r.get("role_state"),
                "session_id": r.get("session_id"),
                "traceback_hash": tb_hash,
            })
        return {"total": total, "items": items}

    @router.get("/security_events")
    async def list_security_events(limit: int = 50, since: Optional[str] = None,
                                    kind: Optional[str] = None):
        """Newest-first window over `security_events`, optionally filtered by
        a single `kind`. Adds a `by_kind` summary across the SAME filter."""
        limit = max(1, min(int(limit or 50), 200))
        q: Dict[str, Any] = {}
        if since:
            q["created_at"] = {"$gte": since}
        if kind:
            q["kind"] = kind
        total = await db.security_events.count_documents(q)
        cur = db.security_events.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
        rows = await cur.to_list(length=limit)
        items = [{
            "ts": r.get("created_at"),
            "kind": r.get("kind"),
            "pattern_matched": r.get("kind"),  # alias — same signal lives in `kind`
            "session_role": r.get("role_state"),
            "session_id": r.get("session_id"),
            "message_excerpt": r.get("user_message_excerpt") or r.get("user_message") or "",
            "action": r.get("action"),
            "fingerprint_hash": r.get("fingerprint_hash"),
            "path": r.get("path"),
        } for r in rows]

        # by_kind aggregate over the SAME since/kind filter (kind filter is
        # idempotent on aggregate — if set it returns a single row).
        agg_q: Dict[str, Any] = {}
        if since:
            agg_q["created_at"] = {"$gte": since}
        if kind:
            agg_q["kind"] = kind
        pipe = [
            {"$match": agg_q},
            {"$group": {"_id": "$kind", "count": {"$sum": 1}}},
            {"$project": {"_id": 0, "kind": "$_id", "count": 1}},
            {"$sort": {"count": -1}},
        ]
        by_kind = await db.security_events.aggregate(pipe).to_list(length=50)
        return {"total": total, "by_kind": by_kind, "items": items}

    # ---------------- Phase 18 — Deck Vector Engine fallback status ----------------
    @router.get("/deck_search/status")
    async def deck_search_status(limit_calls: int = 25):
        """Phase 18 (Workstream A) operations panel.

        Surfaces:
          * The current flag state (`enabled`) + soft kill-switch suspension.
          * In-memory ring buffer of the most recent calls (status, latency,
            totalIndexed_seen, audience-dropped counts) — useful when the
            ops team is debugging unexpected zero-result responses.
          * A bounded slice of the `deck_search_calls` telemetry collection
            for longer-window auditing.
        """
        from agents import deck_search as _ds
        snap = _ds.status()
        rows: List[Dict[str, Any]] = []
        try:
            cur = db.deck_search_calls.find({}, {"_id": 0}).sort("created_at", -1).limit(max(1, min(limit_calls, 200)))
            async for row in cur:
                rows.append(row)
        except Exception:
            logger.exception("deck_search_status query failed (non-fatal)")
        snap["recent_telemetry"] = rows
        return snap

    # ---------------- Phase 20 — Tools admin observability ----------------
    @router.get("/tools/registry")
    async def tools_registry():
        """Returns every tool in the manifest with metadata + 7-day stats.
        Phase 24c — also surfaces the 4 BMIA tools so they appear in the
        admin tool console alongside OrgLens tools."""
        from orglens_tools import registry as _reg
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        since_iso = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
        tools = list(_reg.all_tools())
        # Append BMIA tool stubs — same shape as OrgLens manifest entries.
        try:
            from agents import bmia_client as _bmia_admin
            for ts in _bmia_admin.TOOL_SCHEMAS:
                fn = ts.get("function") or {}
                tools.append({
                    "name": fn.get("name"),
                    "description": (fn.get("description") or "")[:240],
                    "category": "bmia",
                    "endpoint": "https://bmia.in/api/public/v1/*",
                    "auth": "required",
                    "params": list((fn.get("parameters") or {}).get("properties", {}).keys()),
                    "allowed_roles": ["visitor", "client", "employee"],
                })
        except Exception:
            pass
        out: List[Dict[str, Any]] = []
        for t in tools:
            stats = {"calls_7d": 0, "ok_7d": 0, "cache_hits_7d": 0,
                     "p50_ms": None, "p95_ms": None}
            try:
                pipeline = [
                    {"$match": {"tool_name": t["name"], "created_at": {"$gte": since_iso}}},
                    {"$group": {"_id": "$tool_name",
                                 "calls": {"$sum": 1},
                                 "ok": {"$sum": {"$cond": ["$ok", 1, 0]}},
                                 "cache_hits": {"$sum": {"$cond": ["$hit_cache", 1, 0]}},
                                 "latencies": {"$push": "$latency_ms"}}},
                ]
                async for doc in db.tool_calls.aggregate(pipeline):
                    stats["calls_7d"] = doc["calls"]
                    stats["ok_7d"] = doc["ok"]
                    stats["cache_hits_7d"] = doc["cache_hits"]
                    lats = sorted(x for x in doc["latencies"] if isinstance(x, int))
                    if lats:
                        stats["p50_ms"] = lats[len(lats) // 2]
                        stats["p95_ms"] = lats[max(0, int(len(lats) * 0.95) - 1)]
            except Exception:
                pass
            out.append({
                "name": t["name"], "description": t["description"].strip().splitlines()[0][:160],
                "endpoint": t["endpoint"],
                "allowed_roles": t["allowed_roles"],
                "latency_tier": t.get("latency_tier"),
                "output_hint": t.get("output_hint"),
                "cache_ttl_seconds": t.get("cache_ttl_seconds"),
                "stats": stats,
            })
        return {"tools": out, "disabled": _reg.disabled(),
                "flag_enabled": os.environ.get("PHASE_20_TOOLS_ENABLED", "false").lower() == "true"}

    @router.get("/tools/recent")
    async def tools_recent(limit: int = 50):
        """Recent tool_call rows for the live tail."""
        cur = db.tool_calls.find({}, {"_id": 0}).sort("created_at", -1).limit(max(1, min(limit, 200)))
        return {"items": await cur.to_list(length=limit)}

    @router.get("/tools/analyzer_stats")
    async def tools_analyzer_stats():
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        since = (_dt.now(_tz.utc) - _td(hours=24)).isoformat()
        out: Dict[str, Any] = {"by_entity": {}, "by_operation": {}, "by_output_hint": {},
                                "total": 0, "avg_latency_ms": None}
        try:
            total = await db.question_analyzer_calls.count_documents({"created_at": {"$gte": since}})
            out["total"] = total
            for field in ("entity_type", "operation", "output_hint"):
                pipeline = [
                    {"$match": {"created_at": {"$gte": since}}},
                    {"$group": {"_id": f"${field}", "n": {"$sum": 1}}},
                    {"$sort": {"n": -1}},
                ]
                key = "by_" + field.split("_")[0]
                # Map back to expected keys.
                key = {"by_entity": "by_entity", "by_operation": "by_operation", "by_output": "by_output_hint"}.get(key, key)
                out_map = {}
                async for doc in db.question_analyzer_calls.aggregate(pipeline):
                    out_map[doc["_id"] or "?"] = doc["n"]
                if field == "entity_type":
                    out["by_entity"] = out_map
                if field == "operation":
                    out["by_operation"] = out_map
                if field == "output_hint":
                    out["by_output_hint"] = out_map
            # Avg latency
            pipeline = [{"$match": {"created_at": {"$gte": since}}},
                        {"$group": {"_id": None, "avg": {"$avg": "$latency_ms"}}}]
            async for d in db.question_analyzer_calls.aggregate(pipeline):
                out["avg_latency_ms"] = int(d.get("avg") or 0)
        except Exception:
            logger.exception("tools_analyzer_stats query failed (non-fatal)")
        return out

    @router.post("/tools/flag")
    async def tools_flag(payload: Dict[str, Any] = Body(...)):
        """Toggle PHASE_20_TOOLS_ENABLED at runtime. Writes to live env only;
        persistence is via .env (re-PUT after restart) — by design we keep
        the flag controllable from the UI for fast preview iteration."""
        on = bool(payload.get("enabled", False))
        os.environ["PHASE_20_TOOLS_ENABLED"] = "true" if on else "false"
        return {"ok": True, "flag_enabled": on}

    @router.get("/bmia/summary")
    async def bmia_summary():
        """Phase 24c — BMIA telemetry tile for the Admin diagnostics view."""
        try:
            from agents import bmia_client as _bmia_admin
            return _bmia_admin.summary()
        except Exception as e:
            return {"endpoints": {}, "error": str(e)[:200]}

    # ---------------- Phase 24b — Anti-Bluff Rail telemetry ----------------
    @router.get("/bluff/summary")
    async def bluff_summary_endpoint(days: int = 7):
        import anti_bluff as _ab
        return await _ab.bluff_summary(db, days=max(1, min(days, 30)))

    @router.get("/bluff/recent")
    async def bluff_recent(limit: int = 50):
        cur = db.bluff_events.find({}, {"_id": 0}).sort("ts", -1).limit(max(1, min(limit, 200)))
        return {"items": await cur.to_list(length=limit)}

    @router.get("/knowledge_gaps_log")
    async def knowledge_gaps_log(limit: int = 100):
        cur = db.knowledge_gaps_log.find({}, {"_id": 0}).sort("ts", -1).limit(max(1, min(limit, 500)))
        return {"items": await cur.to_list(length=limit)}

    # ---------------- Phase 24d — Website Ingestion Crawler ----------------
    @router.post("/ingest/crawl")
    async def ingest_crawl(payload: IngestCrawlPayload = Body(...)):
        """Trigger a single-site crawl. `site` is either a registered seed
        domain (e.g. 'sebi.gov.in') or 'all' to fan out to every seed.

        WARNING: synchronous — the response may take up to wall-time-cap
        (default 30 min). Callers should set generous client timeouts.
        """
        from agents import web_ingest as _wi
        if payload.site.lower() == "all":
            results = []
            for s in _wi.DEFAULT_SEEDS:
                r = await _wi.crawl_site(
                    db, s["seed_url"],
                    max_depth=payload.max_depth or s.get("max_depth", 3),
                    max_pages=payload.max_pages or s.get("max_pages", 200),
                    allow_pdf=s.get("allow_pdf", True),
                    allowed_path_prefix=s.get("allowed_path_prefix"),
                    dry_run=payload.dry_run,
                )
                results.append(r)
            return {"results": results}
        seed = _wi.get_seed(payload.site)
        if not seed:
            raise HTTPException(status_code=400, detail=f"Unknown site '{payload.site}'. "
                                                          f"Known: {[s['site'] for s in _wi.DEFAULT_SEEDS]}")
        return await _wi.crawl_site(
            db, seed["seed_url"],
            max_depth=payload.max_depth or seed.get("max_depth", 3),
            max_pages=payload.max_pages or seed.get("max_pages", 200),
            allow_pdf=seed.get("allow_pdf", True),
            allowed_path_prefix=seed.get("allowed_path_prefix"),
            dry_run=payload.dry_run,
        )

    @router.get("/ingest/status")
    async def ingest_status_endpoint():
        from agents import web_ingest as _wi
        return await _wi.ingest_status(db)

    # ---------------- Phase 22 — Fraud Watch (device fingerprint) ----------------
    import hashlib as _hashlib

    def _admin_actor_hash(x_admin_token: str) -> str:
        if not x_admin_token:
            return ""
        return _hashlib.sha256(x_admin_token.encode("utf-8")).hexdigest()[:16]

    @router.get("/fingerprint/summary")
    async def fingerprint_summary():
        import fingerprint_guard as _fpg
        return await _fpg.counters_summary(db)

    @router.get("/fingerprint/list")
    async def fingerprint_list(status: str = "active", limit: int = 50):
        import fingerprint_guard as _fpg
        if status not in ("active", "flagged", "blocked", "trusted"):
            status = "active"
        items = await _fpg.list_top_suspicious(
            db, limit=max(1, min(int(limit), 200)),
            only_status=status,
        )
        return {"status": status, "count": len(items), "items": items}

    @router.get("/fingerprint/{fp_hash}")
    async def fingerprint_detail(fp_hash: str):
        import fingerprint_guard as _fpg
        row = await _fpg.get_fingerprint(db, fp_hash)
        if not row:
            raise HTTPException(status_code=404, detail="fingerprint_not_found")
        audit_cur = db.device_fingerprint_audit.find(
            {"fingerprint_hash": fp_hash}, {"_id": 0}).sort("ts", -1).limit(50)
        row["audit"] = await audit_cur.to_list(length=50)
        return row

    @router.post("/fingerprint/{fp_hash}/block")
    async def fingerprint_block(
        fp_hash: str, payload: Dict[str, Any] = Body(default_factory=dict),
        x_admin_token: str = Header(default=""),
    ):
        import fingerprint_guard as _fpg
        reason = (payload.get("reason") or "manual_admin").strip()[:300]
        ok = await _fpg.block(db, fp_hash, reason=reason,
                                by_token_hash=_admin_actor_hash(x_admin_token))
        if not ok:
            raise HTTPException(status_code=404, detail="fingerprint_not_found")
        return {"ok": True, "fingerprint_hash": fp_hash, "blocked": True,
                "reason": reason}

    @router.post("/fingerprint/{fp_hash}/unblock")
    async def fingerprint_unblock(
        fp_hash: str, payload: Dict[str, Any] = Body(default_factory=dict),
        x_admin_token: str = Header(default=""),
    ):
        import fingerprint_guard as _fpg
        reason = (payload.get("reason") or "manual_admin").strip()[:300]
        ok = await _fpg.unblock(db, fp_hash, reason=reason,
                                  by_token_hash=_admin_actor_hash(x_admin_token))
        if not ok:
            raise HTTPException(status_code=404, detail="fingerprint_not_found")
        return {"ok": True, "fingerprint_hash": fp_hash, "blocked": False,
                "reason": reason}

    @router.post("/fingerprint/{fp_hash}/trust")
    async def fingerprint_trust(
        fp_hash: str, payload: Dict[str, Any] = Body(default_factory=dict),
        x_admin_token: str = Header(default=""),
    ):
        import fingerprint_guard as _fpg
        reason = (payload.get("reason") or "operator_attested_legit").strip()[:300]
        ok = await _fpg.set_trust(db, fp_hash, trusted=True, reason=reason,
                                    by_token_hash=_admin_actor_hash(x_admin_token))
        if not ok:
            raise HTTPException(status_code=404, detail="fingerprint_not_found")
        return {"ok": True, "fingerprint_hash": fp_hash, "trusted": True,
                "reason": reason}

    @router.post("/fingerprint/{fp_hash}/untrust")
    async def fingerprint_untrust(
        fp_hash: str, payload: Dict[str, Any] = Body(default_factory=dict),
        x_admin_token: str = Header(default=""),
    ):
        import fingerprint_guard as _fpg
        reason = (payload.get("reason") or "trust_revoked").strip()[:300]
        ok = await _fpg.set_trust(db, fp_hash, trusted=False, reason=reason,
                                    by_token_hash=_admin_actor_hash(x_admin_token))
        if not ok:
            raise HTTPException(status_code=404, detail="fingerprint_not_found")
        return {"ok": True, "fingerprint_hash": fp_hash, "trusted": False,
                "reason": reason}

    @router.post("/fingerprint/{fp_hash}/note")
    async def fingerprint_note(
        fp_hash: str, payload: Dict[str, Any] = Body(...),
        x_admin_token: str = Header(default=""),
    ):
        import fingerprint_guard as _fpg
        note = (payload.get("note") or "").strip()
        if not note:
            raise HTTPException(status_code=400, detail="note_required")
        ok = await _fpg.add_note(db, fp_hash, note=note[:1000],
                                   by_token_hash=_admin_actor_hash(x_admin_token))
        if not ok:
            raise HTTPException(status_code=404, detail="fingerprint_not_found")
        return {"ok": True, "fingerprint_hash": fp_hash}

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
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s.lstrip("# ").strip()
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s[:100]
    return Path(fallback_name).stem.replace("_", " ").title()
