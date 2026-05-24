import { useEffect, useRef, useState } from "react";
import { Upload, Trash2, FileText, CheckCircle2, AlertTriangle, Database, RefreshCw, ShieldAlert, ShieldCheck } from "lucide-react";

export default function KnowledgeBaseTab({ api }) {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [toast, setToast] = useState(null);
  const inputRef = useRef(null);
  // Phase 9 — SMIFS Knowledge API status
  const [kbStatus, setKbStatus] = useState(null);
  const [deckStatus, setDeckStatus] = useState(null);   // Phase 18 / 18.1
  const [syncing, setSyncing] = useState(false);

  const loadStatus = async () => {
    try {
      const { data } = await api.get("/admin/knowledge/status");
      setKbStatus(data);
    } catch (e) { /* non-fatal */ }
    try {
      const { data } = await api.get("/admin/deck_search/status?limit_calls=10");
      setDeckStatus(data);
    } catch (e) { /* non-fatal */ }
  };

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/admin/docs");
      setDocs(data.docs || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); loadStatus(); /* eslint-disable-next-line */ }, []);

  const showToast = (kind, msg) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 8000);
  };

  const upload = async (fileList) => {
    if (!fileList || !fileList.length) return;
    const fd = new FormData();
    Array.from(fileList).forEach((f) => fd.append("files", f));
    setUploading(true);
    try {
      const { data } = await api.post("/admin/reingest", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      showToast("ok", `Uploaded ${data.docs_added} doc(s) · ${data.chunks_added} chunks indexed.`);
      await load();
    } catch (e) {
      const detail = e?.response?.data?.detail || e.message;
      showToast("err", `Upload failed: ${detail}`);
    } finally {
      setUploading(false);
    }
  };

  const onDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    upload(e.dataTransfer.files);
  };

  const onPick = (e) => upload(e.target.files);

  const remove = async (doc_id) => {
    if (!window.confirm(`Remove ${doc_id} from the knowledge base?`)) return;
    try {
      await api.delete(`/admin/docs/${doc_id}`);
      showToast("ok", `Removed ${doc_id}.`);
      await load();
    } catch (e) {
      const detail = e?.response?.data?.detail || e.message;
      showToast("err", detail);
    }
  };

  const runSync = async (mode) => {
    setSyncing(true);
    try {
      const { data } = await api.post("/admin/knowledge/sync", { mode, dry_run: false });
      const errs = (data.errors || []).length;
      if (errs > 0) {
        showToast("err", `Sync finished with ${errs} error(s) · fetched ${data.fetched}, upserted ${data.upserted}`);
      } else {
        showToast("ok", `Sync ${mode} done · fetched ${data.fetched} · upserted ${data.upserted} · skipped ${data.skipped} · removed ${data.removed}`);
      }
      await loadStatus();
      await load();
    } catch (e) {
      showToast("err", e?.response?.data?.detail || e.message);
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="smifs-admin-page">
      <header className="smifs-admin-page-head">
        <p className="smifs-admin-eyebrow">Embeddings · Hub AI text-embedding-3-small · 1536-dim</p>
        <h2 className="smifs-admin-title">Knowledge Base</h2>
      </header>

      {/* Phase 9 — SMIFS Knowledge API status + sync */}
      {kbStatus && (
        <section className="smifs-kb-api-panel" data-testid="kb-smifs-panel">
          <div className="smifs-kb-api-head">
            <div>
              <div className="smifs-kb-api-title">
                <Database size={14} strokeWidth={2.25} />
                SMIFS Knowledge API
                {kbStatus.api_reachable
                  ? <span className="smifs-kb-api-badge smifs-kb-api-badge--ok" data-testid="kb-api-status"><ShieldCheck size={10} strokeWidth={2.5} /> Reachable</span>
                  : <span className="smifs-kb-api-badge smifs-kb-api-badge--warn" data-testid="kb-api-status"><ShieldAlert size={10} strokeWidth={2.5} /> Offline</span>
                }
              </div>
              <p className="smifs-kb-api-meta">
                Last sync {kbStatus.last_sync?.last_sync_at ? new Date(kbStatus.last_sync.last_sync_at).toLocaleString() : "never"}
                {kbStatus.last_sync?.last_mode ? ` · ${kbStatus.last_sync.last_mode}` : ""}
              </p>
              {kbStatus.auto_sync_enabled && (
                <p className="smifs-kb-api-meta" data-testid="kb-auto-sync-info">
                  Auto-sync every {Math.round((kbStatus.auto_sync_interval_seconds || 900) / 60)} min ·
                  next at {kbStatus.next_scheduled_sync_at ? new Date(kbStatus.next_scheduled_sync_at).toLocaleTimeString() : "—"}
                </p>
              )}
            </div>
            <div className="smifs-kb-api-actions">
              <button type="button" className="smifs-btn smifs-btn--ghost"
                disabled={syncing || !kbStatus.api_reachable}
                onClick={() => runSync("delta")} data-testid="kb-sync-delta-btn">
                <RefreshCw size={12} strokeWidth={2.5} /> Delta sync
              </button>
              <button type="button" className="smifs-btn smifs-btn--primary"
                disabled={syncing || !kbStatus.api_reachable}
                onClick={() => runSync("full")} data-testid="kb-sync-full-btn">
                <RefreshCw size={12} strokeWidth={2.5} /> Full sync
              </button>
            </div>
          </div>
          <div className="smifs-kb-counts">
            <div className="smifs-kb-count smifs-kb-count--primary" data-testid="kb-count-smifs">
              <span className="smifs-kb-count-value">{kbStatus.total_smifs_chunks ?? 0}</span>
              <span className="smifs-kb-count-label">SMIFS official</span>
            </div>
            <div className="smifs-kb-count" data-testid="kb-count-seed">
              <span className="smifs-kb-count-value">{kbStatus.total_seed_chunks ?? 0}</span>
              <span className="smifs-kb-count-label">Seed docs</span>
            </div>
            <div className="smifs-kb-count" data-testid="kb-count-upload">
              <span className="smifs-kb-count-value">{kbStatus.total_uploaded_chunks ?? 0}</span>
              <span className="smifs-kb-count-label">Uploaded</span>
            </div>
            <div className="smifs-kb-count" data-testid="kb-count-archive">
              <span className="smifs-kb-count-value">{kbStatus.total_archive_chunks ?? 0}</span>
              <span className="smifs-kb-count-label">Archives</span>
            </div>
            <div className="smifs-kb-count smifs-kb-count--warn" data-testid="kb-count-hallucination">
              <span className="smifs-kb-count-value">{kbStatus.hallucination_events_7d ?? 0}</span>
              <span className="smifs-kb-count-label">Low-conf · 7d</span>
            </div>
          </div>
          {syncing && <p className="smifs-kb-api-syncing">Syncing from deck.pesmifs.com …</p>}
          {kbStatus?.last_run_summary && kbStatus.last_run_summary.length > 0 && (
            <details className="smifs-kb-history" data-testid="kb-sync-history">
              <summary>Last {kbStatus.last_run_summary.length} runs</summary>
              <ul>
                {kbStatus.last_run_summary.map((r, i) => (
                  <li key={i} data-testid={`kb-run-${i}`}>
                    <span className="smifs-mono">{(r.started_at || "").slice(0, 19).replace("T", " ")}</span>
                    <span className={`smifs-status-pill smifs-status-pill--${r.trigger === "scheduler" ? "qualified" : "new"}`}>{r.trigger}</span>
                    <span>{r.mode}</span>
                    <span>fetched {r.fetched} · upserted {r.upserted} · skipped {r.skipped} · removed {r.removed}</span>
                    <span className="smifs-table-cell-sub">{r.duration_ms}ms</span>
                    {(r.errors || []).length > 0 && <span className="smifs-status-pill smifs-status-pill--new">{r.errors.length} error(s)</span>}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}

      {/* Phase 18 / 18.1 — Deck Vector Engine fallback status panel */}
      {deckStatus && (
        <section className="smifs-kb-api-panel" data-testid="deck-search-panel" style={{ marginTop: 12 }}>
          <div className="smifs-kb-api-head">
            <div>
              <div className="smifs-kb-api-title">
                <Database size={14} strokeWidth={2.25} />
                Deck Vector Engine fallback
                {deckStatus.enabled
                  ? <span className="smifs-kb-api-badge smifs-kb-api-badge--ok" data-testid="deck-enabled-badge"><ShieldCheck size={10} strokeWidth={2.5} /> enabled: true</span>
                  : <span className="smifs-kb-api-badge smifs-kb-api-badge--warn" data-testid="deck-enabled-badge"><ShieldAlert size={10} strokeWidth={2.5} /> disabled</span>
                }
                {deckStatus.suspended && (
                  <span className="smifs-kb-api-badge smifs-kb-api-badge--warn" data-testid="deck-suspended-badge">
                    <ShieldAlert size={10} strokeWidth={2.5} /> Suspended
                  </span>
                )}
              </div>
              <p className="smifs-kb-api-meta">
                min_score {deckStatus.min_score} · timeout {deckStatus.timeout_s}s ·
                slow threshold {deckStatus.slow_response_ms}ms ·
                deck indexed {deckStatus.current_totalIndexed_seen ?? "—"}
              </p>
            </div>
          </div>
          <div className="smifs-kb-counts" data-testid="deck-counters">
            <div className="smifs-kb-count smifs-kb-count--primary" data-testid="deck-calls-today">
              <span className="smifs-kb-count-value">{deckStatus.total_calls_today ?? 0}</span>
              <span className="smifs-kb-count-label">Calls today</span>
            </div>
            <div className="smifs-kb-count" data-testid="deck-p50">
              <span className="smifs-kb-count-value">{deckStatus.p50_latency_ms_last_50 ?? "—"}</span>
              <span className="smifs-kb-count-label">p50 ms (last 50)</span>
            </div>
            <div className="smifs-kb-count smifs-kb-count--warn" data-testid="deck-timeouts">
              <span className="smifs-kb-count-value">{deckStatus.timeouts_today ?? 0}</span>
              <span className="smifs-kb-count-label">Timeouts today</span>
            </div>
            <div className="smifs-kb-count" data-testid="deck-slow">
              <span className="smifs-kb-count-value">{deckStatus.slow_responses_today ?? 0}</span>
              <span className="smifs-kb-count-label">Slow responses</span>
            </div>
            <div className="smifs-kb-count" data-testid="deck-audience-drops">
              <span className="smifs-kb-count-value">{deckStatus.audience_drops_today ?? 0}</span>
              <span className="smifs-kb-count-label">Audience drops</span>
            </div>
          </div>
          {Array.isArray(deckStatus.recent_telemetry) && deckStatus.recent_telemetry.length > 0 && (
            <details className="smifs-kb-history" data-testid="deck-telemetry" open>
              <summary>Last {deckStatus.recent_telemetry.length} calls (telemetry collection)</summary>
              <ul>
                {deckStatus.recent_telemetry.slice(0, 10).map((row, idx) => (
                  <li key={idx} style={{ fontFamily: "monospace", fontSize: 11 }}>
                    {row.created_at?.slice(11, 19)} ·{" "}
                    <strong>{row.status === 200 ? "200" : String(row.status)}</strong>{" "}
                    · {row.elapsed_ms}ms · raw={row.results_count_raw ?? 0} · post-audience={row.results_count_post_audience ?? 0}
                    {row.audience_drops ? ` · drops=${row.audience_drops}` : ""}
                    {row.slow ? " · slow" : ""}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}

      <section
        className="smifs-kb-drop"
        onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        data-testid="kb-dropzone"
      >
        <Upload size={22} strokeWidth={2} />
        <p className="smifs-kb-drop-title">Upload PDF, DOCX, MD, or TXT</p>
        <p className="smifs-kb-drop-sub">Drag and drop here, or click to browse · max 10 MB per file · multiple files OK</p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.md,.txt"
          onChange={onPick}
          style={{ display: "none" }}
          data-testid="kb-file-input"
        />
        {uploading && <div className="smifs-kb-uploading">Uploading & embedding…</div>}
      </section>

      {toast && (
        <div className={`smifs-toast smifs-toast--${toast.kind}`} data-testid="kb-toast">
          {toast.kind === "ok" ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
          {toast.msg}
        </div>
      )}

      {loading ? (
        <div className="smifs-admin-loading">Loading knowledge base…</div>
      ) : (
        <div className="smifs-table-wrap">
          <table className="smifs-table" data-testid="kb-table">
            <thead>
              <tr>
                <th>Doc</th><th>Title</th><th>Source</th><th>Chunks</th><th>Filename</th><th>Uploaded</th><th></th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.doc_id} data-testid={`kb-row-${d.doc_id}`}>
                  <td><FileText size={12} style={{ marginRight: 6, opacity: 0.6 }} />{d.doc_id}</td>
                  <td className="smifs-table-cell-2">{d.doc_title}</td>
                  <td>
                    <span className={`smifs-status-pill smifs-status-pill--${d.source === "upload" ? "qualified" : "new"}`}>
                      {d.source}
                    </span>
                  </td>
                  <td className="smifs-mono-cell">{d.chunks}</td>
                  <td>{d.filename || "—"}</td>
                  <td className="smifs-mono-cell">{(d.uploaded_at || d.created_at || "").slice(0, 16).replace("T", " ")}</td>
                  <td>
                    {d.source === "upload" ? (
                      <button
                        type="button"
                        className="smifs-table-action"
                        onClick={() => remove(d.doc_id)}
                        data-testid={`kb-delete-${d.doc_id}`}
                        title="Remove from knowledge base"
                      >
                        <Trash2 size={13} />
                      </button>
                    ) : (
                      <span className="smifs-table-cell-sub">seed</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
