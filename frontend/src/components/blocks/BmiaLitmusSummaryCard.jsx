/**
 * Phase 31 — BMIA Litmus Summary card.
 *
 * Renders `bmia_litmus_summary_card` — aggregate paper-trading scorecard.
 *
 * Expected `data` shape (from `bmia_client.litmus_summary`):
 *   { open_positions, closed_cycles, total_pnl, avg_pnl, win_rate (0-1),
 *     avg_holding_days, ...maybe more }
 *
 * Pure CSS KPI grid + a CSS-only win-rate gauge.
 */
import { Info, TrendingUp, TrendingDown, Minus } from "lucide-react";

function fmtINR(n) {
  if (n === null || n === undefined) return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  const sign = v < 0 ? "-" : "";
  const abs = Math.abs(v);
  if (abs >= 10000000) return `${sign}₹${(abs / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000)   return `${sign}₹${(abs / 100000).toFixed(2)} L`;
  if (abs >= 1000)     return `${sign}₹${(abs / 1000).toFixed(1)}k`;
  return `${sign}₹${abs.toFixed(0)}`;
}

function fmtPctFraction(n) {
  if (n === null || n === undefined) return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  return ((v <= 1 ? v * 100 : v)).toFixed(1) + "%";
}

function fmtNum(n, dec = 1) {
  if (n === null || n === undefined) return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, {
    minimumFractionDigits: dec,
    maximumFractionDigits: dec,
  });
}

function pnlIcon(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return null;
  if (n > 0) return <TrendingUp size={14} strokeWidth={2.5} />;
  if (n < 0) return <TrendingDown size={14} strokeWidth={2.5} />;
  return <Minus size={14} strokeWidth={2.5} />;
}

function pnlClass(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return "";
  if (n > 0) return "smifs-bmia-pnl--up";
  if (n < 0) return "smifs-bmia-pnl--dn";
  return "smifs-bmia-pnl--flat";
}

function WinRateGauge({ rate }) {
  if (rate === null || rate === undefined) return null;
  const v = Number(rate);
  if (Number.isNaN(v)) return null;
  const pct = v <= 1 ? v * 100 : v;
  const clamped = Math.max(0, Math.min(100, pct));
  // Pie-arc SVG (semicircle, 0-100% mapped to 0-180°).
  const r = 32;
  const cx = 40, cy = 40;
  const angle = (clamped / 100) * Math.PI;
  const x = cx + r * Math.cos(Math.PI - angle);
  const y = cy - r * Math.sin(Math.PI - angle);
  const arcPath = `M ${cx - r} ${cy} A ${r} ${r} 0 ${clamped > 50 ? 1 : 0} 1 ${x.toFixed(2)} ${y.toFixed(2)}`;
  const trackPath = `M ${cx - r} ${cy} A ${r} ${r} 0 1 1 ${cx + r} ${cy}`;
  return (
    <div className="smifs-bmia-gauge" data-testid="bmia-summary-winrate-gauge">
      <svg width="80" height="48" viewBox="0 0 80 48" aria-hidden="true">
        <path d={trackPath} fill="none" stroke="#e2e8f0" strokeWidth="6" strokeLinecap="round" />
        <path d={arcPath} fill="none" stroke="#0c8a4d" strokeWidth="6" strokeLinecap="round" />
      </svg>
      <p className="smifs-bmia-gauge-val">{clamped.toFixed(0)}%</p>
    </div>
  );
}

export default function BmiaLitmusSummaryCard({ data }) {
  const d = data || {};
  const totalPnl = d.total_pnl;
  const avgPnl = d.avg_pnl ?? d.average_pnl;
  const winRate = d.win_rate ?? d.hit_rate;
  const open = d.open_positions ?? d.open_count;
  const closed = d.closed_cycles ?? d.closed_count;
  const avgDays = d.avg_holding_days ?? d.average_holding_days;

  return (
    <div className="smifs-bmia-card" data-testid="bmia-litmus-summary-card">
      <header className="smifs-bmia-card-head">
        <div>
          <p className="smifs-bmia-card-eyebrow">Litmus · Scorecard</p>
          <h3 className="smifs-bmia-card-symbol">Paper-Trading Aggregate</h3>
        </div>
        {winRate !== undefined && winRate !== null ? <WinRateGauge rate={winRate} /> : null}
      </header>

      <div className="smifs-bmia-kpi-grid" data-testid="bmia-summary-kpis">
        <div className="smifs-bmia-kpi">
          <p className="smifs-bmia-kpi-label">Total P&amp;L</p>
          <p className={`smifs-bmia-kpi-val ${pnlClass(totalPnl)}`} data-testid="bmia-summary-total-pnl">
            <span className="smifs-bmia-pnl-cell">{pnlIcon(totalPnl)}{fmtINR(totalPnl)}</span>
          </p>
        </div>
        <div className="smifs-bmia-kpi">
          <p className="smifs-bmia-kpi-label">Avg P&amp;L / trade</p>
          <p className={`smifs-bmia-kpi-val ${pnlClass(avgPnl)}`} data-testid="bmia-summary-avg-pnl">
            {fmtINR(avgPnl)}
          </p>
        </div>
        <div className="smifs-bmia-kpi">
          <p className="smifs-bmia-kpi-label">Win Rate</p>
          <p className="smifs-bmia-kpi-val" data-testid="bmia-summary-win-rate">
            {fmtPctFraction(winRate)}
          </p>
        </div>
        <div className="smifs-bmia-kpi">
          <p className="smifs-bmia-kpi-label">Avg Holding</p>
          <p className="smifs-bmia-kpi-val" data-testid="bmia-summary-avg-days">
            {avgDays !== undefined && avgDays !== null ? `${fmtNum(avgDays, 1)} d` : "—"}
          </p>
        </div>
        <div className="smifs-bmia-kpi">
          <p className="smifs-bmia-kpi-label">Open</p>
          <p className="smifs-bmia-kpi-val" data-testid="bmia-summary-open">
            {open ?? "—"}
          </p>
        </div>
        <div className="smifs-bmia-kpi">
          <p className="smifs-bmia-kpi-label">Closed</p>
          <p className="smifs-bmia-kpi-val" data-testid="bmia-summary-closed">
            {closed ?? "—"}
          </p>
        </div>
      </div>

      <footer className="smifs-bmia-card-foot">
        <Info size={11} strokeWidth={2.4} />
        Source: BMIA Litmus · aggregate paper-trading stats
      </footer>
    </div>
  );
}
