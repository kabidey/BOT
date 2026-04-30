import { useEffect, useState } from "react";
import { Database, Loader2, ShieldCheck, AlertCircle, FileText, ChevronRight, RefreshCw } from "lucide-react";

export default function ArchivesTab({ api }) {
  const [archives, setArchives] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [roleFilter, setRoleFilter] = useState("all");
  const [q, setQ] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(false);
  const [ingestResult, setIngestResult] = useState(null);
  const [error, setError] = useState("");

  const fetchArchives = async (resetOffset = false) => {
    setLoading(true);
    setError("");
    try {
      const params = { role: roleFilter, limit: 50, offset: resetOffset ? 0 : offset };
      if (q.trim()) params.q = q.trim();
      if (dateFrom) params.date_from = new Date(dateFrom).toISOString();
      if (dateTo) params.date_to = new Date(dateTo).toISOString();
      const { data } = await api.get("/admin/archives", { params });
      setArchives(data.archives || []);
      setTotal(data.total || 0);
      if (resetOffset) setOffset(0);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchArchives(true); /* eslint-disable-next-line */ }, [roleFilter]);
  // Re-fetch when offset changes
  useEffect(() => { if (offset !== 0) fetchArchives(false); /* eslint-disable-next-line */ }, [offset]);

  const openDetail = async (id) => {
    setSelected(id);
    setDetail(null);
    try {
      const { data } = await api.get(`/admin/archives/${id}`);
      setDetail(data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    }
  };

  const toggleConsent = async (id, current) => {
    try {
      const { data } = await api.patch(`/admin/archives/${id}`, { consent_to_ingest: !current });
      setArchives((prev) => prev.map((a) => (a.session_id === id ? { ...a, ...data, messages: undefined } : a)));
      if (selected === id) setDetail(data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    }
  };

  const runIngest = async (dry) => {
    setBusy(true);
    setIngestResult(null);
    try {
      const { data } = await api.post("/admin/archives/ingest_to_rag", { dry_run: dry, role: roleFilter });
      setIngestResult(data);
      if (!dry) await fetchArchives(true);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  const onSearchSubmit = (e) => {
    e?.preventDefault?.();
    fetchArchives(true);
  };

  return (
    <div className="smifs-admin-content" data-testid="archives-tab">
      <header className="smifs-admin-content-head">
        <div>
          <p className="smifs-admin-eyebrow">Archives</p>
          <h2 className="smifs-admin-title">Verified-session archives</h2>
          <p className="smifs-admin-subtitle">
            Snapshots of verified employee &amp; client conversations. Employees auto-consent
            to RAG ingestion; client archives require explicit consent.
          </p>
        </div>
        <div className="smifs-admin-toolbar">
          <form onSubmit={onSearchSubmit} className="smifs-admin-search-form" role="search">
            <input
              type="text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search by name, UCC, employee ID, email, intent…"
              className="smifs-admin-input"
              data-testid="archives-search-input"
            />
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="smifs-admin-input smifs-admin-input--date"
              data-testid="archives-date-from"
              title="From date"
            />
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="smifs-admin-input smifs-admin-input--date"
              data-testid="archives-date-to"
              title="To date"
            />
            <button type="submit" className="smifs-admin-btn-secondary" data-testid="archives-search-submit">Search</button>
          </form>
          <select
            value={roleFilter}
            onChange={(e) => setRoleFilter(e.target.value)}
            className="smifs-admin-select"
            data-testid="archives-role-filter"
          >
            <option value="all">All roles</option>
            <option value="employee">Employees</option>
            <option value="client">Clients</option>
            <option value="visitor">Visitors</option>
          </select>
          <button className="smifs-admin-btn-secondary" onClick={() => fetchArchives(true)} disabled={loading} data-testid="archives-refresh">
            <RefreshCw size={13} strokeWidth={2.25} /> Refresh
          </button>
          <button className="smifs-admin-btn-secondary" onClick={() => runIngest(true)} disabled={busy} data-testid="archives-dry-run">
            Dry-run ingest
          </button>
          <button className="smifs-admin-btn-primary" onClick={() => runIngest(false)} disabled={busy} data-testid="archives-ingest-now">
            <Database size={13} strokeWidth={2.25} /> Ingest to RAG
          </button>
        </div>
      </header>

      {error && <div className="smifs-admin-err" data-testid="archives-error"><AlertCircle size={12} /> {error}</div>}
      {ingestResult && (
        <div className="smifs-admin-info" data-testid="archives-ingest-result">
          {ingestResult.dry_run ? "Dry-run" : "Ingested"}: scanned={ingestResult.scanned}, ingested={ingestResult.ingested}, chunks_added={ingestResult.chunks_added}
        </div>
      )}

      <div className="smifs-admin-split">
        <div className="smifs-admin-list" data-testid="archives-list">
          <div className="smifs-admin-list-caption" data-testid="archives-count-caption">
            Showing {archives.length} of {total}{q.trim() ? ` · matching "${q.trim()}"` : ""}
          </div>
          {loading ? (
            <div className="smifs-admin-loading"><Loader2 size={14} className="spin" /> Loading archives…</div>
          ) : archives.length === 0 ? (
            <div className="smifs-admin-empty">No archives yet. Verify a session to populate this view.</div>
          ) : archives.map((a) => (
            <button
              key={a.session_id}
              type="button"
              className={`smifs-admin-row ${selected === a.session_id ? "smifs-admin-row--on" : ""}`}
              onClick={() => openDetail(a.session_id)}
              data-testid={`archive-row-${a.session_id}`}
            >
              <div className="smifs-admin-row-main">
                <span className={`smifs-admin-tag smifs-admin-tag--${a.session_type}`}>{a.session_type}</span>
                <span className="smifs-admin-row-name">
                  {a.identity_summary?.first_name || a.identity_summary?.name || a.session_id.slice(0, 8)}
                </span>
                {a.identity_summary?.designation && <span className="smifs-admin-row-meta"> · {a.identity_summary.designation}</span>}
                {a.identity_summary?.ucc && <span className="smifs-admin-row-meta"> · UCC {a.identity_summary.ucc}</span>}
              </div>
              <div className="smifs-admin-row-side">
                {a.consent_to_ingest && <span className="smifs-admin-pill"><ShieldCheck size={10} /> consented</span>}
                {a.ingested_to_rag && <span className="smifs-admin-pill smifs-admin-pill--gold"><Database size={10} /> in KB</span>}
                <ChevronRight size={14} strokeWidth={2.25} />
              </div>
            </button>
          ))}
        </div>

        <div className="smifs-admin-detail" data-testid="archive-detail">
          {!selected ? (
            <div className="smifs-admin-empty">Select an archive to inspect the transcript.</div>
          ) : !detail ? (
            <div className="smifs-admin-loading"><Loader2 size={14} className="spin" /> Loading…</div>
          ) : (
            <>
              <div className="smifs-admin-detail-head">
                <h3>{detail.identity_summary?.first_name || detail.session_id.slice(0, 12)}</h3>
                <button
                  type="button"
                  className="smifs-admin-btn-ghost"
                  onClick={() => toggleConsent(detail.session_id, detail.consent_to_ingest)}
                  data-testid={`archive-toggle-consent-${detail.session_id}`}
                >
                  {detail.consent_to_ingest ? "Revoke RAG consent" : "Grant RAG consent"}
                </button>
              </div>
              <p className="smifs-admin-row-meta">
                {detail.session_type} · verified {detail.verified_at} · {detail.ingested_to_rag ? `${detail.rag_chunks_added} chunks in RAG` : "not ingested"}
              </p>
              <div className="smifs-admin-transcript">
                {(detail.messages || []).map((m, i) => (
                  <div key={i} className={`smifs-admin-msg smifs-admin-msg--${m.role}`}>
                    <span className="smifs-admin-msg-role">{m.role}</span>
                    <FileText size={11} className="smifs-admin-msg-icon" />
                    <span className="smifs-admin-msg-content">{m.content}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
