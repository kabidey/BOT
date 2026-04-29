import { Briefcase, MapPin, Users, UserCheck, Calendar } from "lucide-react";

export default function DirectoryCardBlock({ block }) {
  const d = block.data || {};
  const tenure = d.current_experience || "";
  const joined = d.date_of_joining ? `Joined ${d.date_of_joining}` : "";
  const reports = d.reports_to_name ? `Reports to: ${d.reports_to_name}` : "";
  const teamSize = d.total_team_size || d.direct_reports_count || 0;
  return (
    <div className="smifs-dir-card" data-testid="directory-card">
      <header className="smifs-dir-card-head">
        <div className="smifs-dir-card-avatar" aria-hidden>
          {(d.first_name || d.name || "?").slice(0, 1).toUpperCase()}
        </div>
        <div className="smifs-dir-card-id">
          <h3 className="smifs-dir-card-name" data-testid="directory-card-name">{d.name || "Unknown"}</h3>
          <p className="smifs-dir-card-title">
            {d.designation}{d.department ? ` · ${d.department}` : ""}
          </p>
        </div>
        {d.employment_status && (
          <span className={`smifs-dir-status smifs-dir-status--${(d.employment_status || "").toLowerCase()}`}>
            {d.employment_status}
          </span>
        )}
      </header>
      <dl className="smifs-dir-card-grid">
        {d.location && (
          <div className="smifs-dir-card-row"><dt><MapPin size={11} strokeWidth={2.25} /> Location</dt><dd>{d.location}</dd></div>
        )}
        {reports && (
          <div className="smifs-dir-card-row"><dt><Briefcase size={11} strokeWidth={2.25} /> Manager</dt><dd>{d.reports_to_name}</dd></div>
        )}
        {teamSize > 0 && (
          <div className="smifs-dir-card-row"><dt><Users size={11} strokeWidth={2.25} /> Team size</dt><dd>{teamSize}</dd></div>
        )}
        {joined && (
          <div className="smifs-dir-card-row"><dt><Calendar size={11} strokeWidth={2.25} /> Joined</dt><dd>{d.date_of_joining}{tenure ? ` (${tenure})` : ""}</dd></div>
        )}
        {d.email_display && (
          <div className="smifs-dir-card-row"><dt><UserCheck size={11} strokeWidth={2.25} /> Email</dt><dd>{d.email_display}</dd></div>
        )}
        {d.employee_id && (
          <div className="smifs-dir-card-row"><dt>Employee ID</dt><dd>{d.employee_id}</dd></div>
        )}
      </dl>
    </div>
  );
}
