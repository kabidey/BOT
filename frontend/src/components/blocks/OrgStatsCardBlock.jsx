import { Building2, Users, MapPin, Layers } from "lucide-react";

function Tile({ icon: Icon, label, value, testid }) {
  return (
    <div className="smifs-stat-tile" data-testid={testid}>
      <div className="smifs-stat-tile-icon" aria-hidden><Icon size={14} strokeWidth={2.25} /></div>
      <div>
        <p className="smifs-stat-tile-value">{value ?? "—"}</p>
        <p className="smifs-stat-tile-label">{label}</p>
      </div>
    </div>
  );
}

export default function OrgStatsCardBlock({ block }) {
  const d = block.data || {};
  return (
    <div className="smifs-org-stats" data-testid="org-stats-card">
      <div className="smifs-org-stats-grid">
        <Tile icon={Users} label="Total employees" value={d.total_employees} testid="stat-total-employees" />
        <Tile icon={Users} label="Active" value={d.active_employees} testid="stat-active" />
        <Tile icon={Layers} label="Departments" value={d.total_departments} testid="stat-departments" />
        <Tile icon={MapPin} label="Locations" value={d.total_locations} testid="stat-locations" />
      </div>
      {d.last_sync && (
        <p className="smifs-org-stats-footer">
          <Building2 size={10} strokeWidth={2.25} /> Directory last synced {new Date(d.last_sync).toLocaleString()}
        </p>
      )}
    </div>
  );
}
