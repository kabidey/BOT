/**
 * Phase 31 — BMIA Fund Decisions card.
 *
 * Renders `bmia_fund_decisions_card` — recent multi-agent consensus calls
 * from the BMIA research desk. Each row shows symbol, verdict chip,
 * confidence, headline, and an expandable rationale.
 *
 * Expected `data` shape (from `bmia_client.fund_decisions`):
 *   {
 *     count: 10,
 *     decisions: [{
 *       symbol, decision|final_verdict, confidence (0-1 or 0-100),
 *       headline, rationale, rationale_excerpt?, key_reasons[],
 *       watch_outs[], sector, last_close, rsi14, ts
 *     }, ...]
 *   }
 *
 * Pure CSS/SVG. No charting library.
 */
import { useState } from "react";
import { TrendingUp, TrendingDown, Minus, ChevronDown, ChevronUp, Info } from "lucide-react";

function fmtAsOf(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric" });
  } catch (_) { return iso; }
}

function pctConfidence(c) {
  if (c === null || c === undefined) return null;
  const v = Number(c);
  if (Number.isNaN(v)) return null;
  return v <= 1 ? Math.round(v * 100) : Math.round(v);
}

function verdictStyle(verdict) {
  const v = String(verdict || "").toUpperCase();
  if (v === "BUY" || v === "ACCEPT" || v === "STRONG BUY")
    return { cls: "smifs-bmia-verdict--buy", icon: <TrendingUp size={12} strokeWidth={2.5} /> };
  if (v === "SELL" || v === "STRONG SELL" || v === "REJECT")
    return { cls: "smifs-bmia-verdict--sell", icon: <TrendingDown size={12} strokeWidth={2.5} /> };
  return { cls: "smifs-bmia-verdict--hold", icon: <Minus size={12} strokeWidth={2.5} /> };
}

function DecisionRow({ d, idx }) {
  const [open, setOpen] = useState(false);
  const verdict = d.final_verdict || d.decision || "—";
  const v = verdictStyle(verdict);
  const conf = pctConfidence(d.confidence);
  const rationale = d.rationale || d.rationale_excerpt || "";
  const keyReasons = Array.isArray(d.key_reasons) ? d.key_reasons.slice(0, 4) : [];
  const watchOuts = Array.isArray(d.watch_outs) ? d.watch_outs.slice(0, 3) : [];

  return (
    <li className="smifs-bmia-dec-row" data-testid={`bmia-decision-row-${idx}`}>
      <div className="smifs-bmia-dec-head">
        <div className="smifs-bmia-dec-left">
          <span className="smifs-bmia-dec-sym" data-testid={`bmia-decision-symbol-${idx}`}>
            {d.symbol || "—"}
          </span>
          {d.sector ? <span className="smifs-bmia-dec-sector">{d.sector}</span> : null}
          {d.ts ? <span className="smifs-bmia-dec-ts">{fmtAsOf(d.ts)}</span> : null}
        </div>
        <div className="smifs-bmia-dec-right">
          <span className={`smifs-bmia-verdict ${v.cls}`} data-testid={`bmia-decision-verdict-${idx}`}>
            {v.icon}{String(verdict).toUpperCase()}
          </span>
          {conf !== null ? (
            <span className="smifs-bmia-dec-conf" data-testid={`bmia-decision-conf-${idx}`}>
              {conf}%
            </span>
          ) : null}
        </div>
      </div>
      {d.headline ? (
        <p className="smifs-bmia-dec-headline">{d.headline}</p>
      ) : null}
      {(d.last_close !== undefined || d.rsi14 !== undefined) ? (
        <div className="smifs-bmia-dec-metrics">
          {d.last_close !== undefined ? (
            <span><strong>Close:</strong> ₹{Number(d.last_close).toFixed(2)}</span>
          ) : null}
          {d.rsi14 !== undefined ? (
            <span><strong>RSI14:</strong> {Number(d.rsi14).toFixed(1)}</span>
          ) : null}
        </div>
      ) : null}

      {(rationale || keyReasons.length || watchOuts.length) ? (
        <>
          <button
            type="button"
            className="smifs-bmia-statements-toggle"
            onClick={() => setOpen((o) => !o)}
            data-testid={`bmia-decision-toggle-${idx}`}
          >
            {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            {open ? "Hide rationale" : "Show rationale"}
          </button>
          {open ? (
            <div className="smifs-bmia-dec-expand" data-testid={`bmia-decision-body-${idx}`}>
              {rationale ? <p className="smifs-bmia-dec-rationale">{rationale}</p> : null}
              {keyReasons.length ? (
                <div>
                  <h6>Key reasons</h6>
                  <ul>{keyReasons.map((r, i) => (
                    <li key={i} className="smifs-bmia-chip smifs-bmia-chip--ok">{r}</li>
                  ))}</ul>
                </div>
              ) : null}
              {watchOuts.length ? (
                <div>
                  <h6>Watch-outs</h6>
                  <ul>{watchOuts.map((r, i) => (
                    <li key={i} className="smifs-bmia-chip smifs-bmia-chip--warn">{r}</li>
                  ))}</ul>
                </div>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}
    </li>
  );
}

export default function BmiaFundDecisionsCard({ data }) {
  const d = data || {};
  const decisions = Array.isArray(d.decisions) ? d.decisions : [];
  const count = d.count ?? decisions.length;

  return (
    <div className="smifs-bmia-card" data-testid="bmia-fund-decisions-card">
      <header className="smifs-bmia-card-head">
        <div>
          <p className="smifs-bmia-card-eyebrow">BMIA · Research Desk</p>
          <h3 className="smifs-bmia-card-symbol">Recent Consensus Calls</h3>
        </div>
        <span className="smifs-bmia-asof" data-testid="bmia-decisions-count">
          {count} {count === 1 ? "call" : "calls"}
        </span>
      </header>

      {decisions.length === 0 ? (
        <p className="smifs-bmia-empty" data-testid="bmia-decisions-empty">
          No recent decisions available.
        </p>
      ) : (
        <ul className="smifs-bmia-dec-list">
          {decisions.map((row, i) => <DecisionRow key={i} d={row} idx={i} />)}
        </ul>
      )}

      <footer className="smifs-bmia-card-foot">
        <Info size={11} strokeWidth={2.4} />
        Source: BMIA · multi-agent consensus (research desk output)
      </footer>
    </div>
  );
}
