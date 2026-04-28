import { Lock, ShieldCheck } from "lucide-react";

export default function ClientCardBlock({ block, msgIdx }) {
  const d = block.data || {};
  const verified = !!d.verified;
  return (
    <div className="smifs-client-card" data-testid={`client-card-${msgIdx}`}>
      <div className="smifs-client-head">
        <div>
          <p className="smifs-client-eyebrow">SMIFS client record</p>
          <h3 className="smifs-client-name">{d.name || "Client"}</h3>
          <p className="smifs-client-code">{d.code}</p>
        </div>
        <span className={`smifs-client-pill ${verified ? "smifs-client-pill--verified" : "smifs-client-pill--locked"}`}>
          {verified ? <ShieldCheck size={12} strokeWidth={2.5} /> : <Lock size={12} strokeWidth={2.5} />}
          {verified ? "Verified" : "Identity not verified"}
        </span>
      </div>
      {d.holdings_summary && (
        <div className="smifs-client-summary">
          <p className="smifs-client-summary-label">Portfolio at a glance</p>
          <p className="smifs-client-summary-text">
            {verified ? d.holdings_summary : "Verify your identity to view portfolio details."}
          </p>
        </div>
      )}
    </div>
  );
}
