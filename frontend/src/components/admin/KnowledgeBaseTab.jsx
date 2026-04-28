import { useEffect, useRef, useState } from "react";
import { Upload, Trash2, FileText, CheckCircle2, AlertTriangle } from "lucide-react";

export default function KnowledgeBaseTab({ api }) {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [toast, setToast] = useState(null); // {kind, msg}
  const inputRef = useRef(null);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/admin/docs");
      setDocs(data.docs || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const showToast = (kind, msg) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 4000);
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

  return (
    <div className="smifs-admin-page">
      <header className="smifs-admin-page-head">
        <p className="smifs-admin-eyebrow">Embeddings · sentence-transformers · 384-dim</p>
        <h2 className="smifs-admin-title">Knowledge Base</h2>
      </header>

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
