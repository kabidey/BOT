import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Filter, Loader2, RefreshCw, Sparkles, TrendingDown } from "lucide-react";

const RANGES = [
  { id: "24h", label: "24 h" },
  { id: "7d", label: "7 d" },
  { id: "30d", label: "30 d" },
];

const ROLES = [
  { id: "all", label: "All roles" },
  { id: "client", label: "Clients" },
  { id: "employee", label: "Employees" },
  { id: "visitor", label: "Visitors" },
];

export default function KnowledgeGapsTab({ api }) {
  const [range, setRange] = useState("7d");
  const [role, setRole] = useState("all");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const { data: d } = await api.get("/admin/knowledge_gaps", { params: { range, role, limit: 100 } });
      setData(d);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [range, role]);

  const maxAssetCount = useMemo(() => {
    const xs = (data?.by_asset_class || []).map((r) => r.count);
    return xs.length ? Math.max(...xs) : 1;
  }, [data]);

  const maxTopCount = useMemo(() => {
    const xs = (data?.top_questions || []).slice(0, 20).map((r) => r.count);
    return xs.length ? Math.max(...xs) : 1;
  }, [data]);

  const markResolved = async (question_normalized, next) => {
    try {
      await api.post("/admin/knowledge_gaps/resolve", { question_normalized, resolved: next });
      // Optimistic update
      setData((prev) => prev ? {
        ...prev,
        top_questions: prev.top_questions.map((r) =>
          r.question_normalized === question_normalized ? { ...r, resolved: next } : r
        ),
        totals: {
          ...prev.totals,
          resolved_questions: prev.top_questions.filter((r) =>
            r.question_normalized === question_normalized ? next : r.resolved
          ).length,
        },
      } : prev);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    }
  };

  return (
    <div className="smifs-admin-page" data-testid="knowledge-gaps-tab">
      <header className="smifs-admin-page-head">
        <div>
          <p className="smifs-admin-eyebrow">Ops · what couldn't we answer?</p>
          <h2 className="smifs-admin-title">Knowledge Gaps</h2>
          <p className="smifs-admin-subtitle">
            Aggregated from hallucination guardrails and Wealth-Manager fallbacks.
            Marking a question resolved hides it from the default view once the KB
            has been updated.
          </p>
        </div>
        <div className="smifs-admin-toolbar">
          <div className="smifs-chip-row" role="tablist" aria-label="Time range">
            {RANGES.map((r) => (
              <button
                key={r.id}
                type="button"
                className={`smifs-chip ${range === r.id ? "smifs-chip--on" : ""}`}
                onClick={() => setRange(r.id)}
                data-testid={`gaps-range-${r.id}`}
              >
                {r.label}
              </button>
            ))}
          </div>
          <div className="smifs-chip-row" role="tablist" aria-label="Role">
            {ROLES.map((r) => (
              <button
                key={r.id}
                type="button"
                className={`smifs-chip ${role === r.id ? "smifs-chip--on" : ""}`}
                onClick={() => setRole(r.id)}
                data-testid={`gaps-role-${r.id}`}
              >
                <Filter size={10} strokeWidth={2.25} /> {r.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="smifs-admin-btn-secondary"
            onClick={load}
            disabled={loading}
            data-testid="gaps-refresh"
          >
            <RefreshCw size={13} strokeWidth={2.25} /> Refresh
          </button>
        </div>
      </header>

      {error && (
        <div className="smifs-admin-err" data-testid="gaps-error">
          <AlertTriangle size={12} /> {error}
        </div>
      )}

      {/* KPI tiles */}
      <div className="smifs-kpi-row" data-testid="gaps-kpis">
        <div className="smifs-kpi">
          <span className="smifs-kpi-label">Hallucination events</span>
          <span className="smifs-kpi-value">{data?.totals?.hallucination_events ?? "—"}</span>
        </div>
        <div className="smifs-kpi">
          <span className="smifs-kpi-label">WM fallbacks</span>
          <span className="smifs-kpi-value">{data?.totals?.wm_fallbacks ?? "—"}</span>
        </div>
        <div className="smifs-kpi">
          <span className="smifs-kpi-label">Unique questions</span>
          <span className="smifs-kpi-value">{data?.totals?.unique_questions ?? "—"}</span>
        </div>
        <div className="smifs-kpi">
          <span className="smifs-kpi-label">Resolved</span>
          <span className="smifs-kpi-value">{data?.totals?.resolved_questions ?? 0}</span>
        </div>
        <div className="smifs-kpi">
          <span className="smifs-kpi-label">Top asset class</span>
          <span className="smifs-kpi-value">{data?.by_asset_class?.[0]?.asset_class || "—"}</span>
        </div>
      </div>

      {/* Phase 16 — per-role counter strip (visible regardless of the filter
          above so content team can see the role split at a glance). */}
      {data?.by_role && (
        <section className="smifs-admin-card" data-testid="gaps-by-role">
          <header>
            <h3 className="smifs-admin-h3"><Filter size={14} strokeWidth={2.25} /> Gap volume by role</h3>
          </header>
          <ul className="smifs-bars">
            {["client", "employee", "visitor"].map((r) => {
              const v = data.by_role[r] || { hallucination_events: 0, wm_fallbacks: 0, unique_questions: 0 };
              const total = (v.hallucination_events || 0) + (v.wm_fallbacks || 0);
              return (
                <li key={r} className="smifs-bar-row" data-testid={`gap-role-${r}`}>
                  <span className="smifs-bar-label" style={{ textTransform: "capitalize" }}>{r}</span>
                  <div className="smifs-bar-track">
                    <div
                      className="smifs-bar-fill"
                      style={{ width: `${Math.max(4, Math.min(100, total * 6))}%` }}
                    />
                  </div>
                  <span className="smifs-bar-count" data-testid={`gap-role-count-${r}`}>
                    {v.hallucination_events || 0} hallu · {v.wm_fallbacks || 0} WM · {v.unique_questions || 0} unique
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {/* By asset class */}
      <section className="smifs-admin-card" data-testid="gaps-by-asset">
        <header>
          <h3 className="smifs-admin-h3"><TrendingDown size={14} strokeWidth={2.25} /> Gap volume by asset class</h3>
        </header>
        {loading ? (
          <div className="smifs-admin-loading"><Loader2 size={14} className="spin" /> Loading…</div>
        ) : (data?.by_asset_class || []).length === 0 ? (
          <div className="smifs-admin-empty">No gaps recorded in this range.</div>
        ) : (
          <ul className="smifs-bars">
            {(data?.by_asset_class || []).map((row) => (
              <li key={row.asset_class} className="smifs-bar-row" data-testid={`gap-asset-${row.asset_class}`}>
                <span className="smifs-bar-label">{row.asset_class}</span>
                <div className="smifs-bar-track">
                  <div
                    className="smifs-bar-fill"
                    style={{ width: `${Math.max(6, (row.count / maxAssetCount) * 100)}%` }}
                  />
                </div>
                <span className="smifs-bar-count">{row.count}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Top questions table */}
      <section className="smifs-admin-card" data-testid="gaps-top-questions">
        <header>
          <h3 className="smifs-admin-h3"><Sparkles size={14} strokeWidth={2.25} /> Top 20 unanswered questions</h3>
        </header>
        {loading ? (
          <div className="smifs-admin-loading"><Loader2 size={14} className="spin" /> Loading…</div>
        ) : (data?.top_questions || []).length === 0 ? (
          <div className="smifs-admin-empty">Nothing to show for this range.</div>
        ) : (
          <div className="smifs-table-wrap">
            <table className="smifs-table" data-testid="gaps-table">
              <thead>
                <tr>
                  <th>Question</th>
                  <th>Asset</th>
                  <th>Roles</th>
                  <th>Count</th>
                  <th>Last seen</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {(data.top_questions || []).slice(0, 20).map((r) => (
                  <tr
                    key={r.question_normalized}
                    data-testid={`gap-row-${r.question_normalized.slice(0, 24).replace(/\s+/g, "-")}`}
                    className={r.resolved ? "smifs-table-row--resolved" : ""}
                  >
                    <td className="smifs-table-cell-2">
                      <div className="smifs-gap-q">{r.sample_question}</div>
                      <div className="smifs-gap-sub">
                        {r.sources?.hallucination_events ? `hallucination ·${r.sources.hallucination_events}` : ""}
                        {r.sources?.wm_fallbacks ? ` · wm_fallback ·${r.sources.wm_fallbacks}` : ""}
                      </div>
                    </td>
                    <td><span className="smifs-status-pill">{r.asset_class}</span></td>
                    <td className="smifs-table-cell-sub">{(r.roles || []).join(", ") || "—"}</td>
                    <td className="smifs-mono-cell">
                      <div className="smifs-bar-row smifs-bar-row--inline">
                        <div className="smifs-bar-track smifs-bar-track--sm">
                          <div className="smifs-bar-fill" style={{ width: `${(r.count / maxTopCount) * 100}%` }} />
                        </div>
                        <span>{r.count}</span>
                      </div>
                    </td>
                    <td className="smifs-mono-cell">{(r.last_seen || "").slice(0, 16).replace("T", " ")}</td>
                    <td>
                      {r.resolved
                        ? <span className="smifs-status-pill smifs-status-pill--qualified"><CheckCircle2 size={10} /> resolved</span>
                        : <span className="smifs-status-pill smifs-status-pill--new">open</span>}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="smifs-admin-btn-ghost"
                        onClick={() => markResolved(r.question_normalized, !r.resolved)}
                        data-testid={`gap-resolve-${r.question_normalized.slice(0, 24).replace(/\s+/g, "-")}`}
                      >
                        {r.resolved ? "Mark open" : "Mark resolved"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
