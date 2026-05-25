import { useEffect, useState, useMemo } from "react";
import { TrendingUp, DollarSign, Calendar, MailCheck, MailX, MailWarning, X, RefreshCw } from "lucide-react";

const PRODUCT_LABEL = {
  mutual_fund: "Mutual Fund", aif: "AIF", pms: "PMS",
  fd: "Fixed Deposit", insurance: "Insurance",
  ncd_primary: "NCD Primary Issue",
  sif: "SIF",
};
const STATUS_OPTIONS = ["submitted", "logged", "funded", "reconciled", "cancelled"];

// Phase 21 — keys that the CURRENT FE schema knows how to display per
// product. Anything in `product_details` that's not in this list (or in
// the matching transfer sub-object) is rendered under a "Legacy fields"
// collapsible so old rows from pre-Phase-21 still surface without
// cluttering the new drawer view.
const CURRENT_PRODUCT_KEYS = {
  mutual_fund: ["amc_name", "scheme_name", "scheme_type", "frequency"],
  aif:         ["aif_name", "commitment_amount_inr"],
  pms:         ["pms_provider", "strategy_name", "corpus_inr"],
  fd:          ["issuer_name", "issuer_type", "tenure_months", "payout_frequency", "fd_type"],
  insurance:   ["carrier", "product_type", "policy_term_years",
                "premium_paying_term_years", "premium_frequency",
                "sum_assured_inr", "premium_amount_inr"],
  ncd_primary: ["issuer_name", "series_option", "application_amount_inr",
                "number_of_ncds", "interest_frequency", "asba_upi_reference"],
  sif:         ["sif_name", "strategy_theme", "investment_type", "frequency",
                "lock_in_months"],
};
const CURRENT_TRANSFER_KEYS = {
  arn_transfer:  ["folio_numbers", "amc_name", "scheme_name", "aif_name", "sif_name",
                   "commitment_account_id", "folio_account_id", "aum_inr", "arn_remarks"],
  aprn_transfer: ["pms_provider", "strategy_name", "portfolio_account_id",
                   "corpus_inr", "aprn_remarks"],
};
const _ALWAYS_VISIBLE_KEYS = new Set(["deck_vehicle", "subtype"]);

// Phase 19 — visual taxonomy for the four send statuses + legacy reasons.
const EMAIL_STATUS_META = {
  sent:                 { tone: "ok",   icon: MailCheck,   label: "Sent" },
  draft_only:           { tone: "skip", icon: MailX,       label: "Draft only" },
  smtp_auth_disabled:   { tone: "warn", icon: MailWarning, label: "SMTP auth disabled" },
  failed_with_fallback: { tone: "warn", icon: MailWarning, label: "Failed · fallback to draft" },
  // legacy
  smtp_not_configured:  { tone: "skip", icon: MailX,       label: "SMTP not configured" },
  no_recipient:         { tone: "skip", icon: MailX,       label: "No recipient" },
};

