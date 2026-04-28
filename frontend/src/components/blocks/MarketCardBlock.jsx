import { ArrowUpRight, ArrowDownRight, Activity } from "lucide-react";

export default function MarketCardBlock({ block, msgIdx }) {
  const d = block.data || {};
  const positive = (d.change_pct ?? 0) >= 0;
  const asOf = d.as_of ? new Date(d.as_of) : null;
  const asOfLabel = asOf
    ? asOf.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })
    : "—";

  return (
    <div className="smifs-mkt-card" data-testid={`market-card-${msgIdx}`}>
      <div className="smifs-mkt-head">
        <div>
          <p className="smifs-mkt-eyebrow">{d.kind === "fund" ? "Mutual Fund · NAV" : `Equity · ${d.exchange || "NSE"}`}</p>
          <p className="smifs-mkt-symbol">{d.symbol}</p>
          <p className="smifs-mkt-name">{d.name}</p>
        </div>
        <Activity size={18} strokeWidth={2} className="smifs-mkt-icon" />
      </div>
      <div className="smifs-mkt-price-row">
        <span className="smifs-mkt-currency">{d.currency || "INR"}</span>
        <span className="smifs-mkt-price">{Number(d.last_price).toLocaleString("en-IN", { minimumFractionDigits: 2 })}</span>
        <span className={`smifs-mkt-change ${positive ? "smifs-mkt-change--up" : "smifs-mkt-change--down"}`}>
          {positive ? <ArrowUpRight size={14} strokeWidth={2.5} /> : <ArrowDownRight size={14} strokeWidth={2.5} />}
          {positive ? "+" : ""}{Number(d.change_pct).toFixed(2)}%
        </span>
      </div>
      <p className="smifs-mkt-foot">As of {asOfLabel} · indicative</p>
    </div>
  );
}
