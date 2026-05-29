/**
 * Phase 31 — BMIA Litmus Positions card.
 *
 * Renders `bmia_litmus_positions_card` — currently OPEN paper-trading
 * positions, with live mark-to-market P&L.
 *
 * Expected `data` shape (from `bmia_client.litmus_positions`):
 *   { count, shown, only_open, positions: [{
 *       symbol, qty, entry_price, current_price|ltp, entry_date|ts,
 *       mtm_pnl|pnl_rs, mtm_pnl_pct|pnl_pct, status, ...
 *   }, ...] }
 *
 * Pure HTML table, no chart lib.
 */
import { Info, TrendingUp, TrendingDown } from "lucide-react";

function fmtNum(n, opts = {}) {
  if (n === null || n === undefined) return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, {
    minimumFractionDigits: opts.dec ?? 0,
    maximumFractionDigits: opts.dec ?? 2,
  });
}

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

export default function BmiaLitmusPositionsCard({ data }) {
  const d = data || {};
  const positions = Array.isArray(d.positions) ? d.positions : [];
  const total = d.count ?? positions.length;
  const shown = d.shown ?? positions.length;
  const onlyOpen = d.only_open !== false;

  return (
    <div className="smifs-bmia-card" data-testid="bmia-litmus-positions-card">
      <header className="smifs-bmia-card-head">
        <div>
          <p className="smifs-bmia-card-eyebrow">Litmus · Paper Trading</p>
          <h3 className="smifs-bmia-card-symbol">
            {onlyOpen ? "Open Positions" : "All Positions"}
          </h3>
        </div>
        <span className="smifs-bmia-asof" data-testid="bmia-positions-count">
          {shown < total ? `${shown} of ${total}` : `${total} ${total === 1 ? "position" : "positions"}`}
        </span>
      </header>

      {positions.length === 0 ? (
        <p className="smifs-bmia-empty" data-testid="bmia-positions-empty">
          No open paper-trading positions right now.
        </p>
      ) : (
        <div className="smifs-bmia-table">
          <div className="smifs-bmia-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Qty</th>
                  <th>Entry</th>
                  <th>LTP</th>
                  <th>MTM ₹</th>
                  <th>MTM %</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => {
                  const ltp = p.current_price ?? p.ltp ?? p.last_price;
                  const pnlRs = p.mtm_pnl ?? p.pnl_rs ?? p.unrealised_pnl;
                  const pnlPct = p.mtm_pnl_pct ?? p.pnl_pct ?? p.unrealised_pnl_pct;
                  return (
                    <tr key={i} data-testid={`bmia-position-row-${i}`}>
                      <td><strong>{p.symbol || "—"}</strong></td>
                      <td>{fmtNum(p.qty)}</td>
                      <td>{fmtINR(p.entry_price)}</td>
                      <td>{fmtINR(ltp)}</td>
                      <td className={pnlClass(pnlRs)}>
                        <span className="smifs-bmia-pnl-cell">
                          {pnlRs > 0 ? <TrendingUp size={11} strokeWidth={2.5} />
                            : pnlRs < 0 ? <TrendingDown size={11} strokeWidth={2.5} /> : null}
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
        Source: BMIA Litmus · paper-trading book{onlyOpen ? " · open only" : ""}
      </footer>
    </div>
  );
}
