/**
 * Phase 24c.4 — BMIA fundamentals card.
 *
 * Renders the `bmia_fundamentals_card` block emitted by the LLM after a
 * `bmia_fundamentals_lookup` tool call.
 *
 * Expected `data` shape (matches `bmia_client.fundamentals(slice='profile')`):
 *   {
 *     symbol, about, last_fetched, pros[], cons[],
 *     profit_loss_3y?: { periods, rows: { "Sales +": [...], "EPS in Rs": [...], ... } },
 *     quarterly_last_4?: { ... },
 *     profit_loss?, balance_sheet?, cash_flow?, ratios?  // full slice
 *   }
 *
 * No charting library — sparklines are pure SVG.
 */
import { useMemo, useState } from "react";
import { TrendingUp, TrendingDown, ChevronDown, ChevronUp, Info, ExternalLink } from "lucide-react";

function fmtAsOf(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  } catch (_) { return iso; }
}

function fmtNumberCr(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  const v = Number(n);
  if (Math.abs(v) >= 100000) return `₹${(v / 100000).toFixed(2)} L Cr`;
  if (Math.abs(v) >= 1000) return `₹${(v / 1000).toFixed(2)}k Cr`;
  return `₹${v.toFixed(0)} Cr`;
}

function Sparkline({ values, label, accent = "#0c8a4d" }) {
  if (!Array.isArray(values) || values.length < 2) return null;
  const w = 96, h = 28, pad = 2;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = (w - pad * 2) / (values.length - 1);
  const points = values.map((v, i) => {
    const x = pad + i * step;
    const y = h - pad - ((v - min) / span) * (h - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const last = values[values.length - 1];
  const first = values[0];
  const dir = last > first ? "up" : last < first ? "down" : "flat";
  return (
    <div className="smifs-bmia-spark" data-testid={`bmia-spark-${label}`}>
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
        <polyline points={points} fill="none" stroke={accent} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
      </svg>
      <p className="smifs-bmia-spark-label">
        {label}
        {dir === "up" ? <TrendingUp size={11} strokeWidth={2.5} color="#0c8a4d" />
          : dir === "down" ? <TrendingDown size={11} strokeWidth={2.5} color="#b9374a" /> : null}
      </p>
    </div>
  );
}

function StatementTable({ title, table }) {
  if (!table || !table.periods || !table.rows) return null;
  const rowKeys = Object.keys(table.rows);
  if (!rowKeys.length) return null;
  return (
    <div className="smifs-bmia-table">
      <h5>{title}</h5>
      <div className="smifs-bmia-table-scroll">
        <table>
          <thead>
            <tr><th>Line item</th>{table.periods.map((p) => <th key={p}>{p}</th>)}</tr>
          </thead>
          <tbody>
            {rowKeys.map((k) => (
              <tr key={k}>
                <td>{k}</td>
                {(table.rows[k] || []).map((v, i) => <td key={i}>{v ?? "—"}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function BmiaFundamentalsCard({ data }) {
  const [aboutOpen, setAboutOpen] = useState(false);
  const [statementsOpen, setStatementsOpen] = useState(false);
  const d = data || {};
  const symbol = d.symbol || "—";
  const about = d.about || "";
  const aboutShort = about.length > 280 ? about.slice(0, 280) + "…" : about;
  const pros = (d.pros || []).slice(0, 3);
  const cons = (d.cons || []).slice(0, 3);

  // Sparklines: prefer profit_loss_3y if present, fallback to profit_loss.
  const epsSeries = useMemo(() => {
    const pl = d.profit_loss_3y || d.profit_loss || {};
    const rows = pl.rows || {};
    const arr = rows["EPS in Rs"];
    return Array.isArray(arr) ? arr.slice(-5).map(Number) : [];
  }, [d]);
  const quarterlySalesSeries = useMemo(() => {
    const q = d.quarterly_last_4 || {};
    const arr = (q.rows || {})["Sales +"];
    return Array.isArray(arr) ? arr.slice(-4).map(Number) : [];
  }, [d]);

  return (
    <div className="smifs-bmia-card" data-testid="bmia-fundamentals-card">
      <header className="smifs-bmia-card-head">
        <div>
          <p className="smifs-bmia-card-eyebrow">NSE · Fundamentals</p>
          <h3 className="smifs-bmia-card-symbol">{symbol}</h3>
        </div>
        {d.last_fetched ? (
          <span className="smifs-bmia-asof" title={d.last_fetched}>
            As of {fmtAsOf(d.last_fetched)}
          </span>
        ) : null}
      </header>

      {about ? (
        <p className="smifs-bmia-about" data-testid="bmia-about">
          {aboutOpen ? about : aboutShort}
          {about.length > 280 ? (
            <button type="button" className="smifs-bmia-toggle"
                    onClick={() => setAboutOpen((v) => !v)}>
              {aboutOpen ? "Less" : "More"}
            </button>
          ) : null}
        </p>
      ) : null}

      {(pros.length || cons.length) ? (
        <div className="smifs-bmia-prcons">
          {pros.length ? (
            <div className="smifs-bmia-prcons-col">
              <h6>Pros</h6>
              <ul>{pros.map((p, i) => <li key={i} className="smifs-bmia-chip smifs-bmia-chip--ok">{p}</li>)}</ul>
            </div>
          ) : null}
          {cons.length ? (
            <div className="smifs-bmia-prcons-col">
              <h6>Cons</h6>
              <ul>{cons.map((c, i) => <li key={i} className="smifs-bmia-chip smifs-bmia-chip--warn">{c}</li>)}</ul>
            </div>
          ) : null}
        </div>
      ) : null}

      {(epsSeries.length || quarterlySalesSeries.length) ? (
        <div className="smifs-bmia-sparks">
          {epsSeries.length >= 2 ? <Sparkline values={epsSeries} label="EPS · last 3-5 yrs" /> : null}
          {quarterlySalesSeries.length >= 2 ? <Sparkline values={quarterlySalesSeries} label="Sales · last 4 Q" /> : null}
        </div>
      ) : null}

      {(d.profit_loss || d.balance_sheet || d.cash_flow || d.quarterly || d.ratios) ? (
        <div className="smifs-bmia-statements">
          <button type="button" className="smifs-bmia-statements-toggle"
                  onClick={() => setStatementsOpen((v) => !v)}
                  data-testid="bmia-statements-toggle">
            {statementsOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            {statementsOpen ? "Hide full statements" : "View full statements"}
          </button>
          {statementsOpen ? (
            <div className="smifs-bmia-statements-body">
              <StatementTable title="Profit & Loss"  table={d.profit_loss} />
              <StatementTable title="Quarterly"      table={d.quarterly} />
              <StatementTable title="Balance Sheet"  table={d.balance_sheet} />
              <StatementTable title="Cash Flow"      table={d.cash_flow} />
              <StatementTable title="Ratios"         table={d.ratios} />
            </div>
          ) : null}
        </div>
      ) : null}

      <footer className="smifs-bmia-card-foot">
        <Info size={11} strokeWidth={2.4} />
        Source: BMIA (Bharat Market Intelligence Aggregator)
        {d.last_fetched ? ` · refreshed ${fmtAsOf(d.last_fetched)}` : ""}
      </footer>
    </div>
  );
}
