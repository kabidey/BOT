import React, { useEffect, useState } from "react";
import axios from "axios";
import { Loader2, RefreshCw, Globe2, FileWarning } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

function headers() {
  const t = localStorage.getItem("smifs_admin_token") || "";
  return t ? { "X-Admin-Token": t } : {};
}

export default function IngestionTab() {
  const [status, setStatus] = useState(null);
  const [bluff, setBluff] = useState(null);
  const [loading, setLoading] = useState(false);
  const [crawling, setCrawling] = useState({});  // site -> bool
  const [error, setError] = useState("");

  async function loadStatus() {
    setLoading(true);
    setError("");
    try {
      const [s, b] = await Promise.all([
        axios.get(`${API}/admin/ingest/status`, { headers: headers() }),
        axios.get(`${API}/admin/bluff/summary`, { headers: headers() }),
      ]);
      setStatus(s.data);
      setBluff(b.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadStatus();
    const i = setInterval(loadStatus, 30000);
    return () => clearInterval(i);
  }, []);

  async function triggerCrawl(site, dryRun = false) {
    setCrawling((p) => ({ ...p, [site]: true }));
    setError("");
    try {
      const r = await axios.post(
        `${API}/admin/ingest/crawl`,
        { site, dry_run: dryRun },
        { headers: headers(), timeout: 1800000 }
      );
      // Show a quick toast-like inline result
      const msg = `Crawl ${site}${dryRun ? " (dry-run)" : ""}: ` +
        `pages=${r.data.pages_fetched ?? 0}, ` +
        `chunks=${r.data.chunks_written ?? 0}, ` +
        `status=${r.data.status}`;
      setError(msg);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || "Crawl failed");
    } finally {
      setCrawling((p) => ({ ...p, [site]: false }));
      loadStatus();
    }
  }

  return (
    <div className="space-y-6" data-testid="ingestion-tab">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-slate-100">Knowledge Ingestion</h3>
          <p className="text-xs text-slate-400">
            Phase 24d — crawl regulator & investor-education sites into
            <code className="mx-1 rounded bg-slate-800 px-1 py-0.5 text-[10px]">doc_chunks</code>.
            Same-origin only, robots.txt strict.
          </p>
        </div>
        <button
          type="button"
          onClick={loadStatus}
          className="flex items-center gap-1 rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800"
          data-testid="ingestion-refresh"
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Anti-Bluff Rail tile (Phase 24b) */}
      {bluff && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-4" data-testid="anti-bluff-tile">
          <div className="mb-2 flex items-center gap-2">
            <FileWarning size={14} className="text-amber-400" />
            <h4 className="text-sm font-semibold text-slate-100">Anti-Bluff Rail · last 7 days</h4>
          </div>
          <div className="grid grid-cols-3 gap-3 text-center">
            <div className="rounded bg-slate-900 p-3" data-testid="bluff-bucket-grounded">
              <div className="text-2xl font-semibold text-emerald-400">{bluff.buckets?.answered_grounded ?? 0}</div>
              <div className="text-[10px] uppercase text-slate-500">Confident</div>
            </div>
            <div className="rounded bg-slate-900 p-3" data-testid="bluff-bucket-caveat">
              <div className="text-2xl font-semibold text-amber-400">{bluff.buckets?.answered_with_caveat ?? 0}</div>
              <div className="text-[10px] uppercase text-slate-500">Cautious</div>
            </div>
            <div className="rounded bg-slate-900 p-3" data-testid="bluff-bucket-escalated">
              <div className="text-2xl font-semibold text-rose-400">{bluff.buckets?.escalated ?? 0}</div>
              <div className="text-[10px] uppercase text-slate-500">Escalated</div>
            </div>
          </div>
          <p className="mt-2 text-[10px] text-slate-500">
            Thresholds: high ≥ {bluff.thresholds?.high} · medium ≥ {bluff.thresholds?.medium} · total {bluff.total}
          </p>
        </div>
      )}

      {error && (
        <div className="rounded border border-amber-700 bg-amber-900/20 px-3 py-2 text-xs text-amber-200" data-testid="ingestion-error">
          {error}
        </div>
      )}

      {/* Source list */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 overflow-hidden">
        <div className="flex items-center gap-2 border-b border-slate-800 px-4 py-2">
          <Globe2 size={14} className="text-blue-400" />
          <h4 className="text-sm font-semibold text-slate-100">Sources</h4>
          {loading && <Loader2 size={12} className="animate-spin text-slate-400" />}
        </div>
        <table className="w-full text-xs text-slate-300">
          <thead className="bg-slate-900/60 text-[10px] uppercase tracking-wider text-slate-500">
            <tr>
              <th className="px-4 py-2 text-left">Domain</th>
              <th className="px-4 py-2 text-right">Chunks</th>
              <th className="px-4 py-2 text-left">Last crawl</th>
              <th className="px-4 py-2 text-left">Status</th>
              <th className="px-4 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {(status?.sources || []).map((s) => (
              <tr key={s.site_domain} className="border-t border-slate-800" data-testid={`source-row-${s.site_domain}`}>
                <td className="px-4 py-2">
                  <div className="font-mono text-slate-200">{s.site_domain}</div>
                  <a href={s.seed_url} target="_blank" rel="noopener noreferrer"
                     className="text-[10px] text-slate-500 hover:text-blue-400">
                    {s.seed_url}
                  </a>
                </td>
                <td className="px-4 py-2 text-right tabular-nums">{s.chunks_count}</td>
                <td className="px-4 py-2 text-slate-400">
                  {s.last_crawl_at ? new Date(s.last_crawl_at).toLocaleString() : "—"}
                </td>
                <td className="px-4 py-2">
                  {s.last_status ? (
                    <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px]">{s.last_status}</span>
                  ) : "—"}
                </td>
                <td className="px-4 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => triggerCrawl(s.site_domain, true)}
                    disabled={!!crawling[s.site_domain]}
                    className="mr-2 rounded border border-slate-700 px-2 py-0.5 text-[10px] hover:bg-slate-800 disabled:opacity-50"
                    data-testid={`crawl-dryrun-${s.site_domain}`}
                  >Dry-run</button>
                  <button
                    type="button"
                    onClick={() => triggerCrawl(s.site_domain, false)}
                    disabled={!!crawling[s.site_domain]}
                    className="rounded bg-emerald-700/60 px-2 py-0.5 text-[10px] text-emerald-100 hover:bg-emerald-700 disabled:opacity-50"
                    data-testid={`crawl-now-${s.site_domain}`}
                  >
                    {crawling[s.site_domain] ? <Loader2 size={10} className="inline animate-spin" /> : "Crawl now"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Crawl history */}
      {status?.history?.length > 0 && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/40 overflow-hidden">
          <div className="border-b border-slate-800 px-4 py-2">
            <h4 className="text-sm font-semibold text-slate-100">Recent crawls</h4>
          </div>
          <table className="w-full text-xs text-slate-300">
            <thead className="bg-slate-900/60 text-[10px] uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-4 py-2 text-left">When</th>
                <th className="px-4 py-2 text-left">Domain</th>
                <th className="px-4 py-2 text-right">Pages</th>
                <th className="px-4 py-2 text-right">Chunks</th>
                <th className="px-4 py-2 text-right">Tokens</th>
                <th className="px-4 py-2 text-left">Status</th>
                <th className="px-4 py-2 text-right">Dur (s)</th>
              </tr>
            </thead>
            <tbody>
              {status.history.map((h, i) => (
                <tr key={i} className="border-t border-slate-800" data-testid={`history-row-${i}`}>
                  <td className="px-4 py-2 text-slate-400">{new Date(h.started_at).toLocaleString()}</td>
                  <td className="px-4 py-2 font-mono text-slate-200">{h.domain}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{h.pages_fetched ?? 0}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{h.chunks_written ?? 0}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{h.tokens_estimated ?? 0}</td>
                  <td className="px-4 py-2">
                    <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px]">{h.status}</span>
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums">{h.duration_sec ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
