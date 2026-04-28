import { useEffect, useState } from "react";
import { Wallet, Users, MessageSquare, ShieldCheck, Inbox, AlertTriangle } from "lucide-react";
import IntentBars from "@/components/admin/IntentBars";

export default function OverviewTab({ api }) {
  const [data, setData] = useState({ cost: null, insights: null, leadsCount: null });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [c, i, l] = await Promise.all([
          api.get("/admin/cost"),
          api.get("/admin/insights?range=7d"),
          api.get("/admin/leads?status=all&limit=200"),
        ]);
        if (cancelled) return;
        setData({ cost: c.data, insights: i.data, leadsCount: l.data.count });
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [api]);

  if (loading) return <div className="smifs-admin-loading">Loading overview…</div>;
  const { cost, insights, leadsCount } = data;

  const formatINR = (v) => "₹" + Number(v || 0).toFixed(2);
  const series = cost?.daily_series || [];
  const max = Math.max(0.01, ...series.map((d) => d.cost_inr));

  return (
    <div className="smifs-admin-page">
      <header className="smifs-admin-page-head">
        <p className="smifs-admin-eyebrow">Live ops · 7-day window</p>
        <h2 className="smifs-admin-title">Overview</h2>
      </header>

      <div className="smifs-kpi-grid" data-testid="kpi-grid">
        <Kpi icon={<Wallet size={14} />} label="Wallet balance" value={formatINR(cost?.balance_inr)} subtitle={cost?.balance_as_of ? `as of ${new Date(cost.balance_as_of).toLocaleString("en-IN")}` : "—"} />
        <Kpi icon={<Wallet size={14} />} label="Today's cost" value={formatINR(cost?.today_inr)} subtitle={`${cost?.calls_today || 0} calls · avg ${cost?.avg_latency_ms || 0}ms`} />
        <Kpi icon={<MessageSquare size={14} />} label="Sessions (7d)" value={insights?.totals?.sessions ?? "—"} subtitle={`${insights?.totals?.messages ?? 0} messages`} />
        <Kpi icon={<ShieldCheck size={14} />} label="Verified clients (7d)" value={insights?.totals?.verified_clients ?? "—"} subtitle="completed identity verification" />
        <Kpi icon={<Inbox size={14} />} label="Total leads" value={leadsCount ?? "—"} subtitle="all-time" />
        <Kpi icon={<AlertTriangle size={14} />} label="Escalation rate" value={`${((insights?.escalation_rate || 0) * 100).toFixed(1)}%`} subtitle="of assistant turns" />
      </div>

      <section className="smifs-admin-card">
        <header className="smifs-admin-card-head">
          <p className="smifs-admin-eyebrow">Cost burn · last 7 days</p>
          <h3 className="smifs-admin-card-title">Daily Hub AI spend (INR)</h3>
        </header>
        <div className="smifs-spark" data-testid="overview-spark">
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

      <section className="smifs-admin-card">
        <header className="smifs-admin-card-head">
          <p className="smifs-admin-eyebrow">Top intents · 7 days</p>
          <h3 className="smifs-admin-card-title">What clients are asking</h3>
        </header>
        <IntentBars intents={insights?.intent_distribution || []} />
      </section>
    </div>
  );
}

function Kpi({ icon, label, value, subtitle }) {
  return (
    <div className="smifs-kpi" data-testid={`kpi-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`}>
      <div className="smifs-kpi-head">
        <span className="smifs-kpi-icon">{icon}</span>
        <span className="smifs-kpi-label">{label}</span>
      </div>
      <p className="smifs-kpi-value">{value}</p>
      {subtitle && <p className="smifs-kpi-sub">{subtitle}</p>}
    </div>
  );
}
