import { useEffect, useState, useMemo } from "react";
import { TrendingUp, DollarSign, Calendar, MailCheck, MailX, X, RefreshCw } from "lucide-react";

const PRODUCT_LABEL = {
  mutual_fund: "Mutual Fund", aif: "AIF", pms: "PMS",
  fd: "Fixed Deposit", insurance: "Insurance",
  ncd_primary: "NCD Primary Issue",
};
const STATUS_OPTIONS = ["submitted", "logged", "funded", "reconciled", "cancelled"];

function fmtINR(n) {
  if (!n) return "—";
  const v = Number(n);
  if (v >= 1e7) return `₹${(v/1e7).toFixed(2)} Cr`;
  if (v >= 1e5) return `₹${(v/1e5).toFixed(2)} L`;
  return `₹${v.toLocaleString("en-IN")}`;
}

function fmtDate(s) {
  if (!s) return "—";
  return s.slice(0, 10);
}

export default function SalesPipelineTab({ api }) {
  const adminApi = api;
  const [rows, setRows] = useState([]);
  const [kpis, setKpis] = useState({});
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const [productFilter, setProductFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const [drawerSubId, setDrawerSubId] = useState(null);
  const [drawerData, setDrawerData] = useState(null);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [drawerErr, setDrawerErr] = useState("");
  const [statusBusy, setStatusBusy] = useState(false);
  const [resendBusy, setResendBusy] = useState(false);

  const load = async () => {
    setLoading(true); setErr("");
    try {
      const params = { limit: 100 };
      if (productFilter) params.product = productFilter;
      if (statusFilter) params.status = statusFilter;
      const { data } = await adminApi.get("/admin/sales", { params });
      setRows(data.items || []);
      setKpis(data.kpis || {});
      setTotal(data.total || 0);
    } catch (e) {
      setErr(e?.response?.data?.detail || "Failed to load sales.");
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [productFilter, statusFilter]);

  const openDrawer = async (submission_id) => {
    setDrawerSubId(submission_id);
    setDrawerData(null); setDrawerLoading(true); setDrawerErr("");
    try {
      const { data } = await adminApi.get(`/admin/sales/${submission_id}`);
      setDrawerData(data);
    } catch (e) {
      setDrawerErr(e?.response?.data?.detail || "Failed to load detail.");
    } finally { setDrawerLoading(false); }
  };

  const closeDrawer = () => { setDrawerSubId(null); setDrawerData(null); setDrawerErr(""); };

  const updateStatus = async (newStatus) => {
    if (!drawerData) return;
    setStatusBusy(true);
    try {
      await adminApi.patch(`/admin/sales/${drawerData.submission_id}/status`, { status: newStatus });
      setDrawerData({ ...drawerData, status: newStatus });
      setRows((rs) => rs.map((r) => r.submission_id === drawerData.submission_id ? { ...r, status: newStatus } : r));
    } catch (e) {
      alert(e?.response?.data?.detail || "Status update failed.");
    } finally { setStatusBusy(false); }
  };

  const resendEmail = async () => {
    if (!drawerData) return;
    setResendBusy(true);
    try {
      const { data } = await adminApi.post(`/admin/sales/${drawerData.submission_id}/resend_email`, {});
      alert(data.ok ? `Sent to ${data.recipients.join(", ")}` : `Skipped: ${data.reason}`);
      load();
    } catch (e) {
      alert(e?.response?.data?.detail || "Resend failed.");
    } finally { setResendBusy(false); }
  };

  const byProduct = useMemo(() => kpis.by_product_7d || [], [kpis]);

  return (
    <div className="smifs-admin-panel" data-testid="sales-pipeline-tab">
      <div className="smifs-admin-panel-head">
        <h2>Sales Pipeline</h2>
        <span className="smifs-admin-pill">{total} {total === 1 ? "sale" : "sales"}</span>
      </div>

      {/* KPI strip */}
      <div className="smifs-admin-kpis">
        <div className="smifs-admin-kpi">
          <Calendar size={14} />
          <div>
            <div className="smifs-admin-kpi-label">Today</div>
            <div className="smifs-admin-kpi-value">{kpis.today_count ?? 0} <span>· {fmtINR(kpis.today_total_inr)}</span></div>
          </div>
        </div>
        <div className="smifs-admin-kpi">
          <TrendingUp size={14} />
          <div>
            <div className="smifs-admin-kpi-label">Last 7 days</div>
            <div className="smifs-admin-kpi-value">{kpis.week_count ?? 0} <span>· {fmtINR(kpis.week_total_inr)}</span></div>
          </div>
        </div>
        <div className="smifs-admin-kpi smifs-admin-kpi--wide">
          <DollarSign size={14} />
          <div>
            <div className="smifs-admin-kpi-label">By product (7d)</div>
            <div className="smifs-admin-kpi-breakdown">
              {byProduct.length === 0 && <span className="smifs-admin-dim">No sales yet</span>}
              {byProduct.map((b) => (
                <span key={b.product} className="smifs-admin-prod-pill">
                  {PRODUCT_LABEL[b.product] || b.product}: <b>{b.count}</b>
                  <span className="smifs-admin-dim"> · {fmtINR(b.total_inr)}</span>
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="smifs-admin-filter-row">
        <label>Product
          <select value={productFilter} onChange={(e) => setProductFilter(e.target.value)} data-testid="sales-filter-product">
            <option value="">All</option>
            {Object.entries(PRODUCT_LABEL).map(([id, lbl]) => <option key={id} value={id}>{lbl}</option>)}
          </select>
        </label>
        <label>Status
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} data-testid="sales-filter-status">
            <option value="">All</option>
            {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <button className="smifs-admin-btn-ghost" onClick={load} disabled={loading} data-testid="sales-refresh">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {err && <div className="smifs-admin-alert">{err}</div>}

      <table className="smifs-admin-table" data-testid="sales-table">
        <thead>
          <tr>
            <th>Reference</th>
            <th>Product</th>
            <th>Employee</th>
            <th>Client</th>
            <th>Amount</th>
            <th>Login</th>
            <th>Status</th>
            <th>Email</th>
            <th>Submitted</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && !loading && (
            <tr><td colSpan={9} className="smifs-admin-empty">No sales yet.</td></tr>
          )}
          {rows.map((r) => (
            <tr key={r.submission_id} onClick={() => openDrawer(r.submission_id)} className="smifs-admin-row-click" data-testid={`sales-row-${r.submission_id}`}>
              <td><b>{r.submission_id}</b></td>
              <td>{PRODUCT_LABEL[r.product] || r.product}</td>
              <td>{r.employee_name}</td>
              <td>{r.client_name_masked}</td>
              <td>{fmtINR(r.amount_inr)}</td>
              <td>{fmtDate(r.expected_login_date)}</td>
              <td><span className={`smifs-admin-status smifs-admin-status--${r.status}`}>{r.status}</span></td>
              <td>{r.email_sent
                ? <span className="smifs-admin-pill smifs-admin-pill--ok"><MailCheck size={11} /> sent</span>
                : <span className="smifs-admin-pill smifs-admin-pill--skip"><MailX size={11} /> {r.email_status || "—"}</span>}</td>
              <td className="smifs-admin-dim">{(r.created_at || "").slice(0, 19).replace("T", " ")}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {drawerSubId && (
        <div className="smifs-admin-drawer-backdrop" onClick={closeDrawer}>
          <div className="smifs-admin-drawer" onClick={(e) => e.stopPropagation()} data-testid="sales-drawer">
            <div className="smifs-admin-drawer-head">
              <h3>{drawerSubId}</h3>
              <button className="smifs-admin-btn-ghost" onClick={closeDrawer} data-testid="sales-drawer-close"><X size={16}/></button>
            </div>
            {drawerLoading && <div className="smifs-admin-dim">Loading…</div>}
            {drawerErr && <div className="smifs-admin-alert">{drawerErr}</div>}
            {drawerData && (
              <div className="smifs-admin-drawer-body">
                <div className="smifs-admin-detail-grid">
                  <div><b>Product</b><br/>{PRODUCT_LABEL[drawerData.product]}</div>
                  <div><b>Amount</b><br/>{fmtINR(drawerData.amount_inr)}</div>
                  <div><b>Login date</b><br/>{fmtDate(drawerData.expected_login_date)}</div>
                  <div><b>Payment date</b><br/>{fmtDate(drawerData.expected_payment_date)}</div>
                </div>
                <div className="smifs-admin-section">Client</div>
                <div className="smifs-admin-detail-grid">
                  <div><b>Name</b><br/>{drawerData.client?.client_name}</div>
                  <div><b>PAN</b><br/><code>{drawerData.client?.client_pan}</code></div>
                  <div><b>Phone</b><br/>{drawerData.client?.client_phone}</div>
                  <div><b>Email</b><br/>{drawerData.client?.client_email}</div>
                </div>
                <div className="smifs-admin-section">{PRODUCT_LABEL[drawerData.product]} specifics</div>
                <div className="smifs-admin-detail-grid">
                  {Object.entries(drawerData.product_details || {}).map(([k, v]) => (
                    <div key={k}><b>{k.replace(/_/g, " ")}</b><br/>{String(v)}</div>
                  ))}
                </div>
                <div className="smifs-admin-section">Submitted by</div>
                <div className="smifs-admin-detail-grid">
                  <div><b>Name</b><br/>{drawerData.employee?.name}</div>
                  <div><b>Employee ID</b><br/>{drawerData.employee?.employee_id}</div>
                  <div><b>Designation</b><br/>{drawerData.employee?.designation}</div>
                  <div><b>Email</b><br/>{drawerData.employee?.email}</div>
                </div>
                {drawerData.remarks && (
                  <>
                    <div className="smifs-admin-section">Remarks</div>
                    <div className="smifs-admin-dim">{drawerData.remarks}</div>
                  </>
                )}
                <div className="smifs-admin-section">Workflow</div>
                <div className="smifs-admin-drawer-actions">
                  <label>Status:</label>
                  <select value={drawerData.status} disabled={statusBusy}
                          onChange={(e) => updateStatus(e.target.value)}
                          data-testid="sales-drawer-status">
                    {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                  <button className="smifs-admin-btn-ghost" onClick={resendEmail}
                          disabled={resendBusy} data-testid="sales-drawer-resend">
                    {resendBusy ? "Sending…" : "Resend email"}
                  </button>
                </div>
                <div className="smifs-admin-dim" style={{ marginTop: 12 }}>
                  Email status: <b>{drawerData.email_status || "—"}</b>
                  {drawerData.email_sent_at && <> · sent at {drawerData.email_sent_at.slice(0, 19).replace("T", " ")}</>}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