function emailStatusBadge(status, sent) {
  const meta = EMAIL_STATUS_META[status]
    || (sent
        ? EMAIL_STATUS_META.sent
        : { tone: "skip", icon: MailX, label: status || "—" });
  const Icon = meta.icon;
  return (
    <span className={`smifs-admin-pill smifs-admin-pill--${meta.tone}`} data-testid={`email-status-${status || "unknown"}`}>
      <Icon size={11} /> {meta.label}
    </span>
  );
}

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
  // Phase 17 → 21 — was `arnOnly` boolean. Now a 3-way enum:
  //   ""        → all
  //   "arn"     → ARN Transfer rows (MF / AIF / SIF; subtype = "arn_transfer")
  //   "aprn"    → APRN Transfer rows (PMS; subtype = "aprn_transfer")
  const [transferFilter, setTransferFilter] = useState("");

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
      if (transferFilter === "arn")  params.subtype = "arn_transfer";
      if (transferFilter === "aprn") params.subtype = "aprn_transfer";
      const { data } = await adminApi.get("/admin/sales", { params });
      setRows(data.items || []);
      setKpis(data.kpis || {});
      setTotal(data.total || 0);
    } catch (e) {
      setErr(e?.response?.data?.detail || "Failed to load sales.");
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [productFilter, statusFilter, transferFilter]);

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
      const routing = data.routing || {};
      const summary = data.ok
        ? `Sent → TO: ${(routing.to || []).join(", ")}\nCC (${(routing.cc || []).length}): ${(routing.cc || []).join(", ")}`
        : `Status: ${data.reason}${(routing.to || []).length ? `\nWould-be TO: ${routing.to.join(", ")}` : ""}`;
      alert(summary);
      load();
      // refresh the drawer so the routing card updates immediately
      const { data: detail } = await adminApi.get(`/admin/sales/${drawerData.submission_id}`);
      setDrawerData(detail);
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
        {/* Phase 21 — was MF-only ARN toggle; now a 3-way transfer filter
            covering ARN (MF/AIF/SIF) and APRN (PMS). */}
        <label className="smifs-admin-arn-toggle" data-testid="sales-filter-transfer-row">
          Transfer subtype
          <select value={transferFilter} onChange={(e) => setTransferFilter(e.target.value)}
                  data-testid="sales-filter-transfer">
            <option value="">All</option>
            <option value="arn">ARN Transfer</option>
            <option value="aprn">APRN Transfer</option>
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
            <th>Vehicle</th>
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
          {rows.map((r) => {
            const vName = r.vehicle_name || "—";
            const vShort = vName.length > 32 ? vName.slice(0, 32) + "…" : vName;
            const isArn  = r.subtype === "arn_transfer";
            const isAprn = r.subtype === "aprn_transfer";
            return (
              <tr key={r.submission_id} onClick={() => openDrawer(r.submission_id)} className="smifs-admin-row-click" data-testid={`sales-row-${r.submission_id}`}>
                <td><b>{r.submission_id}</b></td>
                <td>
                  {PRODUCT_LABEL[r.product] || r.product}
                  {isArn  && <span className="smifs-admin-pill smifs-admin-pill--arn"
                                    data-testid={`sales-arn-badge-${r.submission_id}`}>ARN</span>}
                  {isAprn && <span className="smifs-admin-pill smifs-admin-pill--aprn"
                                    data-testid={`sales-aprn-badge-${r.submission_id}`}>APRN</span>}
                </td>
                <td title={vName} data-testid={`sales-vehicle-${r.submission_id}`}>{vShort}</td>
                <td>{r.client_name_masked}</td>
                <td>{fmtINR(r.amount_inr)}</td>
                <td>{fmtDate(r.expected_login_date)}</td>
                <td><span className={`smifs-admin-status smifs-admin-status--${r.status}`}>{r.status}</span></td>
                <td data-testid={`sales-email-cell-${r.submission_id}`}>
                  {emailStatusBadge(r.email_status, r.email_sent)}
                </td>
                <td className="smifs-admin-dim">{(r.created_at || "").slice(0, 19).replace("T", " ")}</td>
              </tr>
            );
          })}
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
                  <div><b>Product</b><br/>{PRODUCT_LABEL[drawerData.product]}
                    {drawerData.subtype === "arn_transfer" && (
                      <span className="smifs-admin-pill smifs-admin-pill--arn"
                            data-testid="sales-drawer-arn-badge"> ARN Transfer</span>
                    )}
                    {drawerData.subtype === "aprn_transfer" && (
                      <span className="smifs-admin-pill smifs-admin-pill--aprn"
                            data-testid="sales-drawer-aprn-badge"> APRN Transfer</span>
                    )}
                  </div>
                  <div><b>Vehicle</b><br/>{drawerData.vehicle_name || "—"}
                    {drawerData.vehicle_type ? <span className="smifs-admin-dim"> · {drawerData.vehicle_type}</span> : null}
                  </div>
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
                <div className="smifs-admin-section">
                  {drawerData.subtype === "arn_transfer"  ? "ARN Transfer details"
                   : drawerData.subtype === "aprn_transfer" ? "APRN Transfer details"
                   : `${PRODUCT_LABEL[drawerData.product]} specifics`}
                </div>
                <div className="smifs-admin-detail-grid" data-testid="sales-drawer-product-details">
                  {(() => {
                    const pd = { ...(drawerData.product_details || {}) };
                    // Flatten ARN / APRN sub-object into the grid.
                    for (const subKey of ["arn_transfer", "aprn_transfer"]) {
                      const sub = pd[subKey];
                      if (sub && typeof sub === "object") {
                        delete pd[subKey];
                        for (const [k, v] of Object.entries(sub)) {
                          if (!(k in pd)) pd[k] = v;
                        }
                      }
                    }
                    // Phase 21 — split into "current" vs "legacy" keys per
                    // product (or per transfer subtype). Legacy keys go into
                    // a collapsible so old rows with dropped fields (e.g.
                    // `category`, `fee_structure`, `coupon_rate_pct`) still
                    // surface without cluttering the new drawer view.
                    const product = drawerData.product;
                    const subtype = drawerData.subtype;
                    const okKeys = new Set([
                      ...(subtype && CURRENT_TRANSFER_KEYS[subtype]
                        ? CURRENT_TRANSFER_KEYS[subtype]
                        : (CURRENT_PRODUCT_KEYS[product] || [])),
                      ..._ALWAYS_VISIBLE_KEYS,
                    ]);
                    const currentEntries = [];
                    const legacyEntries = [];
                    for (const [k, v] of Object.entries(pd)) {
                      if (okKeys.has(k)) currentEntries.push([k, v]);
                      else                legacyEntries.push([k, v]);
                    }
                    return (
                      <>
                        {currentEntries.map(([k, v]) => (
                          <div key={k} data-testid={`sales-drawer-field-${k}`}>
                            <b>{k.replace(/_/g, " ")}</b><br/>{String(v)}
                          </div>
                        ))}
                        {legacyEntries.length > 0 && (
                          <details className="smifs-admin-legacy-fields"
                                   data-testid="sales-drawer-legacy-fields"
                                   style={{ gridColumn: "1 / -1", marginTop: 6 }}>
                            <summary style={{ cursor: "pointer", color: "var(--smifs-fg-muted, #6e7a78)" }}>
                              Legacy fields ({legacyEntries.length}) — captured before Phase 21 cleanup
                            </summary>
                            <div className="smifs-admin-detail-grid" style={{ marginTop: 6 }}>
                              {legacyEntries.map(([k, v]) => (
                                <div key={k} data-testid={`sales-drawer-legacy-${k}`}>
                                  <b>{k.replace(/_/g, " ")}</b><br/>
                                  {typeof v === "object" ? JSON.stringify(v) : String(v)}
                                </div>
                              ))}
                            </div>
                          </details>
                        )}
                      </>
                    );
                  })()}
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

                <div className="smifs-admin-section">Email routing</div>
                <div className="smifs-admin-drawer-email-status" data-testid="sales-drawer-email-status">
                  {emailStatusBadge(drawerData.email_status, drawerData.email_sent)}
                  {drawerData.email_sent_at && (
                    <span className="smifs-admin-dim" style={{ marginLeft: 8 }}>
                      sent at {drawerData.email_sent_at.slice(0, 19).replace("T", " ")}
                    </span>
                  )}
                </div>
                {(() => {
                  const routing = drawerData.email_routing || {};
                  const toList = routing.to || [];
                  const chain = routing.chain || [];
                  const opsCc = routing.ops_cc || [];
                  const allCc = routing.cc || [];
                  // Fallback for legacy rows that don't carry the structured payload yet.
                  const legacy = !routing.to && Array.isArray(drawerData.email_recipients);
                  return (
                    <div className="smifs-admin-detail-grid" style={{ marginTop: 8 }} data-testid="sales-drawer-routing">
                      <div>
                        <b>TO</b>
                        <ul className="smifs-admin-recipient-list" data-testid="sales-drawer-to-list">
                          {(legacy ? drawerData.email_recipients.slice(0, 1) : toList).map((e) => (
                            <li key={e} data-testid={`sales-drawer-to-${e}`}><code>{e}</code></li>
                          ))}
                          {(legacy ? drawerData.email_recipients.length === 0 : toList.length === 0) && (
                            <li className="smifs-admin-dim">—</li>
                          )}
                        </ul>
                      </div>
                      <div>
                        <b>CC — Manager chain</b>
                        {legacy ? (
                          <div className="smifs-admin-dim">(legacy — re-send to populate)</div>
                        ) : (
                          <ol className="smifs-admin-recipient-list" data-testid="sales-drawer-chain">
                            {chain.length === 0 && <li className="smifs-admin-dim">none resolved</li>}
                            {chain.map((c) => (
                              <li key={c.employee_id} data-testid={`sales-drawer-chain-l${c.level}`}>
                                <span className="smifs-admin-dim">L{c.level}</span> · <b>{c.name}</b>
                                <span className="smifs-admin-dim"> · {c.designation || ""}</span>
                                <br/><code>{c.email}</code>
                              </li>
                            ))}
                          </ol>
                        )}
                        {routing.max_hops_reached && (
                          <div className="smifs-admin-alert" style={{ marginTop: 6 }}>
                            Chain capped at 10 levels — additional managers above were not added.
                          </div>
                        )}
                        {Array.isArray(routing.errors) && routing.errors.length > 0 && (
                          <div className="smifs-admin-dim" style={{ marginTop: 6 }}>
                            chain notes: {routing.errors.join(", ")}
                          </div>
                        )}
                      </div>
                      <div>
                        <b>CC — Fixed Ops</b>
                        <ul className="smifs-admin-recipient-list" data-testid="sales-drawer-ops-cc">
                          {(legacy ? drawerData.email_recipients.slice(1) : opsCc).map((e) => (
                            <li key={e} data-testid={`sales-drawer-ops-${e}`}><code>{e}</code></li>
                          ))}
                          {(legacy ? drawerData.email_recipients.length <= 1 : opsCc.length === 0) && (
                            <li className="smifs-admin-dim">none</li>
                          )}
                        </ul>
                      </div>
                      {!legacy && (
                        <div className="smifs-admin-dim" style={{ gridColumn: "1 / -1" }}>
                          Total CC: <b>{allCc.length}</b> · cache hit: <b>{routing.cache_hit ? "yes" : "no"}</b>
                          {routing.resolved_at && (
                            <> · resolved at {routing.resolved_at.slice(11, 19)}</>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
