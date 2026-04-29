import { Lock, ShieldCheck, MapPin, UserCheck } from "lucide-react";

export default function ClientCardBlock({ block, msgIdx }) {
  const d = block.data || {};
  const verified = !!d.verified;
  const segments = d.segments || {};
  const activeSegments = Object.entries(segments).filter(([_, v]) => v && v !== "No").map(([k]) => k.toUpperCase());
  return (
    <div className="smifs-client-card" data-testid={`client-card-${msgIdx}`}>
      <div className="smifs-client-head">
        <div>
          <p className="smifs-client-eyebrow">Mackertich ONE · Client record</p>
          <h3 className="smifs-client-name">{d.first_name || "Valued investor"}</h3>
          {d.ucc && <p className="smifs-client-code">UCC · {d.ucc}</p>}
        </div>
        <span className={`smifs-client-pill ${verified ? "smifs-client-pill--verified" : "smifs-client-pill--locked"}`}>
          {verified ? <ShieldCheck size={12} strokeWidth={2.5} /> : <Lock size={12} strokeWidth={2.5} />}
          {verified ? "Verified" : "Identity not verified"}
        </span>
      </div>
      <dl className="smifs-emp-grid">
        {d.rm_name && (
          <div><dt><UserCheck size={12} strokeWidth={2.25} /> Relationship Manager</dt>
            <dd>{d.rm_name}{d.rm_code ? ` · ${d.rm_code}` : ""}</dd></div>
        )}
        {d.branch_name && (
          <div><dt>Branch</dt><dd>{d.branch_name}</dd></div>
        )}
        {d.risk_profile && (
          <div><dt>Risk profile</dt><dd>{d.risk_profile}</dd></div>
        )}
        {d.status && (
          <div><dt>Status</dt><dd>{d.status}</dd></div>
        )}
        {(d.city || d.state) && (
          <div><dt><MapPin size={12} strokeWidth={2.25} /> Region</dt>
            <dd>{[d.city, d.state].filter(Boolean).join(" · ")}</dd></div>
        )}
        {activeSegments.length > 0 && (
          <div><dt>Active segments</dt><dd>{activeSegments.join(" · ")}</dd></div>
        )}
      </dl>
    </div>
  );
}
