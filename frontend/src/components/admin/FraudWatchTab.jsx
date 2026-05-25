import { useEffect, useMemo, useState } from "react";
import { Shield, Eye, Ban, CheckCircle2, RefreshCw, X, AlertTriangle, MessageSquare } from "lucide-react";

const STATUS_FILTERS = [
  { id: "active",  label: "Active" },
  { id: "flagged", label: "Flagged" },
  { id: "blocked", label: "Blocked" },
  { id: "trusted", label: "Trusted" },
];

function StatusPill({ status }) {
  const tone =
    status === "blocked" ? "smifs-pill smifs-pill--danger" :
    status === "flagged" ? "smifs-pill smifs-pill--warn" :
    status === "trusted" ? "smifs-pill smifs-pill--ok" :
    "smifs-pill";
  return <span className={tone} data-testid={`fp-row-status-${status}`}>{status}</span>;
}

function ScoreBar({ value }) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  const tone =
    pct >= 75 ? "linear-gradient(90deg,#b9374a,#ec4f63)" :
    pct >= 40 ? "linear-gradient(90deg,#c08a2e,#e0a44a)" :
                "linear-gradient(90deg,#3f5e6a,#5a8190)";
  return (
    <div style={{ width: 110, height: 6, background: "rgba(255,255,255,0.08)", borderRadius: 999 }}>
      <div style={{ width: `${pct}%`, height: "100%", background: tone, borderRadius: 999 }} />
    </div>
  );
}

function fmtTs(s) {
  if (!s) return "—";
  try { return new Date(s).toLocaleString(); } catch (_) { return s; }
}

function maskHash(h) {
  if (!h) return "";
  if (h.length <= 14) return h;
  return `${h.slice(0, 8)}…${h.slice(-4)}`;
}

