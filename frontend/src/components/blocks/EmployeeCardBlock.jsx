import { Lock, ShieldCheck, MapPin, Briefcase, Calendar, Users, UserCheck, AlertCircle } from "lucide-react";

const STATUS_TONES = {
  Active: "smifs-emp-status--ok",
  Confirmed: "smifs-emp-status--ok",
  "On Notice": "smifs-emp-status--warn",
  Probation: "smifs-emp-status--warn",
  Inactive: "smifs-emp-status--off",
  Resigned: "smifs-emp-status--off",
  Absconding: "smifs-emp-status--off",
};

export default function EmployeeCardBlock({ block, msgIdx }) {
  const d = block.data || {};
  const verified = !!d.verified;
  const statusTone = STATUS_TONES[d.employment_status] || "smifs-emp-status--ok";
  const showRtLine = !!d.reports_to_name || (d.total_team_size > 0) || (d.direct_reports_count > 0);

  return (
    <div className="smifs-emp-card" data-testid={`employee-card-${msgIdx}`}>
      <div className="smifs-emp-head">
        <div>
          <p className="smifs-emp-eyebrow">SMIFS Ltd · Employee record</p>
          <h3 className="smifs-emp-name">{d.name || d.first_name || "Colleague"}</h3>
          <div className="smifs-emp-sub-row">
            {d.employee_id && <span className="smifs-emp-eid">{d.employee_id}</span>}
            {d.employment_status && (
              <span className={`smifs-emp-status ${statusTone}`} data-testid={`emp-status-${msgIdx}`}>
                {d.employment_status}
              </span>
            )}
            {d.on_notice && <span className="smifs-emp-status smifs-emp-status--warn">On Notice</span>}
            {d.is_absconding && <span className="smifs-emp-status smifs-emp-status--off"><AlertCircle size={10} /> Absconding</span>}
          </div>
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
          <div><dt>Department</dt><dd>{d.department}{d.business_unit ? ` · ${d.business_unit}` : ""}</dd></div>
        )}
        {d.location && (
          <div><dt><MapPin size={12} strokeWidth={2.25} /> Location</dt>
            <dd>{d.location}{d.location_type ? ` · ${d.location_type}` : ""}</dd></div>
        )}
        {(d.date_of_joining || d.current_experience) && (
          <div><dt><Calendar size={12} strokeWidth={2.25} /> Joined</dt>
            <dd>
              {d.date_of_joining || "—"}
              {d.current_experience ? <span className="smifs-emp-tenure"> · {d.current_experience}</span> : null}
            </dd></div>
        )}
        {d.employee_type && (
          <div><dt>Type</dt><dd>{d.employee_type}</dd></div>
        )}
        {showRtLine && (
          <div><dt><UserCheck size={12} strokeWidth={2.25} /> Reports to</dt>
            <dd>
              {d.reports_to_name || "—"}
              {(d.total_team_size > 0 || d.direct_reports_count > 0) && (
                <span className="smifs-emp-tenure">
                  {" · "}
                  <Users size={10} style={{ display: "inline", verticalAlign: "-1px" }} />{" "}
                  {d.direct_reports_count > 0 ? `${d.direct_reports_count} direct` : null}
                  {d.direct_reports_count > 0 && d.total_team_size > 0 ? " / " : null}
                  {d.total_team_size > 0 ? `${d.total_team_size} team` : null}
                </span>
              )}
            </dd></div>
        )}
        {d.hrbp_name && (
          <div><dt>HRBP</dt><dd>{d.hrbp_name}</dd></div>
        )}
      </dl>
    </div>
  );
}
