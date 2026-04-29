import { Lock, ShieldCheck, MapPin, UserCheck, Mail, Phone, TrendingUp, Calendar } from "lucide-react";

const STATUS_TONES = {
  Active: "smifs-emp-status--ok",
  Closed: "smifs-emp-status--off",
  Suspended: "smifs-emp-status--warn",
  Inactive: "smifs-emp-status--off",
  Dormant: "smifs-emp-status--warn",
};

export default function ClientCardBlock({ block, msgIdx }) {
  const d = block.data || {};
  const verified = !!d.verified;
  const segments = d.segments || {};
  const segChips = Object.entries(segments)
    .filter(([, v]) => v === "Yes" || v === "yes")
    .map(([k]) => k.toUpperCase());
  const statusTone = STATUS_TONES[d.status] || "smifs-emp-status--ok";
  const hasRmContact = !!(d.rm_email || d.rm_mobile);

  return (
    <div className="smifs-client-card" data-testid={`client-card-${msgIdx}`}>
      <div className="smifs-client-head">
        <div>
          <p className="smifs-client-eyebrow">Mackertich ONE · Client record</p>
          <h3 className="smifs-client-name">{d.first_name || d.name || "Valued investor"}</h3>
          <div className="smifs-emp-sub-row">
            {d.ucc && <span className="smifs-emp-eid">UCC · {d.ucc}</span>}
            {d.status && (
              <span className={`smifs-emp-status ${statusTone}`} data-testid={`client-status-${msgIdx}`}>
                {d.status}
              </span>
            )}
            {d.poa && d.poa.toLowerCase() === "yes" && <span className="smifs-emp-status smifs-emp-status--ok">POA</span>}
          </div>
        </div>
        <span className={`smifs-client-pill ${verified ? "smifs-client-pill--verified" : "smifs-client-pill--locked"}`}>
          {verified ? <ShieldCheck size={12} strokeWidth={2.5} /> : <Lock size={12} strokeWidth={2.5} />}
          {verified ? "Verified" : "Identity not verified"}
        </span>
      </div>

      <dl className="smifs-emp-grid">
        {d.rm_name && (
          <div><dt><UserCheck size={12} strokeWidth={2.25} /> Relationship Manager</dt>
            <dd>
              {d.rm_name}{d.rm_code ? ` · ${d.rm_code}` : ""}
              {hasRmContact && (
                <div className="smifs-rm-contact" data-testid={`rm-contact-${msgIdx}`}>
                  {d.rm_email && (
                    <a href={`mailto:${d.rm_email}`} className="smifs-rm-link" data-testid={`rm-mailto-${msgIdx}`}>
                      <Mail size={11} strokeWidth={2.25} /> {d.rm_email}
                    </a>
                  )}
                  {d.rm_mobile && (
                    <a href={`tel:${d.rm_mobile}`} className="smifs-rm-link" data-testid={`rm-tel-${msgIdx}`}>
                      <Phone size={11} strokeWidth={2.25} /> {d.rm_mobile}
                    </a>
                  )}
                </div>
              )}
            </dd></div>
        )}
        {d.branch_name && (
          <div><dt>Branch</dt>
            <dd>{d.branch_name}{d.branch_code ? ` · ${d.branch_code}` : ""}</dd></div>
        )}
        {d.risk_profile && (
          <div><dt><TrendingUp size={12} strokeWidth={2.25} /> Risk profile</dt><dd>{d.risk_profile}</dd></div>
        )}
        {d.active_date && (
          <div><dt><Calendar size={12} strokeWidth={2.25} /> Active since</dt><dd>{d.active_date}</dd></div>
        )}
        {(d.city || d.state) && (
          <div><dt><MapPin size={12} strokeWidth={2.25} /> Region</dt>
            <dd>{[d.city, d.state].filter(Boolean).join(" · ")}</dd></div>
        )}
        {d.occupation && (
          <div><dt>Occupation</dt><dd>{d.occupation}</dd></div>
        )}
        {d.sub_broker_name && (
          <div><dt>Sub-broker</dt><dd>{d.sub_broker_name}</dd></div>
        )}
        {segChips.length > 0 && (
          <div className="smifs-client-segments-row" data-testid={`client-segments-${msgIdx}`}>
            <dt>Active segments</dt>
            <dd className="smifs-seg-chip-row">
              {segChips.map((s) => (
                <span key={s} className="smifs-seg-chip">{s}</span>
              ))}
            </dd>
          </div>
        )}
      </dl>
    </div>
  );
}