export default function FraudWatchTab({ api }) {
  const [filter, setFilter] = useState("active");
  const [rows, setRows] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [active, setActive] = useState(null);
  const [activeBusy, setActiveBusy] = useState(false);
  const [actionErr, setActionErr] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const [listRes, sumRes] = await Promise.all([
        api.get(`/admin/fingerprint/list?status=${filter}&limit=100`),
        api.get(`/admin/fingerprint/summary`),
      ]);
      setRows(listRes.data?.items || []);
      setSummary(sumRes.data || null);
    } catch (_) { /* swallow */ }
    finally { setLoading(false); }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const openRow = async (hash) => {
    try {
      const { data } = await api.get(`/admin/fingerprint/${encodeURIComponent(hash)}`);
      setActive(data);
    } catch (_) { /* swallow */ }
  };

  const reloadActive = async (hash) => {
    try {
      const { data } = await api.get(`/admin/fingerprint/${encodeURIComponent(hash)}`);
      setActive(data);
    } catch (_) { /* swallow */ }
  };

  const doAction = async (hash, kind) => {
    setActionErr("");
    setActiveBusy(true);
    const reason = window.prompt(
      kind === "block" ? "Reason for blocking?" :
      kind === "unblock" ? "Reason for unblocking?" :
      kind === "trust" ? "Why mark trusted?" :
      kind === "untrust" ? "Why revoke trust?" :
      "Add a note",
      "",
    );
    if (reason === null) { setActiveBusy(false); return; }  // cancelled
    try {
      const body = kind === "note"
        ? { note: reason }
        : { reason: reason || "" };
      await api.post(`/admin/fingerprint/${encodeURIComponent(hash)}/${kind}`, body);
      await reloadActive(hash);
      await load();
    } catch (e) {
      setActionErr(e?.response?.data?.detail || "Action failed.");
    } finally { setActiveBusy(false); }
  };

  const counters = useMemo(() => ([
    { label: "Total devices",       value: summary?.total_fingerprints ?? "—" },
    { label: "Flagged",             value: summary?.flagged ?? "—" },
    { label: "Blocked",             value: summary?.blocked ?? "—" },
    { label: "Trusted",             value: summary?.trusted ?? "—" },
    { label: "Silent blocks (24h)", value: summary?.silent_blocks_served_today ?? "—" },
  ]), [summary]);

  const resolution = summary?.resolution_source_24h || null;

  return (
    <div className="smifs-admin-pane" data-testid="fraud-watch-tab">
      <header className="smifs-admin-pane-head">
        <div>
          <p className="smifs-admin-pane-eyebrow"><Shield size={12} strokeWidth={2.4} /> Fraud Watch · Phase 22</p>
          <h2 className="smifs-admin-pane-title">Device fingerprint monitor</h2>
          <p className="smifs-admin-pane-sub">
            Silent detection of client-data harvesting. Blocked devices receive a benign
            soft-error response and never see a 403.
          </p>
        </div>
        <button
          type="button"
          className="smifs-admin-cta smifs-admin-cta--ghost"
          onClick={load}
          data-testid="fp-refresh"
        >
          <RefreshCw size={14} strokeWidth={2.25} /> Refresh
        </button>
      </header>

      <section className="smifs-admin-kpis" data-testid="fp-counters">
        {counters.map((c) => (
          <article className="smifs-admin-kpi" key={c.label}>
            <p className="smifs-admin-kpi-label">{c.label}</p>
            <p className="smifs-admin-kpi-value">{c.value}</p>
          </article>
        ))}
      </section>

      {summary?.thresholds ? (
        <div className="smifs-admin-meta" data-testid="fp-thresholds" style={{ marginTop: 8 }}>
          <span>Block ≥ {summary.thresholds.block_score}</span>
          <span>Flag ≥ {summary.thresholds.flag_score}</span>
          <span>Half-life {summary.thresholds.half_life_days}d</span>
          <span>Rapid window {summary.thresholds.rapid_window_min}m</span>
          <span>Lifetime cap (no RM) {summary.thresholds.lifetime_client_limit_no_rm}</span>
        </div>
      ) : null}

      {resolution ? (
        <div className="smifs-admin-meta" data-testid="fp-resolution-sources" style={{ marginTop: 4 }}>
          <span style={{ opacity: 0.7 }}>FP source (24h)</span>
          <span><strong>header</strong>: {resolution.header}</span>
          <span style={{ color: resolution.session > 0 ? "#e0a44a" : undefined }}>
            <strong>session-fallback</strong>: {resolution.session}
          </span>
          <span style={{ color: resolution.ip_ua > 0 ? "#ec4f63" : undefined }}>
            <strong>ip+ua fallback</strong>: {resolution.ip_ua}
          </span>
        </div>
      ) : null}

      <nav className="smifs-admin-filters" data-testid="fp-filters">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            className={`smifs-admin-filter ${filter === f.id ? "smifs-admin-filter--on" : ""}`}
            onClick={() => setFilter(f.id)}
            data-testid={`fp-filter-${f.id}`}
          >
            {f.label}
          </button>
        ))}
      </nav>

      <div className="smifs-admin-table" data-testid="fp-table">
        <div className="smifs-admin-tr smifs-admin-tr--head">
          <div>Fingerprint</div>
          <div>Status</div>
          <div>Score</div>
          <div>Clients</div>
          <div>Employees</div>
          <div>IPs</div>
          <div>Last seen</div>
          <div></div>
        </div>
        {loading ? (
          <div className="smifs-admin-empty" data-testid="fp-loading">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="smifs-admin-empty" data-testid="fp-empty">
            No fingerprints in the <strong>{filter}</strong> bucket yet.
          </div>
        ) : (
          rows.map((r) => (
            <div className="smifs-admin-tr" key={r.fingerprint_hash} data-testid={`fp-row-${r.fingerprint_hash}`}>
              <div className="smifs-mono" title={r.fingerprint_hash}>{maskHash(r.fingerprint_hash)}</div>
              <div><StatusPill status={r.status} /></div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <ScoreBar value={r.score} />
                <span className="smifs-mono">{Number(r.score || 0).toFixed(1)}</span>
              </div>
              <div>{r.client_count}</div>
              <div>{r.employee_count}</div>
              <div>{r.ip_count}</div>
              <div>{fmtTs(r.last_seen)}</div>
              <div>
                <button
                  type="button"
                  className="smifs-admin-cta smifs-admin-cta--ghost"
                  onClick={() => openRow(r.fingerprint_hash)}
                  data-testid={`fp-open-${r.fingerprint_hash}`}
                >
                  <Eye size={13} strokeWidth={2.25} /> Inspect
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {active ? (
        <aside className="smifs-admin-drawer" data-testid="fp-drawer">
          <header className="smifs-admin-drawer-head">
            <div>
              <p className="smifs-admin-pane-eyebrow"><Shield size={12} strokeWidth={2.4} /> Device forensics</p>
              <h3 className="smifs-admin-pane-title smifs-mono" style={{ wordBreak: "break-all" }}>
                {active.fingerprint_hash}
              </h3>
            </div>
            <button
              type="button"
              className="smifs-admin-cta smifs-admin-cta--ghost"
              onClick={() => setActive(null)}
              data-testid="fp-drawer-close"
            >
              <X size={14} strokeWidth={2.25} />
            </button>
          </header>

          {actionErr ? (
            <div className="smifs-admin-err" data-testid="fp-action-err">
              <AlertTriangle size={12} /> {actionErr}
            </div>
          ) : null}

          <div className="smifs-admin-drawer-actions">
            {active.blocked && !active.admin_trusted ? (
              <button type="button"
                className="smifs-admin-cta"
                disabled={activeBusy}
                onClick={() => doAction(active.fingerprint_hash, "unblock")}
                data-testid="fp-action-unblock">
                <CheckCircle2 size={13} strokeWidth={2.25} /> Unblock
              </button>
            ) : (
              <button type="button"
                className="smifs-admin-cta smifs-admin-cta--danger"
                disabled={activeBusy || active.admin_trusted}
                onClick={() => doAction(active.fingerprint_hash, "block")}
                data-testid="fp-action-block">
                <Ban size={13} strokeWidth={2.25} /> Block now
              </button>
            )}
            {active.admin_trusted ? (
              <button type="button"
                className="smifs-admin-cta smifs-admin-cta--ghost"
                disabled={activeBusy}
                onClick={() => doAction(active.fingerprint_hash, "untrust")}
                data-testid="fp-action-untrust">
                Revoke trust
              </button>
            ) : (
              <button type="button"
                className="smifs-admin-cta smifs-admin-cta--ghost"
                disabled={activeBusy}
                onClick={() => doAction(active.fingerprint_hash, "trust")}
                data-testid="fp-action-trust">
                <CheckCircle2 size={13} strokeWidth={2.25} /> Mark trusted
              </button>
            )}
            <button type="button"
              className="smifs-admin-cta smifs-admin-cta--ghost"
              disabled={activeBusy}
              onClick={() => doAction(active.fingerprint_hash, "note")}
              data-testid="fp-action-note">
              <MessageSquare size={13} strokeWidth={2.25} /> Add note
            </button>
          </div>

          <section className="smifs-admin-drawer-block">
            <h4>Score breakdown</h4>
            <div className="smifs-admin-meta" data-testid="fp-breakdown">
              {Object.entries(active.score_breakdown || {}).map(([k, v]) => (
                <span key={k}><strong>{k}</strong>: {v}</span>
              ))}
            </div>
            <p className="smifs-admin-meta" style={{ marginTop: 4 }}>
              Final score: <strong>{Number(active.suspicious_score || 0).toFixed(1)}</strong>
              {" · "}First seen: {fmtTs(active.first_seen)}
              {" · "}Last seen: {fmtTs(active.last_seen)}
            </p>
          </section>

          <section className="smifs-admin-drawer-block">
            <h4>Client identities ({(active.client_identities || []).length})</h4>
            <div className="smifs-admin-mini-table" data-testid="fp-clients">
              {(active.client_identities || []).map((c, i) => (
                <div key={`${c.ucc}-${i}`} className="smifs-admin-mini-row">
                  <span className="smifs-mono">{c.ucc}</span>
                  <span>{c.rm_name || "—"}</span>
                  <span>{fmtTs(c.first_at)}</span>
                  <span>×{c.verification_count}</span>
                </div>
              ))}
              {!(active.client_identities || []).length ? <div className="smifs-admin-empty">None.</div> : null}
            </div>
          </section>

          <section className="smifs-admin-drawer-block">
            <h4>Employee identities ({(active.employee_identities || []).length})</h4>
            <div className="smifs-admin-mini-table" data-testid="fp-employees">
              {(active.employee_identities || []).map((e, i) => (
                <div key={`${e.employee_id}-${i}`} className="smifs-admin-mini-row">
                  <span className="smifs-mono">{e.employee_id}</span>
                  <span>—</span>
                  <span>{fmtTs(e.first_at)}</span>
                  <span>×{e.verification_count}</span>
                </div>
              ))}
              {!(active.employee_identities || []).length ? <div className="smifs-admin-empty">None.</div> : null}
            </div>
          </section>

          <section className="smifs-admin-drawer-block">
            <h4>IP variety ({(active.ips_seen || []).length})</h4>
            <div className="smifs-admin-mini-table">
              {(active.ips_seen || []).slice(-8).map((ip, i) => (
                <div key={`${ip.ip}-${i}`} className="smifs-admin-mini-row">
                  <span className="smifs-mono">{ip.ip}</span>
                  <span>{ip.network_prefix}</span>
                  <span>{fmtTs(ip.last_at)}</span>
                  <span>×{ip.count}</span>
                </div>
              ))}
              {!(active.ips_seen || []).length ? <div className="smifs-admin-empty">None.</div> : null}
            </div>
          </section>

          <section className="smifs-admin-drawer-block">
            <h4>Audit trail ({(active.audit || []).length})</h4>
            <div className="smifs-admin-mini-table" data-testid="fp-audit">
              {(active.audit || []).slice(0, 30).map((a, i) => (
                <div key={i} className="smifs-admin-mini-row">
                  <span>{fmtTs(a.ts)}</span>
                  <span><strong>{a.kind}</strong></span>
                  <span>{a.reason || a.note || a.identity_key_masked || "—"}</span>
                  <span className="smifs-mono">{(a.by_token_hash || "").slice(0, 8)}</span>
                </div>
              ))}
              {!(active.audit || []).length ? <div className="smifs-admin-empty">No events yet.</div> : null}
            </div>
          </section>

          {(active.notes || []).length ? (
            <section className="smifs-admin-drawer-block">
              <h4>Notes</h4>
              {(active.notes || []).map((n, i) => (
                <div key={i} className="smifs-admin-note">
                  <p className="smifs-admin-meta">{fmtTs(n.ts)} · {(n.by_token_hash || "").slice(0, 8)}</p>
                  <p>{n.note}</p>
                </div>
              ))}
            </section>
          ) : null}
        </aside>
      ) : null}
    </div>
  );
}
