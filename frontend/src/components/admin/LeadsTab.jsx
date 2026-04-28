import { useEffect, useState } from "react";
import { X, Save } from "lucide-react";

const STATUSES = ["new", "contacted", "qualified", "closed"];

export default function LeadsTab({ api }) {
  const [filter, setFilter] = useState("all");
  const [leads, setLeads] = useState([]);
  const [loading, setLoading] = useState(true);
  const [active, setActive] = useState(null); // active lead with transcript
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get(`/admin/leads?status=${filter}&limit=200`);
      setLeads(data.leads || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const openLead = async (lead_id) => {
    try {
      const { data } = await api.get(`/admin/leads/${lead_id}`);
      setActive(data);
    } catch (_) { /* swallow */ }
  };

  const saveLead = async (lead_id, status, notes) => {
    setSaving(true);
    try {
      await api.patch(`/admin/leads/${lead_id}`, { status, notes });
      await load();
      // Refresh the side drawer with the updated lead
      const { data } = await api.get(`/admin/leads/${lead_id}`);
      setActive(data);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="smifs-admin-page">
      <header className="smifs-admin-page-head">
        <p className="smifs-admin-eyebrow">Lead capture · Callback queue</p>
        <h2 className="smifs-admin-title">Leads</h2>
        <div className="smifs-admin-filter-row" data-testid="leads-filter">
          {["all", ...STATUSES].map((s) => (
            <button
              key={s}
              type="button"
              className={`smifs-admin-filter ${filter === s ? "smifs-admin-filter--on" : ""}`}
              onClick={() => setFilter(s)}
              data-testid={`leads-filter-${s}`}
            >
              {s}
            </button>
          ))}
        </div>
      </header>

      {loading ? (
        <div className="smifs-admin-loading">Loading leads…</div>
      ) : leads.length === 0 ? (
        <div className="smifs-empty">No leads matching '{filter}'.</div>
      ) : (
        <div className="smifs-table-wrap">
          <table className="smifs-table" data-testid="leads-table">
            <thead>
              <tr>
                <th>Created</th><th>Type</th><th>Name</th><th>Email / Phone</th>
                <th>Asset</th><th>Range</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((l) => (
                <tr
                  key={l.lead_id}
                  className="smifs-table-row"
                  onClick={() => openLead(l.lead_id)}
                  data-testid={`lead-row-${l.lead_id}`}
                >
                  <td className="smifs-mono-cell">{(l.created_at || "").slice(0, 16).replace("T", " ")}</td>
                  <td>{l.form_type === "lead_capture" ? "Lead" : "Callback"}</td>
                  <td>{l.fields?.name || "—"}</td>
                  <td className="smifs-table-cell-2">
                    <div>{l.fields?.email}</div>
                    <div className="smifs-table-cell-sub">{l.fields?.phone}</div>
                  </td>
                  <td>{l.context?.asset_class || "—"}</td>
                  <td>{l.fields?.investment_range || l.fields?.preferred_time || "—"}</td>
                  <td>
                    <span className={`smifs-status-pill smifs-status-pill--${l.status}`}>{l.status || "new"}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {active && (
        <LeadDrawer lead={active} onClose={() => setActive(null)} onSave={saveLead} saving={saving} />
      )}
    </div>
  );
}

function LeadDrawer({ lead, onClose, onSave, saving }) {
  const [status, setStatus] = useState(lead.status || "new");
  const [notes, setNotes] = useState(lead.notes || "");
  return (
    <>
      <div className="smifs-popover-scrim" onClick={onClose} />
      <aside className="smifs-popover smifs-lead-drawer" data-testid="lead-drawer" role="dialog">
        <div className="smifs-popover-head">
          <div>
            <p className="smifs-popover-eyebrow">{lead.form_type === "lead_capture" ? "Lead capture" : "Callback request"}</p>
            <h3 className="smifs-popover-title">{lead.fields?.name || "—"}</h3>
            <p className="smifs-popover-section">{lead.fields?.email} · {lead.fields?.phone}</p>
          </div>
          <button type="button" className="smifs-popover-close" onClick={onClose} data-testid="lead-drawer-close">
            <X size={16} />
          </button>
        </div>
        <div className="smifs-popover-body">
          <div className="smifs-lead-grid">
            <Field label="Asset class">{lead.context?.asset_class || "—"}</Field>
            <Field label="Range / time">{lead.fields?.investment_range || lead.fields?.preferred_time || "—"}</Field>
            <Field label="City">{lead.fields?.city || "—"}</Field>
            <Field label="Topic">{lead.fields?.topic || "—"}</Field>
            <Field label="Created">{(lead.created_at || "").slice(0, 19).replace("T", " ")}</Field>
            <Field label="Lead ID"><span className="smifs-mono-cell">{lead.lead_id?.slice(0, 12)}</span></Field>
          </div>

          <h4 className="smifs-lead-section">Status</h4>
          <div className="smifs-admin-filter-row">
            {STATUSES.map((s) => (
              <button
                key={s}
                type="button"
                className={`smifs-admin-filter ${status === s ? "smifs-admin-filter--on" : ""}`}
                onClick={() => setStatus(s)}
                data-testid={`lead-status-${s}`}
              >
                {s}
              </button>
            ))}
          </div>

          <h4 className="smifs-lead-section">Advisor notes</h4>
          <textarea
            className="smifs-lead-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Add context or follow-up details…"
            data-testid="lead-notes"
            rows={3}
          />
          <button
            type="button"
            className="smifs-form-submit"
            onClick={() => onSave(lead.lead_id, status, notes)}
            disabled={saving}
            data-testid="lead-save"
          >
            {saving ? "Saving…" : "Save"} <Save size={13} />
          </button>

          <h4 className="smifs-lead-section">Recent transcript</h4>
          {lead.transcript?.length ? (
            <ol className="smifs-transcript">
              {lead.transcript.map((t, i) => (
                <li key={i} className={`smifs-transcript-row smifs-transcript-row--${t.role}`}>
                  <span className="smifs-transcript-role">{t.role}{t.intent ? ` · ${t.intent.toLowerCase()}` : ""}</span>
                  <span className="smifs-transcript-text">{(t.text || "").slice(0, 240)}</span>
                </li>
              ))}
            </ol>
          ) : (
            <div className="smifs-empty">No transcript captured for this session.</div>
          )}
        </div>
      </aside>
    </>
  );
}

function Field({ label, children }) {
  return (
    <div className="smifs-lead-field">
      <span className="smifs-form-label">{label}</span>
      <span className="smifs-lead-value">{children}</span>
    </div>
  );
}
