import { Users } from "lucide-react";

export default function DirectoryListBlock({ block }) {
  const d = block.data || {};
  const items = d.items || [];
  const fields = d.summary_fields || ["name", "designation", "department", "location"];
  return (
    <div className="smifs-dir-list" data-testid="directory-list">
      <header className="smifs-dir-list-head">
        <Users size={13} strokeWidth={2.25} />
        <h4 className="smifs-dir-list-title">{d.title || "Employees"}</h4>
        {typeof d.total === "number" && (
          <span className="smifs-dir-list-count">{items.length} of {d.total}</span>
        )}
      </header>
      {items.length === 0 ? (
        <p className="smifs-dir-list-empty">No matches.</p>
      ) : (
        <ul className="smifs-dir-list-body">
          {items.map((e, i) => (
            <li key={i} className="smifs-dir-list-row" data-testid={`directory-list-row-${i}`}>
              <div className="smifs-dir-list-primary">
                <span className="smifs-dir-list-name">{e.name || e.employee_id || "—"}</span>
                {e.designation && fields.includes("designation") && (
                  <span className="smifs-dir-list-sub">{e.designation}</span>
                )}
              </div>
              <div className="smifs-dir-list-meta">
                {fields.includes("department") && e.department && <span>{e.department}</span>}
                {fields.includes("location") && e.location && <span>{(e.location || "").split(",")[0]}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
