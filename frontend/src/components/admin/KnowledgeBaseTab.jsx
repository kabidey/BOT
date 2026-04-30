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
  const [syncing, setSyncing] = useState(false);

  const loadStatus = async () => {
    try {
      const { data } = await api.get("/admin/knowledge/status");
      setKbStatus(data);
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
