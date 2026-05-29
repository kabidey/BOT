/**
 * Phase 31 — BMIA Fund Portfolio card.
 *
 * Renders `bmia_fund_portfolio_card` — composition of a named model
 * portfolio book (long_term, swing, intraday).
 *
 * Expected `data` shape (from `bmia_client.fund_portfolio`):
 *   Available:  { name, available: true, holdings?: [{symbol, weight?, qty?, ...}], ...everything BMIA returns }
 *   404 case:   { name, available: false, reason, hint }
 *
 * Pure CSS, no charting lib.
 */
import { Info, AlertCircle, Briefcase } from "lucide-react";

const BOOK_LABEL = {
  long_term: "Long-Term Conviction Book",
  swing:     "Swing Trading Book",
  intraday:  "Intraday Book",
};

function fmtPct(n) {
  if (n === null || n === undefined) return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  // Heuristic: if 0<=v<=1 treat as fraction, else as already percent.
  return (v <= 1 ? v * 100 : v).toFixed(2) + "%";
}

function fmtNum(n) {
  if (n === null || n === undefined) return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function HoldingsTable({ holdings }) {
  if (!Array.isArray(holdings) || !holdings.length) return null;
  // Best-effort column discovery (BMIA may evolve schema).
  const has = (k) => holdings.some((h) => h && h[k] !== undefined && h[k] !== null);
  const cols = [
    { key: "symbol", label: "Symbol", fmt: (v) => v ?? "—" },
    has("qty")        && { key: "qty",        label: "Qty",        fmt: fmtNum },
    has("entry_price")&& { key: "entry_price",label: "Entry",      fmt: (v) => `₹${fmtNum(v)}` },
    has("ltp")        && { key: "ltp",        label: "LTP",        fmt: (v) => `₹${fmtNum(v)}` },
    has("weight")     && { key: "weight",     label: "Weight",     fmt: fmtPct },
    has("sector")     && { key: "sector",     label: "Sector",     fmt: (v) => v ?? "—" },
  ].filter(Boolean);
  return (
    <div className="smifs-bmia-table">
      <h5>Holdings</h5>
      <div className="smifs-bmia-table-scroll">
        <table>
          <thead>
            <tr>{cols.map((c) => <th key={c.key}>{c.label}</th>)}</tr>
          </thead>
          <tbody>
            {holdings.map((h, i) => (
              <tr key={i} data-testid={`bmia-portfolio-row-${i}`}>
                {cols.map((c) => <td key={c.key}>{c.fmt(h[c.key])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function BmiaFundPortfolioCard({ data }) {
  const d = data || {};
  const name = d.name || "—";
  const label = BOOK_LABEL[name] || `Portfolio: ${name}`;
  const available = d.available !== false; // default to true if missing
  const holdings = d.holdings || d.positions || d.constituents;

  return (
    <div className="smifs-bmia-card" data-testid="bmia-fund-portfolio-card">
      <header className="smifs-bmia-card-head">
        <div>
          <p className="smifs-bmia-card-eyebrow">BMIA · Model Portfolio</p>
          <h3 className="smifs-bmia-card-symbol">{label}</h3>
        </div>
        <span className={`smifs-bmia-asof ${available ? "" : "smifs-bmia-asof--warn"}`}
              data-testid="bmia-portfolio-status">
          {available ? "Live" : "Not provisioned"}
        </span>
      </header>

      {!available ? (
        <div className="smifs-bmia-portfolio-empty" data-testid="bmia-portfolio-unavailable">
          <AlertCircle size={18} strokeWidth={2.2} className="smifs-bmia-portfolio-empty-icon" />
          <div>
            <p className="smifs-bmia-portfolio-empty-title">
              This book hasn't been published by the BMIA research desk yet.
            </p>
            {d.hint ? <p className="smifs-bmia-portfolio-empty-hint">{d.hint}</p> : null}
            <p className="smifs-bmia-portfolio-empty-suggest">
              Try the {name === "long_term" ? "swing or intraday" : "long-term"} book,
              or check back later.
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="smifs-bmia-portfolio-meta">
            {Array.isArray(holdings) ? (
              <p>
                <Briefcase size={13} strokeWidth={2.2} />
                <span><strong>{holdings.length}</strong> holdings</span>
              </p>
            ) : null}
            {d.as_of ? (
              <p><strong>As of:</strong> {d.as_of}</p>
            ) : null}
            {d.notes ? <p className="smifs-bmia-portfolio-notes">{d.notes}</p> : null}
          </div>
          <HoldingsTable holdings={holdings} />
        </>
      )}

      <footer className="smifs-bmia-card-foot">
        <Info size={11} strokeWidth={2.4} />
        Source: BMIA · model portfolio book ({name})
      </footer>
    </div>
  );
}
