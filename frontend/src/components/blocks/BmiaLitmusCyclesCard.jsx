/**
 * Phase 31 — BMIA Litmus Cycles card.
 *
 * Renders `bmia_litmus_cycles_card` — closed paper-trading cycles with
 * realised P&L per trade.
 *
 * Expected `data` shape (from `bmia_client.litmus_cycles`):
 *   { count, cycles: [{
 *       symbol, qty?, entry_price, exit_price, entry_date?|entry_ts?,
 *       exit_date?|exit_ts?, holding_days,
 *       pnl_rs|realised_pnl, pnl_pct|realised_pnl_pct,
 *       entry_decision?, exit_decision?, ...
 *   }, ...] }
 */
import { Info, TrendingUp, TrendingDown } from "lucide-react";

function fmtINR(n) {
  if (n === null || n === undefined) return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  return `₹${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtPct(n) {
  if (n === null || n === undefined) return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  return (v <= 1 && v >= -1 ? v * 100 : v).toFixed(2) + "%";
}

function pnlClass(v) {
  if (v === null || v === undefined) return "";
  const n = Number(v);
  if (Number.isNaN(n)) return "";
  if (n > 0) return "smifs-bmia-pnl--up";
  if (n < 0) return "smifs-bmia-pnl--dn";
  return "smifs-bmia-pnl--flat";
}

function fmtDateShort(s) {
  if (!s) return "—";
  try {
    const d = new Date(s);
    if (Number.isNaN(d.getTime())) return s;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch (_) { return s; }
}

export default function BmiaLitmusCyclesCard({ data }) {
  const d = data || {};
  const cycles = Array.isArray(d.cycles) ? d.cycles : [];
  const count = d.count ?? cycles.length;

  // Quick win-rate banner from the rendered slice.
  const wins = cycles.filter((c) => {
    const v = Number(c.pnl_rs ?? c.realised_pnl ?? c.pnl);
    return !Number.isNaN(v) && v > 0;
  }).length;
  const winPct = cycles.length ? Math.round((wins / cycles.length) * 100) : null;

  return (
    <div className="smifs-bmia-card" data-testid="bmia-litmus-cycles-card">
      <header className="smifs-bmia-card-head">
        <div>
          <p className="smifs-bmia-card-eyebrow">Litmus · Closed Trades</p>
          <h3 className="smifs-bmia-card-symbol">Recent Cycles</h3>
        </div>
        <span className="smifs-bmia-asof" data-testid="bmia-cycles-count">
          {count} {count === 1 ? "trade" : "trades"}
          {winPct !== null ? ` · ${winPct}% wins (shown)` : ""}
        </span>
      </header>

      {cycles.length === 0 ? (
        <p className="smifs-bmia-empty" data-testid="bmia-cycles-empty">
          No closed paper-trading cycles yet.
        </p>
      ) : (
        <div className="smifs-bmia-table">
          <div className="smifs-bmia-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Entry → Exit</th>
                  <th>Days</th>
                  <th>P&amp;L ₹</th>
                  <th>P&amp;L %</th>
                </tr>
              </thead>
              <tbody>
                {cycles.map((c, i) => {
                  const pnlRs = c.pnl_rs ?? c.realised_pnl ?? c.pnl;
                  const pnlPct = c.pnl_pct ?? c.realised_pnl_pct ?? c.return_pct;
                  const eDate = fmtDateShort(c.entry_date || c.entry_ts);
                  const xDate = fmtDateShort(c.exit_date || c.exit_ts);
                  return (
                    <tr key={i} data-testid={`bmia-cycle-row-${i}`}>
                      <td><strong>{c.symbol || "—"}</strong></td>
                      <td>
                        <span className="smifs-bmia-cyc-range">
                          {fmtINR(c.entry_price)} → {fmtINR(c.exit_price)}
                        </span>
                        <span className="smifs-bmia-cyc-dates">{eDate} → {xDate}</span>
                      </td>
                      <td>{c.holding_days ?? "—"}</td>
                      <td className={pnlClass(pnlRs)}>
                        <span className="smifs-bmia-pnl-cell">
                          {Number(pnlRs) > 0 ? <TrendingUp size={11} strokeWidth={2.5} />
                            : Number(pnlRs) < 0 ? <TrendingDown size={11} strokeWidth={2.5} /> : null}
                          {fmtINR(pnlRs)}
                        </span>
                      </td>
                      <td className={pnlClass(pnlPct)}>{fmtPct(pnlPct)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <footer className="smifs-bmia-card-foot">
        <Info size={11} strokeWidth={2.4} />
        Source: BMIA Litmus · realised paper-trading P&amp;L
      </footer>
    </div>
  );
}
