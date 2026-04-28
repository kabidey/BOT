import { useEffect, useState } from "react";
import IntentBars from "@/components/admin/IntentBars";

export default function InsightsTab({ api }) {
  const [range, setRange] = useState("7d");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.get(`/admin/insights?range=${range}`).then((r) => {
      if (cancelled) return;
      setData(r.data);
      setLoading(false);
    }).catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [api, range]);

  return (
    <div className="smifs-admin-page">
      <header className="smifs-admin-page-head">
        <p className="smifs-admin-eyebrow">Conversation telemetry</p>
        <h2 className="smifs-admin-title">Insights</h2>
        <div className="smifs-admin-filter-row" data-testid="insights-range">
          {["1d", "7d", "30d"].map((r) => (
            <button key={r} type="button"
              className={`smifs-admin-filter ${range === r ? "smifs-admin-filter--on" : ""}`}
              onClick={() => setRange(r)}
              data-testid={`insights-range-${r}`}>{r}</button>
          ))}
        </div>
      </header>

      {loading ? (
        <div className="smifs-admin-loading">Loading insights…</div>
      ) : !data ? (
        <div className="smifs-empty">No data.</div>
      ) : (
        <>
          <div className="smifs-kpi-grid">
            <div className="smifs-kpi"><p className="smifs-kpi-label">Sessions</p><p className="smifs-kpi-value">{data.totals.sessions}</p></div>
            <div className="smifs-kpi"><p className="smifs-kpi-label">Messages</p><p className="smifs-kpi-value">{data.totals.messages}</p></div>
            <div className="smifs-kpi"><p className="smifs-kpi-label">Verified clients</p><p className="smifs-kpi-value">{data.totals.verified_clients}</p></div>
            <div className="smifs-kpi"><p className="smifs-kpi-label">Escalation rate</p><p className="smifs-kpi-value">{(data.escalation_rate * 100).toFixed(1)}%</p></div>
          </div>

          <div className="smifs-admin-2col">
            <section className="smifs-admin-card">
              <header className="smifs-admin-card-head">
                <p className="smifs-admin-eyebrow">Intent distribution</p>
                <h3 className="smifs-admin-card-title">What the router classified</h3>
              </header>
              <IntentBars intents={data.intent_distribution} />
            </section>
            <section className="smifs-admin-card">
              <header className="smifs-admin-card-head">
                <p className="smifs-admin-eyebrow">Lead asset classes</p>
                <h3 className="smifs-admin-card-title">Where prospect demand sits</h3>
              </header>
              {data.lead_asset_classes?.length ? (
                <IntentBars intents={data.lead_asset_classes.map((l) => ({ intent: l.asset_class || "Unspecified", count: l.count }))} emptyLabel="No leads in range" />
              ) : (
                <div className="smifs-empty">No leads in range.</div>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
