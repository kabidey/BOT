import { Download as DownloadIcon, FileSpreadsheet, FileJson } from "lucide-react";

// Phase 20 — DownloadBlock
// Props: block = { title, format ('csv'|'json'), url, row_count, size_bytes }
export default function DownloadBlock({ block, msgIdx }) {
  const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
  const url = block.url
    ? (block.url.startsWith("http") ? block.url : `${BACKEND_URL}${block.url}`)
    : null;
  const Icon = block.format === "csv" ? FileSpreadsheet : FileJson;
  const sizeLabel = (() => {
    const n = Number(block.size_bytes || 0);
    if (!n) return "";
    if (n > 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    if (n > 1024) return `${(n / 1024).toFixed(0)} KB`;
    return `${n} B`;
  })();
  return (
    <div className="smifs-block-download" data-testid={`download-block-${msgIdx}`}>
      <Icon size={18} className="smifs-block-download-icon" />
      <div className="smifs-block-download-meta">
        <div className="smifs-block-download-title">{block.title || "Dataset"}</div>
        <div className="smifs-block-download-sub">
          {block.row_count ? `${Number(block.row_count).toLocaleString("en-IN")} rows` : ""}
          {block.row_count && sizeLabel ? " · " : ""}
          {sizeLabel}
        </div>
      </div>
      {url ? (
        <a href={url} download className="smifs-block-download-btn"
            data-testid={`download-block-${msgIdx}-btn`}>
          <DownloadIcon size={13} /> Download {block.format?.toUpperCase() || ""}
        </a>
      ) : (
        <span className="smifs-block-download-disabled">link expired</span>
      )}
    </div>
  );
}
