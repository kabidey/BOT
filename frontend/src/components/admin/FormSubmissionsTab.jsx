import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Mail, RefreshCw, CheckCircle2, AlertCircle, Clock, FileDown } from "lucide-react";

/**
 * Phase 26.2.C — Admin Forms tab.
 *
 * Lists every dynamic-form submission (demand_capture, referral_capture,
 * feedback_capture, complaint_capture, callback_request). Supports filter
 * by form_type / persona / email_status, row-expand for full form data +
 * conversation excerpt, retry-send for failed emails, CSV export.
 */

const FORM_TYPES = [
  { id: "",                 label: "All forms" },
  { id: "demand_capture",   label: "Demand", color: "#0F766E" },
  { id: "referral_capture", label: "Referral", color: "#7C3AED" },
  { id: "feedback_capture", label: "Feedback", color: "#0D9488" },
  { id: "complaint_capture", label: "Complaint", color: "#C04444" },
  { id: "callback_request", label: "Callback", color: "#B45309" },
];

const STATUS_COLORS = {
  sent:    "#10B981",
  pending: "#D97706",
  failed:  "#DC2626",
};

const PERSONAS = ["", "visitor", "client", "employee"];

function fmtTs(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  } catch { return iso; }
}

function FormBadge({ formId }) {
  const meta = FORM_TYPES.find(f => f.id === formId);
  return (
    <span
      style={{
        display: "inline-block", padding: "2px 10px", borderRadius: 12,
        background: meta?.color ? `${meta.color}22` : "#e5e7eb",
        color: meta?.color || "#444",
        fontSize: 11, fontWeight: 700, letterSpacing: ".04em", textTransform: "uppercase",
      }}
      data-testid={`form-badge-${formId}`}
    >
      {(meta?.label || formId).replace("_", " ")}
    </span>
  );
}

function StatusPill({ status }) {
  const color = STATUS_COLORS[status] || "#6B7280";
  const Icon = status === "sent" ? CheckCircle2 : status === "failed" ? AlertCircle : Clock;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color }}
          data-testid={`email-status-${status}`}>
      <Icon size={13} strokeWidth={2.5} />
      <span style={{ fontSize: 12, fontWeight: 600, textTransform: "capitalize" }}>{status || "—"}</span>
    </span>
  );
}

