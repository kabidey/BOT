import { FileText } from "lucide-react";

/** Tiny markdown-ish formatter — bold + bullet lists. The Hub AI replies are mostly
 * plain prose; we keep this minimal to avoid pulling in react-markdown. */
function formatLine(text) {
  const parts = [];
  let lastIndex = 0;
  const re = /\*\*([^*]+)\*\*/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > lastIndex) parts.push(text.slice(lastIndex, m.index));
    parts.push(<strong key={`b-${m.index}`}>{m[1]}</strong>);
    lastIndex = m.index + m[0].length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}

function MdParagraphs({ text }) {
  if (!text) return null;
  const blocks = text.split(/\n\s*\n/);
  return (
    <>
      {blocks.map((block, bi) => {
        const lines = block.split(/\n/);
        const isBulletList = lines.length > 1 && lines.every((l) => /^\s*[-*•]\s+/.test(l));
        if (isBulletList) {
          return (
            <ul key={bi} className="smifs-md-list">
              {lines.map((l, li) => (
                <li key={li}>{formatLine(l.replace(/^\s*[-*•]\s+/, ""))}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={bi} className="smifs-md-p">
            {lines.map((l, li) => (
              <span key={li}>
                {formatLine(l)}
                {li < lines.length - 1 ? <br /> : null}
              </span>
            ))}
          </p>
        );
      })}
    </>
  );
}

// Phase 16 — short month-day-year formatter for "Updated 24 Mar 2026" badges
function formatUpdatedAt(iso) {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  } catch {
    return null;
  }
}

export default function TextBlock({ block, citations, onCitationClick, msgIdx, activeCitationKey }) {
  const grounded = block.grounded;
  const stopped = !!block.stopped;
  return (
    <div className="smifs-msg-bubble" data-testid={`text-block-${msgIdx}`}>
      <MdParagraphs text={block.text} />
      {stopped && (
        <span className="smifs-stopped" data-testid={`text-stopped-${msgIdx}`}>
          (stopped)
        </span>
      )}
      {citations && citations.length > 0 && (
        <div className="smifs-cites smifs-cites--inline" data-testid={`citations-${msgIdx}`}>
          {citations.map((c, ci) => {
            const key = `${msgIdx}-${ci}`;
            const isOfficial = c.is_official || c.source === "smifs_knowledge";
            const updatedLabel = formatUpdatedAt(c.updated_at);
            // Phase 16.1 — gate version badge on `version_major >= 2`.
            const versionMajor = typeof c.version_major === "number"
              ? c.version_major
              : (typeof c.version_no === "number"
                  ? c.version_no
                  : (typeof c.version_no === "string"
                      ? parseInt((c.version_no.match(/v?(\d+)/i) || [, ""])[1], 10) || null
                      : null));
            const versionLabel = (versionMajor != null && versionMajor >= 2)
              ? (typeof c.version_no === "string" ? c.version_no : `v${versionMajor}`)
              : null;
            return (
              <button
                key={ci}
                type="button"
                className={`smifs-cite ${activeCitationKey === key ? "smifs-cite--active" : ""} ${isOfficial ? "smifs-cite--official" : ""}`}
                onClick={() => onCitationClick(msgIdx, ci)}
                data-testid={`citation-${msgIdx}-${ci}`}
                title={`${isOfficial ? "SMIFS Official · " : ""}Score ${c.score.toFixed(2)}${updatedLabel ? ` · Updated ${updatedLabel}` : ""}${versionLabel ? ` · ${versionLabel}` : ""} — click to view passage`}
              >
                {isOfficial && (
                  <span className="smifs-cite-official-dot" aria-hidden data-testid={`citation-official-${msgIdx}-${ci}`} />
                )}
                <FileText size={11} strokeWidth={2.25} />
                <span className="smifs-cite-doc">{c.doc_title}</span>
                <span className="smifs-cite-sep">·</span>
                <span className="smifs-cite-sec">§{c.section}</span>
                {updatedLabel && (
                  <span className="smifs-cite-meta" data-testid={`citation-updated-${msgIdx}-${ci}`}>
                    Updated {updatedLabel}
                  </span>
                )}
                {versionLabel && (
                  <span className="smifs-cite-meta smifs-cite-meta--version" data-testid={`citation-version-${msgIdx}-${ci}`}>
                    {versionLabel}
                  </span>
                )}
                {isOfficial && <span className="smifs-cite-official-label">SMIFS Official</span>}
              </button>
            );
          })}
        </div>
      )}
      {grounded !== undefined && (
        <div
          className={`smifs-grounded ${grounded ? "smifs-grounded--on" : "smifs-grounded--off"}`}
          data-testid={grounded ? `grounded-on-${msgIdx}` : `grounded-off-${msgIdx}`}
        >
          {grounded ? "● Knowledge grounded" : "○ Outside knowledge base"}
        </div>
      )}
    </div>
  );
}
