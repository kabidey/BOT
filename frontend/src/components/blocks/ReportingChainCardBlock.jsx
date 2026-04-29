import { ChevronUp } from "lucide-react";

export default function ReportingChainCardBlock({ block }) {
  const chain = (block.data && block.data.chain) || [];
  if (chain.length === 0) return null;
  return (
    <div className="smifs-chain-card" data-testid="reporting-chain-card">
      <ul className="smifs-chain-list">
        {chain.map((e, i) => (
          <li key={i} className="smifs-chain-row" data-testid={`chain-row-${i}`}>
            <div className="smifs-chain-dot" aria-hidden />
            <div className="smifs-chain-body">
              <p className="smifs-chain-name">
                {i === 0 ? "You · " : ""}{e.name || e.employee_id}
              </p>
              <p className="smifs-chain-sub">
                {e.designation}{e.department ? ` · ${e.department}` : ""}
              </p>
            </div>
            {i < chain.length - 1 && (
              <span className="smifs-chain-link" aria-hidden>
                <ChevronUp size={10} strokeWidth={2.5} />
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
