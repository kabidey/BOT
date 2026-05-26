import { useEffect, useState } from "react";
import {
  Wrench, RefreshCw, Power, Activity, CheckCircle2, AlertCircle, Database,
} from "lucide-react";

export default function ToolsTab({ api }) {
  const [registry, setRegistry] = useState(null);
  const [recent, setRecent] = useState([]);
  const [analyzer, setAnalyzer] = useState(null);
  const [bmia, setBmia] = useState(null);
  const [loading, setLoading] = useState(true);
  const [flagBusy, setFlagBusy] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [{ data: reg }, { data: rec }, { data: ana }, bmiaRes] = await Promise.all([
        api.get("/admin/tools/registry"),
        api.get("/admin/tools/recent?limit=30"),
        api.get("/admin/tools/analyzer_stats"),
        api.get("/admin/bmia/summary").catch(() => ({ data: null })),
      ]);
      setRegistry(reg);
      setRecent(rec.items || []);
      setAnalyzer(ana);
      setBmia(bmiaRes.data);
    } catch (e) { /* non-fatal */ }
    setLoading(false);
  };
  useEffect(() => {
    load();
    // Phase 24c — poll BMIA tile every 30s for live counter updates.
    const id = setInterval(async () => {
      try {
        const { data } = await api.get("/admin/bmia/summary");
        setBmia(data);
      } catch (_) { /* swallow */ }
    }, 30000);
    return () => clearInterval(id);
    // eslint-disable-next-line
  }, []);

  const toggleFlag = async () => {
    if (!registry) return;
    setFlagBusy(true);
    try {
      const next = !registry.flag_enabled;
      const { data } = await api.post("/admin/tools/flag", { enabled: next });
      setRegistry((r) => ({ ...r, flag_enabled: data.flag_enabled }));
    } catch (e) { /* non-fatal */ }
    setFlagBusy(false);
  };

  const totalCalls7d = (registry?.tools || []).reduce((s, t) => s + (t.stats?.calls_7d || 0), 0);
  const totalCacheHits = (registry?.tools || []).reduce((s, t) => s + (t.stats?.cache_hits_7d || 0), 0);
  const cacheRate = totalCalls7d > 0 ? Math.round((totalCacheHits / totalCalls7d) * 100) : 0;

  return (
    <div className="smifs-admin-panel" data-testid="tools-tab">
      <div className="smifs-admin-panel-head">
        <h2><Wrench size={18} style={{ verticalAlign: "-3px", marginRight: 8 }} />Phase 20 — Tool Registry</h2>
        {registry && (
          <button onClick={toggleFlag} disabled={flagBusy} className="smifs-admin-btn-primary"
                  data-testid="tools-flag-toggle">
            <Power size={13} />
            {flagBusy ? "…" : (registry.flag_enabled ? "Pipeline ON" : "Pipeline OFF")}
          </button>
        )}
      </div>

      {loading ? (
        <div className="smifs-admin-loading">Loading registry…</div>
      ) : (
        <>
          <section className="smifs-kb-api-panel" data-testid="tools-summary">
            <header className="smifs-kb-api-head">
              <div>
                <h3 className="smifs-kb-api-title">Pipeline summary</h3>
                <p className="smifs-kb-api-sub">
                  {registry?.tools?.length || 0} tools active · {Object.keys(registry?.disabled || {}).length} disabled ·
                  cutover gate <b>45/50</b> on the question matrix
                </p>
              </div>
            </header>
            <div className="smifs-kb-api-counters">
              <div className="smifs-kb-count">
                <span className="smifs-kb-count-label">Calls (7d)</span>
                <span className="smifs-kb-count-value">{totalCalls7d}</span>
              </div>
              <div className="smifs-kb-count">
                <span className="smifs-kb-count-label">Cache hit rate</span>
                <span className="smifs-kb-count-value">{cacheRate}%</span>
              </div>
              <div className="smifs-kb-count">
                <span className="smifs-kb-count-label">Analyzer calls (24h)</span>
                <span className="smifs-kb-count-value">{analyzer?.total || 0}</span>
              </div>
              <div className="smifs-kb-count">
                <span className="smifs-kb-count-label">Analyzer avg latency</span>
                <span className="smifs-kb-count-value">{analyzer?.avg_latency_ms || 0}ms</span>
              </div>
            </div>
          </section>

          <section className="smifs-kb-api-panel" style={{ marginTop: 12 }} data-testid="bmia-tile">
            <header className="smifs-kb-api-head">
              <div>
                <h3 className="smifs-kb-api-title">BMIA · live regulator + market intelligence</h3>
                <p className="smifs-kb-api-sub">
                  {(() => {
                    const eps = (bmia && bmia.endpoints) || {};
                    const totalCalls = Object.values(eps).reduce((a, e) => a + (e.calls || 0), 0);
                    const totalOk = Object.values(eps).reduce((a, e) => a + (e.ok || 0), 0);
                    const totalCache = Object.values(eps).reduce((a, e) => a + (e.cache_hit || 0), 0);
                    const hitRate = totalCalls > 0 ? Math.round((totalCache / totalCalls) * 100) : 0;
                    return `${totalCalls} calls · ${totalOk} ok · cache hit ${hitRate}% · rate cap ${bmia?.rate_per_min || 30}/min`;
                  })()}
                </p>
              </div>
            </header>
            <div className="smifs-kb-api-counters">
              {Object.entries((bmia && bmia.endpoints) || {}).map(([ep, c]) => (
                <div className="smifs-kb-count" key={ep} data-testid={`bmia-ep-${ep}`}>
                  <span className="smifs-kb-count-label" title={ep}>{ep.split("/").pop() || ep}</span>
                  <span className="smifs-kb-count-value">{c.calls || 0}</span>
                </div>
              ))}
              {!Object.keys((bmia && bmia.endpoints) || {}).length ? (
                <div className="smifs-kb-count"><span className="smifs-kb-count-label">No calls yet</span><span className="smifs-kb-count-value">—</span></div>
              ) : null}
            </div>
            {(bmia?.recent_errors || []).length ? (
              <div style={{ marginTop: 10, padding: "8px 12px", background: "rgba(220,38,38,0.06)", borderRadius: 8, border: "1px solid rgba(220,38,38,0.18)" }}>
                <p style={{ margin: 0, fontSize: 12, fontWeight: 600, color: "#b91c1c" }}>Recent errors</p>
                <ul style={{ margin: "4px 0 0", padding: 0, listStyle: "none", fontSize: 12, color: "#7f1d1d" }}>
                  {(bmia.recent_errors || []).slice(0, 5).map((er, i) => (
                    <li key={i}>· {er.endpoint} — {(er.error || "").slice(0, 80)}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>

          <section className="smifs-kb-api-panel" style={{ marginTop: 12 }} data-testid="tools-registry-table">
            <header className="smifs-kb-api-head">
              <div>
                <h3 className="smifs-kb-api-title">Tool registry</h3>
                <p className="smifs-kb-api-sub">Manifest-driven. Each row is one OrgLens endpoint exposed to the LLM.</p>
              </div>
              <button onClick={load} className="smifs-admin-btn-ghost"><RefreshCw size={14}/> Refresh</button>
            </header>
            <div className="smifs-table-wrap">
              <table className="smifs-table">
                <thead>
                  <tr>
                    <th>Tool</th><th>Roles</th><th>Output</th>
                    <th>Calls 7d</th><th>OK</th><th>Cache hits</th>
                    <th>p50</th><th>p95</th>
                  </tr>
                </thead>
                <tbody>
                  {(registry?.tools || []).map((t) => (
                    <tr key={t.name} data-testid={`tools-row-${t.name}`}>
                      <td>
                        <code style={{ fontSize: 11 }}>{t.name}</code>
                        <div className="smifs-admin-dim" style={{ fontSize: 10 }}>{t.description}</div>
                      </td>
                      <td>{t.allowed_roles.join(", ")}</td>
                      <td>{t.output_hint || "—"}</td>
                      <td>{t.stats.calls_7d || 0}</td>
                      <td>{t.stats.ok_7d || 0}</td>
                      <td>{t.stats.cache_hits_7d || 0}</td>
                      <td>{t.stats.p50_ms != null ? `${t.stats.p50_ms}ms` : "—"}</td>
                      <td>{t.stats.p95_ms != null ? `${t.stats.p95_ms}ms` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="smifs-kb-api-panel" style={{ marginTop: 12 }} data-testid="tools-recent">
            <header className="smifs-kb-api-head">
              <div>
                <h3 className="smifs-kb-api-title">Recent tool calls</h3>
                <p className="smifs-kb-api-sub">Last 30 — newest first. Params are redacted.</p>
              </div>
            </header>
            <div className="smifs-table-wrap">
              <table className="smifs-table">
                <thead>
                  <tr><th>When</th><th>Tool</th><th>Role</th><th>Params (redacted)</th><th>Latency</th><th>OK</th></tr>
                </thead>
                <tbody>
                  {recent.map((r, i) => (
                    <tr key={i} data-testid={`tools-recent-row-${i}`}>
                      <td>{(r.created_at || "").slice(11, 19)}</td>
                      <td><code style={{ fontSize: 11 }}>{r.tool_name}</code></td>
                      <td>{r.role_state}</td>
                      <td><code style={{ fontSize: 10 }}>{JSON.stringify(r.params_redacted || {})}</code></td>
                      <td>{r.latency_ms}ms{r.hit_cache ? " (cache)" : ""}</td>
                      <td>
                        {r.ok
                          ? <span className="smifs-admin-pill smifs-admin-pill--ok"><CheckCircle2 size={11}/> ok</span>
                          : <span className="smifs-admin-pill smifs-admin-pill--warn"><AlertCircle size={11}/> {r.error_kind || "fail"}</span>}
                      </td>
                    </tr>
                  ))}
                  {recent.length === 0 && (
                    <tr><td colSpan={6} className="smifs-admin-dim" style={{ padding: 18, textAlign: "center" }}>
                      No tool calls yet. Talk to the bot to populate this table.
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          {analyzer && (
            <section className="smifs-kb-api-panel" style={{ marginTop: 12 }} data-testid="tools-analyzer">
              <header className="smifs-kb-api-head">
                <div>
                  <h3 className="smifs-kb-api-title">
                    <Activity size={13} style={{ verticalAlign: -2, marginRight: 6 }}/>
                    Question Analyzer · last 24h
                  </h3>
                </div>
              </header>
              <div className="smifs-erelay-grid" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
                <div>
                  <b style={{ fontSize: 11, color: "var(--ink-muted)" }}>By entity</b>
                  {Object.entries(analyzer.by_entity || {}).map(([k, v]) => (
                    <div key={k} style={{ fontSize: 12, padding: "3px 0" }}>
                      <code>{k}</code> · {v}
                    </div>
                  ))}
                </div>
                <div>
                  <b style={{ fontSize: 11, color: "var(--ink-muted)" }}>By operation</b>
                  {Object.entries(analyzer.by_operation || {}).map(([k, v]) => (
                    <div key={k} style={{ fontSize: 12, padding: "3px 0" }}>
                      <code>{k}</code> · {v}
                    </div>
                  ))}
                </div>
                <div>
                  <b style={{ fontSize: 11, color: "var(--ink-muted)" }}>By output hint</b>
                  {Object.entries(analyzer.by_output_hint || {}).map(([k, v]) => (
                    <div key={k} style={{ fontSize: 12, padding: "3px 0" }}>
                      <code>{k}</code> · {v}
                    </div>
                  ))}
                </div>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
