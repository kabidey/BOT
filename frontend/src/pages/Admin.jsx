import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { LayoutDashboard, Inbox, Wallet, BarChart3, FileStack, LogOut, Lock, ShieldCheck, AlertCircle } from "lucide-react";

import OverviewTab from "@/components/admin/OverviewTab";
import LeadsTab from "@/components/admin/LeadsTab";
import CostLedgerTab from "@/components/admin/CostLedgerTab";
import InsightsTab from "@/components/admin/InsightsTab";
import KnowledgeBaseTab from "@/components/admin/KnowledgeBaseTab";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;
const TOKEN_KEY = "smifs_admin_token";

const TABS = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "leads", label: "Leads", icon: Inbox },
  { id: "cost", label: "Cost Ledger", icon: Wallet },
  { id: "insights", label: "Insights", icon: BarChart3 },
  { id: "kb", label: "Knowledge Base", icon: FileStack },
];

export default function Admin() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || "");
  const [pendingToken, setPendingToken] = useState("");
  const [authError, setAuthError] = useState("");
  const [authChecking, setAuthChecking] = useState(false);
  const [activeTab, setActiveTab] = useState("overview");

  // Authenticated axios instance
  const adminApi = useMemo(() => {
    const inst = axios.create({ baseURL: API });
    inst.interceptors.request.use((cfg) => {
      const t = localStorage.getItem(TOKEN_KEY);
      if (t) cfg.headers["X-Admin-Token"] = t;
      return cfg;
    });
    inst.interceptors.response.use(
      (r) => r,
      (err) => {
        if (err?.response?.status === 401) {
          // token rejected
          localStorage.removeItem(TOKEN_KEY);
          setToken("");
          setAuthError("Token rejected. Please re-authenticate.");
        }
        return Promise.reject(err);
      }
    );
    return inst;
  }, []);

  // On mount: validate stored token (silently — interceptor handles 401)
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setAuthChecking(true);
    adminApi.get("/admin/cost")
      .catch(() => {})
      .finally(() => { if (!cancelled) setAuthChecking(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const submitToken = (e) => {
    e?.preventDefault();
    if (!pendingToken.trim()) return;
    setAuthError("");
    localStorage.setItem(TOKEN_KEY, pendingToken.trim());
    setToken(pendingToken.trim());
    setPendingToken("");
  };

  const signOut = () => {
    localStorage.removeItem(TOKEN_KEY);
    setToken("");
    setActiveTab("overview");
  };

  if (!token) {
    return (
      <div className="smifs-shell smifs-admin-gate" data-testid="admin-gate">
        <div className="smifs-bg-blob smifs-bg-blob--gold" aria-hidden />
        <div className="smifs-bg-blob smifs-bg-blob--teal" aria-hidden />
        <div className="smifs-grain" aria-hidden />
        <form className="smifs-admin-gate-card" onSubmit={submitToken}>
          <div className="smifs-admin-gate-icon" aria-hidden>
            <Lock size={20} strokeWidth={2} />
          </div>
          <p className="smifs-admin-gate-eyebrow">Mackertich ONE · Admin Console</p>
          <h1 className="smifs-admin-gate-title">Restricted access</h1>
          <p className="smifs-admin-gate-body">
            Enter the admin token issued by the Mackertich ONE operations team (SMIFS Ltd) to continue.
          </p>
          <input
            type="password"
            placeholder="X-Admin-Token"
            value={pendingToken}
            onChange={(e) => setPendingToken(e.target.value)}
            className="smifs-admin-gate-input"
            data-testid="admin-token-input"
            autoFocus
          />
          {authError && (
            <p className="smifs-admin-gate-err" data-testid="admin-token-err">
              <AlertCircle size={12} /> {authError}
            </p>
          )}
          <button type="submit" className="smifs-admin-gate-submit" data-testid="admin-token-submit">
            Unlock console <ShieldCheck size={14} strokeWidth={2.5} />
          </button>
        </form>
      </div>
    );
  }

  const TabComp =
    activeTab === "overview" ? OverviewTab :
    activeTab === "leads"    ? LeadsTab :
    activeTab === "cost"     ? CostLedgerTab :
    activeTab === "insights" ? InsightsTab :
    KnowledgeBaseTab;

  return (
    <div className="smifs-admin-shell" data-testid="admin-shell">
      <div className="smifs-bg-blob smifs-bg-blob--gold" aria-hidden />
      <div className="smifs-bg-blob smifs-bg-blob--teal" aria-hidden />
      <div className="smifs-grain" aria-hidden />

      <aside className="smifs-admin-side">
        <div className="smifs-admin-brand">
          <div className="smifs-mono" aria-hidden>M1</div>
          <div>
            <p className="smifs-admin-brand-eyebrow">Mackertich ONE · Admin</p>
            <h1 className="smifs-admin-brand-title">Operations Console</h1>
          </div>
        </div>
        <nav className="smifs-admin-nav">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              className={`smifs-admin-tab ${activeTab === id ? "smifs-admin-tab--on" : ""}`}
              onClick={() => setActiveTab(id)}
              data-testid={`admin-tab-${id}`}
            >
              <Icon size={15} strokeWidth={2.25} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <button
          type="button"
          className="smifs-admin-signout"
          onClick={signOut}
          data-testid="admin-signout"
        >
          <LogOut size={14} strokeWidth={2.25} />
          Sign out
        </button>
      </aside>

      <main className="smifs-admin-main" data-testid={`admin-content-${activeTab}`}>
        {authChecking ? (
          <div className="smifs-admin-loading">Checking token…</div>
        ) : (
          <TabComp api={adminApi} />
        )}
      </main>
    </div>
  );
}