function ExpandedRow({ row, api, onRetry }) {
  const [retryState, setRetryState] = useState(null);
  const formData = row.form_data || {};
  const excerpt = row.conversation_excerpt || [];

  const onRetryClick = async () => {
    setRetryState({ loading: true });
    try {
      const { data } = await api.post(`/admin/forms/${row.submission_id}/retry`);
      setRetryState({ ok: data.ok, detail: data.detail, status: data.status });
      if (onRetry) onRetry();
    } catch (err) {
      setRetryState({ ok: false, detail: err?.response?.data?.detail || err.message });
    }
  };

  return (
    <tr className="smifs-forms-row-expanded">
      <td colSpan={6} style={{ background: "rgba(2,55,38,0.04)", padding: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
          <div>
            <h4 style={{ marginTop: 0, fontSize: 13, color: "#0B3B2C", textTransform: "uppercase", letterSpacing: ".05em" }}>Form data</h4>
            <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
              <tbody>
                {Object.keys(formData).map((k) => (
                  <tr key={k}>
                    <td style={{ padding: "4px 8px", color: "#0B3B2C", fontWeight: 600, width: "36%", verticalAlign: "top" }}>
                      {k.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())}
                    </td>
                    <td style={{ padding: "4px 8px", color: "#1A1A1A", whiteSpace: "pre-wrap" }}>
                      {String(formData[k] ?? "—")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ marginTop: 16, display: "flex", gap: 8 }}>
              <button
                type="button"
                onClick={onRetryClick}
                disabled={retryState?.loading}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "6px 12px", border: "1px solid #023726", color: "#023726",
                  background: "white", borderRadius: 6, fontWeight: 600, cursor: "pointer",
                }}
                data-testid={`retry-${row.submission_id}`}
              >
                <RefreshCw size={13} strokeWidth={2.5} />
                {retryState?.loading ? "Sending…" : "Retry send"}
              </button>
              {retryState && !retryState.loading && (
                <span style={{ fontSize: 12, color: retryState.ok ? "#10B981" : "#DC2626", alignSelf: "center" }}>
                  {retryState.ok ? `✓ ${retryState.status || "sent"} — ${retryState.detail || ""}` : `✗ ${retryState.detail}`}
                </span>
              )}
            </div>
          </div>
          <div>
            <h4 style={{ marginTop: 0, fontSize: 13, color: "#0B3B2C", textTransform: "uppercase", letterSpacing: ".05em" }}>
              Conversation excerpt (last 8 turns)
            </h4>
            <div style={{ maxHeight: 320, overflowY: "auto", border: "1px solid #E2EBE2", borderRadius: 4, padding: 8 }}>
              {excerpt.length === 0 ? (
                <p style={{ color: "#888", fontSize: 12, margin: 0 }}>No conversation captured.</p>
              ) : excerpt.map((m, i) => (
                <div key={i} style={{
                  padding: "6px 8px", marginBottom: 4, borderRadius: 4,
                  background: m.role === "user" ? "rgba(2,55,38,0.05)" : "white",
                  borderLeft: `3px solid ${m.role === "user" ? "#065B40" : "#098C62"}`,
                }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: "#0B3B2C", textTransform: "uppercase" }}>
                    {m.role}
                  </div>
                  <div style={{ fontSize: 12, whiteSpace: "pre-wrap", marginTop: 2 }}>
                    {(m.content || "").slice(0, 600)}
                  </div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 12, fontSize: 11, color: "#6B7280" }}>
              <div>Session id: <code>{row.session_id || "—"}</code></div>
              <div>Submission id: <code>{row.submission_id}</code></div>
              <div>Priority: {row.priority || "normal"}</div>
              {row.email_detail && <div>Email detail: {row.email_detail}</div>}
            </div>
          </div>
        </div>
      </td>
    </tr>
  );
}

export default function FormSubmissionsTab({ api }) {
  const [rows, setRows] = useState([]);
  const [counts, setCounts] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [filterForm, setFilterForm] = useState("");
  const [filterPersona, setFilterPersona] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [expandedId, setExpandedId] = useState(null);

  const fetchRows = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = {};
      if (filterForm) params.form_id = filterForm;
      if (filterPersona) params.persona = filterPersona;
      if (filterStatus) params.email_status = filterStatus;
      const { data } = await api.get("/admin/forms/submissions", { params });
      setRows(data.rows || []);
      setCounts(data.counts || {});
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchRows(); /* eslint-disable-next-line */ }, [filterForm, filterPersona, filterStatus]);

  const todayCount = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    return rows.filter(r => (r.submitted_at || "").slice(0, 10) === today).length;
  }, [rows]);

  const exportCSV = () => {
    const headers = ["submitted_at", "form_id", "persona", "session_id", "email_status", "priority"];
    const lines = [headers.join(",")];
    for (const r of rows) {
      lines.push(headers.map(h => JSON.stringify(r[h] ?? "")).join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `form_submissions_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="smifs-admin-tab" data-testid="admin-forms-tab">
      <header className="smifs-admin-head">
        <div>
          <h2 style={{ margin: 0 }}>Form Submissions</h2>
          <p style={{ margin: "6px 0 0", color: "#6B7280" }}>
            All dynamic-form responses captured by the chat surface.
          </p>
        </div>
        <button
          type="button"
          onClick={exportCSV}
          disabled={rows.length === 0}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "8px 14px", border: "1px solid #023726", color: "#023726",
            background: "white", borderRadius: 6, fontWeight: 600, cursor: "pointer",
          }}
          data-testid="forms-export-csv"
        >
          <FileDown size={14} strokeWidth={2.5} />
          Export CSV
        </button>
      </header>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 12, margin: "16px 0" }}>
        <Counter label="Total today" value={todayCount} />
        <Counter label="Pending email" value={counts.pending || 0} color="#D97706" />
        <Counter label="Failed email" value={counts.failed || 0} color="#DC2626" />
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12, alignItems: "center" }}>
        <select value={filterForm} onChange={(e) => setFilterForm(e.target.value)} data-testid="forms-filter-type">
          {FORM_TYPES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
        <select value={filterPersona} onChange={(e) => setFilterPersona(e.target.value)} data-testid="forms-filter-persona">
          {PERSONAS.map(p => <option key={p} value={p}>{p ? p[0].toUpperCase() + p.slice(1) : "All personas"}</option>)}
        </select>
        <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)} data-testid="forms-filter-status">
          <option value="">All statuses</option>
          <option value="sent">Sent</option>
          <option value="pending">Pending</option>
          <option value="failed">Failed</option>
        </select>
        <button
          type="button"
          onClick={fetchRows}
          disabled={loading}
          style={{
            padding: "6px 12px", border: "1px solid #023726", borderRadius: 6,
            background: "white", cursor: "pointer", color: "#023726", fontWeight: 600,
          }}
          data-testid="forms-refresh"
        >
          <RefreshCw size={12} strokeWidth={2.5} style={{ marginRight: 4 }} />
          Refresh
        </button>
        <span style={{ color: "#6B7280", fontSize: 13 }}>
          {loading ? "Loading…" : `${rows.length} of ${counts.total || 0}`}
        </span>
      </div>

      {error && (
        <div style={{ padding: 12, background: "#FEE2E2", borderRadius: 6, color: "#B91C1C", marginBottom: 12 }}>
          {error}
        </div>
      )}

      <div style={{ background: "white", border: "1px solid #E2EBE2", borderRadius: 6, overflow: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ background: "#F3F8F3", borderBottom: "1px solid #E2EBE2" }}>
              <th style={{ padding: "10px 8px", width: 28 }}></th>
              <th style={{ padding: "10px 8px", textAlign: "left" }}>Submitted</th>
              <th style={{ padding: "10px 8px", textAlign: "left" }}>Form</th>
              <th style={{ padding: "10px 8px", textAlign: "left" }}>Persona</th>
              <th style={{ padding: "10px 8px", textAlign: "left" }}>Submitter</th>
              <th style={{ padding: "10px 8px", textAlign: "left" }}>Email</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !loading && (
              <tr><td colSpan={6} style={{ padding: 24, textAlign: "center", color: "#6B7280" }}>
                No form submissions match these filters.
              </td></tr>
            )}
            {rows.map((r) => {
              const isOpen = expandedId === r.submission_id;
              const submitterName = (r.form_data || {}).name
                                   || (r.form_data || {}).referrer_name
                                   || (r.form_data || {}).lead_name
                                   || (r.session_id ? r.session_id.slice(0, 8) : "—");
              return (
                <>
                  <tr
                    key={r.submission_id}
                    onClick={() => setExpandedId(isOpen ? null : r.submission_id)}
                    style={{ borderBottom: "1px solid #F0F0F0", cursor: "pointer" }}
                    data-testid={`forms-row-${r.submission_id}`}
                  >
                    <td style={{ padding: "8px", color: "#023726" }}>
                      {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </td>
                    <td style={{ padding: "8px" }}>{fmtTs(r.submitted_at)}</td>
                    <td style={{ padding: "8px" }}><FormBadge formId={r.form_id} /></td>
                    <td style={{ padding: "8px", textTransform: "capitalize" }}>{r.persona || "—"}</td>
                    <td style={{ padding: "8px" }}>{submitterName}</td>
                    <td style={{ padding: "8px" }}><StatusPill status={r.email_status} /></td>
                  </tr>
                  {isOpen && <ExpandedRow row={r} api={api} onRetry={fetchRows} />}
                </>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Counter({ label, value, color }) {
  return (
    <div style={{
      background: "white", border: "1px solid #E2EBE2", borderRadius: 6,
      padding: "12px 16px",
    }}>
      <div style={{ fontSize: 11, color: "#6B7280", textTransform: "uppercase", letterSpacing: ".05em" }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 600, color: color || "#0B3B2C", marginTop: 4 }}>{value}</div>
    </div>
  );
}
