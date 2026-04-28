import { useEffect, useState } from "react";

export default function CostLedgerTab({ api }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    api.get("/admin/cost").then((r) => {
      if (cancelled) return;
      setData(r.data);
      setLoading(false);
    }).catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [api]);

  if (loading) return <div className="smifs-admin-loading">Loading cost ledger…</div>;
  if (!data) return <div className="smifs-empty">Unable to load cost data.</div>;

  const series = data.daily_series || [];
  const max = Math.max(0.01, ...series.map((d) => d.cost_inr));

  return (
    <div className="smifs-admin-page">
      <header className="smifs-admin-page-head">
        <p className="smifs-admin-eyebrow">Hub AI spend · pay-as-you-go</p>
        <h2 className="smifs-admin-title">Cost Ledger</h2>
      </header>

      <div className="smifs-kpi-grid" data-testid="cost-kpis">
        <div className="smifs-kpi smifs-kpi--feature">
          <p className="smifs-kpi-label">Wallet balance</p>
          <p className="smifs-kpi-value smifs-kpi-value--xl">₹{Number(data.balance_inr).toFixed(2)}</p>
          <p className="smifs-kpi-sub">{data.balance_as_of ? `as of ${new Date(data.balance_as_of).toLocaleString("en-IN")}` : "—"}</p>
        </div>
        <div className="smifs-kpi">
          <p className="smifs-kpi-label">Today</p>
          <p className="smifs-kpi-value">₹{Number(data.today_inr).toFixed(2)}</p>
          <p className="smifs-kpi-sub">{data.calls_today} calls</p>
        </div>
        <div className="smifs-kpi">
          <p className="smifs-kpi-label">Last 7 days</p>
          <p className="smifs-kpi-value">₹{Number(data.week_inr).toFixed(2)}</p>
          <p className="smifs-kpi-sub">{data.calls_week} calls</p>
        </div>
        <div className="smifs-kpi">
          <p className="smifs-kpi-label">Last 30 days</p>
          <p className="smifs-kpi-value">₹{Number(data.month_inr).toFixed(2)}</p>
          <p className="smifs-kpi-sub">avg latency {data.avg_latency_ms}ms</p>
        </div>
      </div>

      <section className="smifs-admin-card">
        <header className="smifs-admin-card-head">
          <p className="smifs-admin-eyebrow">7-day spend</p>
          <h3 className="smifs-admin-card-title">Daily burn</h3>
        </header>
        <div className="smifs-spark" data-testid="cost-spark">
          {series.map((d, i) => {
            const h = max > 0 ? Math.max(2, (d.cost_inr / max) * 90) : 2;
            return (
              <div key={i} className="smifs-spark-col" title={`${d.date} · ₹${d.cost_inr.toFixed(2)} · ${d.calls} calls`}>
                <span className="smifs-spark-bar" style={{ height: `${h}%` }} />
                <span className="smifs-spark-label">{d.date.slice(5)}</span>
              </div>
            );
          })}
        </div>
      </section>

      <div className="smifs-admin-2col">
        <section className="smifs-admin-card">
          <header className="smifs-admin-card-head">
            <p className="smifs-admin-eyebrow">By model · 30d</p>
            <h3 className="smifs-admin-card-title">Spend per LLM</h3>
          </header>
          <Table headers={["Model", "Calls", "Cost (₹)"]} rows={data.by_model.map((r) => [r.model, r.calls, Number(r.cost_inr).toFixed(2)])} testId="by-model" />
        </section>
        <section className="smifs-admin-card">
          <header className="smifs-admin-card-head">
            <p className="smifs-admin-eyebrow">By task · 30d</p>
            <h3 className="smifs-admin-card-title">Router vs Chat</h3>
          </header>
          <Table headers={["Task", "Calls", "Cost (₹)"]} rows={data.by_task.map((r) => [r.task, r.calls, Number(r.cost_inr).toFixed(2)])} testId="by-task" />
        </section>
      </div>
    </div>
  );
}

function Table({ headers, rows, testId }) {
  if (!rows?.length) return <div className="smifs-empty">No data yet.</div>;
  return (
    <table className="smifs-table" data-testid={testId}>
      <thead><tr>{headers.map((h) => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>
        {rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}
      </tbody>
    </table>
  );
}
