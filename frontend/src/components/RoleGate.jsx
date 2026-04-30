import { User, Briefcase, Sparkles } from "lucide-react";

export default function RoleGate({ onSelect, disabled }) {
  const options = [
    { role: "client", label: "I am a client", icon: User,
      sub: "UCC + PAN verification · rich client context" },
    { role: "employee", label: "I am an Employee", icon: Briefcase,
      sub: "Email + PAN verification · directory + knowledge base" },
    { role: "visitor", label: "I am new to the site", icon: Sparkles,
      sub: "Explore Mackertich ONE · request a callback" },
  ];
  return (
    <div className="smifs-role-gate" data-testid="role-gate">
      <div className="smifs-role-gate-inner">
        <p className="smifs-role-eyebrow">Welcome to Mackertich ONE</p>
        <h2 className="smifs-role-title">How would you like to continue?</h2>
        <p className="smifs-role-sub">
          Choose the path that fits you — we'll route the conversation accordingly.
        </p>
        <div className="smifs-role-pills">
          {options.map(({ role, label, icon: Icon, sub }) => (
            <button
              type="button"
              key={role}
              disabled={disabled}
              className="smifs-role-pill"
              data-testid={`role-pill-${role}`}
              onClick={() => onSelect(role)}
            >
              <span className="smifs-role-pill-icon" aria-hidden>
                <Icon size={15} strokeWidth={2.25} />
              </span>
              <span className="smifs-role-pill-label">
                <span className="smifs-role-pill-title">{label}</span>
                <span className="smifs-role-pill-sub">{sub}</span>
              </span>
            </button>
          ))}
        </div>
        <p className="smifs-role-footnote">
          We don't share anything outside your session. Sign out anytime from the chat footer.
        </p>
      </div>
    </div>
  );
}
