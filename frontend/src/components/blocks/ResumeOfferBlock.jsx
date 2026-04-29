import { History, Play, X } from "lucide-react";

function timeAgo(iso) {
  if (!iso) return "";
  try {
    const t = new Date(iso).getTime();
    const diffMs = Date.now() - t;
    const m = Math.round(diffMs / 60000);
    if (m < 1) return "just now";
    if (m < 60) return `${m} min ago`;
    const h = Math.round(m / 60);
    if (h < 24) return `${h} hr${h > 1 ? "s" : ""} ago`;
    const d = Math.round(h / 24);
    return `${d} day${d > 1 ? "s" : ""} ago`;
  } catch { return ""; }
}

export default function ResumeOfferBlock({ block, onResume, onDecline }) {
  const candidates = (block.data && block.data.candidates) || [];
  if (candidates.length === 0) return null;
  const top = candidates[0];
  return (
    <div className="smifs-resume-card" data-testid="resume-offer-card">
      <header className="smifs-resume-head">
        <History size={14} strokeWidth={2.25} />
        <span>Welcome back — we spoke earlier</span>
      </header>
      <blockquote className="smifs-resume-quote" data-testid="resume-summary">
        "{top.summary}"
      </blockquote>
      <p className="smifs-resume-meta">
        {top.message_count} messages · {timeAgo(top.ended_at)}
        {top.session_type ? ` · ${top.session_type}` : ""}
      </p>
      <div className="smifs-resume-actions">
        <button
          type="button"
          className="smifs-resume-btn smifs-resume-btn--primary"
          onClick={() => onResume && onResume(top.prior_session_id)}
          data-testid="resume-btn-primary"
        >
          <Play size={12} strokeWidth={2.5} /> Resume previous
        </button>
        <button
          type="button"
          className="smifs-resume-btn smifs-resume-btn--ghost"
          onClick={() => onDecline && onDecline()}
          data-testid="resume-btn-decline"
        >
          <X size={12} strokeWidth={2.5} /> Start fresh
        </button>
      </div>
    </div>
  );
}
