import { Lock, ShieldCheck, MapPin, Briefcase } from "lucide-react";

export default function EmployeeCardBlock({ block, msgIdx }) {
  const d = block.data || {};
  const verified = !!d.verified;
  return (
    <div className="smifs-emp-card" data-testid={`employee-card-${msgIdx}`}>
      <div className="smifs-emp-head">
        <div>
          <p className="smifs-emp-eyebrow">SMIFS Ltd · Employee record</p>
          <h3 className="smifs-emp-name">{d.name || d.first_name || "Colleague"}</h3>
          {d.employee_id && <p className="smifs-emp-eid">{d.employee_id}</p>}
        </div>
        <span className={`smifs-emp-pill ${verified ? "smifs-emp-pill--verified" : "smifs-emp-pill--locked"}`}>
          {verified ? <ShieldCheck size={12} strokeWidth={2.5} /> : <Lock size={12} strokeWidth={2.5} />}
          {verified ? "Verified · EMP" : "Identity not verified"}
        </span>
      </div>
      <dl className="smifs-emp-grid">
        {d.designation && (
          <div><dt><Briefcase size={12} strokeWidth={2.25} /> Designation</dt><dd>{d.designation}</dd></div>
        )}
        {d.department && (
          <div><dt>Department</dt><dd>{d.department}</dd></div>
        )}
        {d.location && (
          <div><dt><MapPin size={12} strokeWidth={2.25} /> Location</dt><dd>{d.location}</dd></div>
        )}
        {d.business_unit && (
          <div><dt>Business unit</dt><dd>{d.business_unit}</dd></div>
        )}
        {d.employment_status && (
          <div><dt>Status</dt><dd>{d.employment_status}</dd></div>
        )}
        {d.reports_to_name && (
          <div><dt>Reports to</dt><dd>{d.reports_to_name}</dd></div>
        )}
      </dl>
    </div>
  );
}
