import { FileText } from "lucide-react";

/** Tiny markdown-ish formatter — bold + bullet lists. The Hub AI replies are mostly
 * plain prose; we keep this minimal to avoid pulling in react-markdown. */
function formatLine(text) {
  // **bold**
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
  // Split on blank lines for paragraphs; collect bullet lines into <ul>
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

export default function TextBlock({ block, citations, onCitationClick, msgIdx, activeCitationKey }) {
  const grounded = block.grounded;
  return (
    <div className="smifs-msg-bubble" data-testid={`text-block-${msgIdx}`}>
      <MdParagraphs text={block.text} />
      {citations && citations.length > 0 && (
        <div className="smifs-cites smifs-cites--inline" data-testid={`citations-${msgIdx}`}>
          {citations.map((c, ci) => {
            const key = `${msgIdx}-${ci}`;
            return (
              <button
                key={ci}
                type="button"
                className={`smifs-cite ${activeCitationKey === key ? "smifs-cite--active" : ""}`}
                onClick={() => onCitationClick(msgIdx, ci)}
                data-testid={`citation-${msgIdx}-${ci}`}
                title={`Score ${c.score.toFixed(2)} — click to view passage`}
              >
                <FileText size={11} strokeWidth={2.25} />
                <span className="smifs-cite-doc">{c.doc_title}</span>
                <span className="smifs-cite-sep">·</span>
                <span className="smifs-cite-sec">§{c.section}</span>
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
