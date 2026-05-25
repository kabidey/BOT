import { useMemo, useState } from "react";
import { ArrowDownAZ, ArrowUpAZ, Download as DownloadIcon } from "lucide-react";

// Phase 20 — TableBlock
// Props: block = { title, columns:[{key,label,type,sortable,frozen,default_sort}], rows, row_total, csv_url, footnote }
// Mobile collapse to cards below 640px.
export default function TableBlock({ block, msgIdx }) {
  const cols = block.columns || [];
  const initial = useMemo(() => {
    const def = cols.find((c) => c.default_sort);
    return def ? { key: def.key, dir: def.default_sort === "desc" ? "desc" : "asc" } : null;
  }, [cols]);
  const [sort, setSort] = useState(initial);
  const [page, setPage] = useState(0);
  const PAGE = 25;

  const rows = useMemo(() => {
    const raw = (block.rows || []).slice();
    if (!sort) return raw;
    return raw.sort((a, b) => {
      const va = a[sort.key]; const vb = b[sort.key];
      if (va === undefined || va === null) return 1;
      if (vb === undefined || vb === null) return -1;
      const an = Number(va); const bn = Number(vb);
      const cmp = !Number.isNaN(an) && !Number.isNaN(bn) && typeof va !== "string"
        ? an - bn
        : String(va).localeCompare(String(vb));
      return sort.dir === "asc" ? cmp : -cmp;
    });
  }, [block.rows, sort]);

  const view = rows.slice(page * PAGE, (page + 1) * PAGE);
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE));

  const fmt = (v, type) => {
    if (v === null || v === undefined || v === "") return "—";
    if (type === "inr") {
      const n = Number(v); if (Number.isNaN(n)) return String(v);
      if (n >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
      if (n >= 1e5) return `₹${(n / 1e5).toFixed(2)} L`;
      return `₹${n.toLocaleString("en-IN")}`;
    }
    if (type === "date_relative" && v) {
      const ms = new Date(v).getTime();
      if (!Number.isNaN(ms)) {
        const d = Math.floor((Date.now() - ms) / 86400000);
        if (d < 1) return "today";
        if (d < 30) return `${d}d ago`;
        if (d < 365) return `${Math.floor(d / 30)}mo ago`;
        return `${Math.floor(d / 365)}y ago`;
      }
    }
    if (type === "num") {
      const n = Number(v); if (Number.isNaN(n)) return String(v);
      return n.toLocaleString("en-IN");
    }
    return String(v);
  };

  const toggleSort = (key, sortable) => {
    if (!sortable) return;
    setPage(0);
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: "desc" };
      return { key, dir: prev.dir === "desc" ? "asc" : "desc" };
    });
  };

  const exportCsv = () => {
    const lines = [];
    lines.push(cols.map((c) => `"${c.label}"`).join(","));
    for (const r of rows) {
      lines.push(cols.map((c) => `"${String(r[c.key] ?? "").replace(/"/g, '""')}"`).join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url;
    a.download = (block.title || "table").replace(/[^a-z0-9]+/gi, "_") + ".csv";
    document.body.appendChild(a); a.click();
    setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 100);
  };

  return (
    <div className="smifs-block-table" data-testid={`table-block-${msgIdx}`}>
      <header className="smifs-block-table-head">
        {block.title && <h4>{block.title}</h4>}
        <div className="smifs-block-table-actions">
          <span className="smifs-block-table-count">
            {rows.length === block.rows.length
              ? `${rows.length} rows`
              : `${rows.length}/${block.row_total || block.rows.length} rows`}
          </span>
          {rows.length > 0 && (
            <button onClick={exportCsv} className="smifs-block-table-csv" data-testid={`table-block-${msgIdx}-csv`}>
              <DownloadIcon size={11} /> CSV
            </button>
          )}
        </div>
      </header>
      <div className="smifs-block-table-scroll">
        <table>
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c.key}
                    className={`${c.frozen ? "frozen" : ""} ${c.sortable ? "sortable" : ""}`}
                    onClick={() => toggleSort(c.key, c.sortable)}>
                  {c.label}
                  {c.sortable && sort && sort.key === c.key && (
                    sort.dir === "asc" ? <ArrowUpAZ size={11}/> : <ArrowDownAZ size={11}/>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.map((r, ri) => (
              <tr key={ri}>
                {cols.map((c) => (
                  <td key={c.key} className={c.frozen ? "frozen" : ""} data-type={c.type || "text"}>
                    {fmt(r[c.key], c.type)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Mobile collapse: render row-cards */}
      <div className="smifs-block-table-cards">
        {view.map((r, ri) => (
          <div key={ri} className="smifs-block-table-card">
            {cols.map((c) => (
              <div key={c.key} className="smifs-block-table-card-row">
                <span className="smifs-block-table-card-label">{c.label}</span>
                <span className="smifs-block-table-card-value" data-type={c.type || "text"}>
                  {fmt(r[c.key], c.type)}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
      {pageCount > 1 && (
        <div className="smifs-block-table-pager">
          <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>‹</button>
          <span>{page + 1} / {pageCount}</span>
          <button onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
                  disabled={page >= pageCount - 1}>›</button>
        </div>
      )}
      {block.footnote && <p className="smifs-block-table-footnote">{block.footnote}</p>}
    </div>
  );
}
