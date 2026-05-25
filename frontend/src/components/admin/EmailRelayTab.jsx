import { useEffect, useState } from "react";
import {
  Mail, MailCheck, MailWarning, MailX, Save, Trash2, Send, Zap, ShieldCheck,
  ShieldAlert, RefreshCw, Info, AlertCircle, CheckCircle2,
} from "lucide-react";

// Phase 19.2 — canonical SMTP config UI. No env editing required.
// Source: GET → render → PUT to upsert → Test Connection / Test Send.

const STATUS_META = {
  sent:                 { tone: "ok",   icon: MailCheck,   label: "Sent" },
  draft_only:           { tone: "skip", icon: MailX,       label: "Draft only" },
  smtp_auth_disabled:   { tone: "warn", icon: MailWarning, label: "SMTP auth disabled" },
  failed_with_fallback: { tone: "warn", icon: MailWarning, label: "Failed · fallback to draft" },
  smtp_not_configured:  { tone: "skip", icon: MailX,       label: "SMTP not configured" },
  no_recipient:         { tone: "skip", icon: MailX,       label: "No recipient" },
};

function fmtRelative(iso) {
  if (!iso) return "—";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return iso;
  const delta = Math.floor((Date.now() - ts) / 1000);
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export default function EmailRelayTab({ api }) {
  // Panel A — config form
  const [form, setForm] = useState({
    host: "", port: 587, starttls: true, user: "",
    password: "", from_email: "", from_name: "",
    cc_ops_fixed: "",
  });
  const [stored, setStored] = useState(null);    // last GET payload (masked)
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [pwTouched, setPwTouched] = useState(false);
  const [toast, setToast] = useState(null);

  // Panel B — tests
  const [testConn, setTestConn] = useState(null);
  const [testConnBusy, setTestConnBusy] = useState(false);
  const [testRecipient, setTestRecipient] = useState("");
  const [testSendBusy, setTestSendBusy] = useState(false);
  const [testSendResult, setTestSendResult] = useState(null);

  // Panel C — status & recent activity
  const [status, setStatus] = useState(null);

  const showToast = (kind, msg) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 6500);
  };

  const loadAll = async () => {
    setLoading(true);
    try {
      const { data: cfg } = await api.get("/admin/email_relay/config");
      setStored(cfg);
      setForm({
        host: cfg.host || "",
        port: cfg.port || 587,
        starttls: cfg.starttls ?? true,
        user: cfg.user || "",
        // We do NOT pre-fill the password input — show placeholder instead.
        password: "",
        from_email: cfg.from_email || "",
        from_name: cfg.from_name || "",
        cc_ops_fixed: (cfg.cc_ops_fixed || []).join(", "),
      });
      setPwTouched(false);
    } catch (e) {
      showToast("err", e?.response?.data?.detail || "Failed to load SMTP config");
    } finally {
      setLoading(false);
    }
    try {
      const { data } = await api.get("/admin/email_relay/status");
      setStatus(data);
    } catch (e) { /* non-fatal */ }
  };

  useEffect(() => { loadAll(); /* eslint-disable-next-line */ }, []);

  const onField = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      const cc_list = String(form.cc_ops_fixed || "")
        .split(",").map((s) => s.trim()).filter(Boolean);
      const payload = {
        host: form.host.trim(),
        port: Number(form.port) || 587,
        starttls: !!form.starttls,
        user: form.user.trim(),
        from_email: form.from_email.trim(),
        from_name: form.from_name.trim() || "SMIFS Wealth Guidance",
        cc_ops_fixed: cc_list,
      };
      // Send password only when the user actually typed one; otherwise the
      // backend keeps the existing stored password.
      if (pwTouched && form.password) {
        payload.password = form.password;
      } else if (stored?.password_set) {
        payload.password = stored.password_masked || "***";  // signal "keep existing"
      } else {
        payload.password = form.password || "";
      }
      const { data } = await api.put("/admin/email_relay/config", payload);
      setStored(data.config);
      setForm((f) => ({ ...f, password: "" }));
      setPwTouched(false);
      showToast("ok", "SMTP configuration saved.");
      await loadAll();
    } catch (e) {
      showToast("err", e?.response?.data?.detail || "Save failed");
    } finally { setSaving(false); }
  };

  const resetToEnv = async () => {
    if (!window.confirm("Clear the Mongo SMTP config? The relay will fall back to environment variables (or be disabled if no env config exists).")) {
      return;
    }
    setResetting(true);
    try {
      const { data } = await api.delete("/admin/email_relay/config");
      setStored(data.config);
      showToast("ok", `Cleared. Source is now: ${data.config?.source || "none"}.`);
      await loadAll();
    } catch (e) {
      showToast("err", e?.response?.data?.detail || "Reset failed");
    } finally { setResetting(false); }
  };

  const runTestConn = async () => {
    setTestConnBusy(true); setTestConn(null);
    try {
      const { data } = await api.post("/admin/email_relay/test_connection");
      setTestConn(data);
    } catch (e) {
      setTestConn({ ok: false, error_kind: "unknown_error",
                    error_message: e?.response?.data?.detail || String(e) });
    } finally { setTestConnBusy(false); }
  };

  const runTestSend = async () => {
    if (!testRecipient || !testRecipient.includes("@")) {
      showToast("err", "Enter a valid recipient email.");
      return;
    }
    setTestSendBusy(true); setTestSendResult(null);
    try {
      const { data } = await api.post("/admin/email_relay/test_send", { recipient: testRecipient });
      setTestSendResult(data);
      if (data.ok) {
        showToast("ok", `Test email sent to ${testRecipient}.`);
        // Refresh status to surface the new attempt in the ring buffer.
        const { data: st } = await api.get("/admin/email_relay/status");
        setStatus(st);
      } else {
        showToast("err", `Send failed: ${data.error_kind}`);
      }
    } catch (e) {
      setTestSendResult({ ok: false, error_kind: "unknown_error",
                          error_message: e?.response?.data?.detail || String(e) });
      showToast("err", "Test send failed.");
    } finally { setTestSendBusy(false); }
  };

  const sourcePill = (src) => {
    if (src === "mongo")
      return <span className="smifs-admin-pill smifs-admin-pill--ok" data-testid="erelay-source-pill"><ShieldCheck size={11} /> Mongo (canonical)</span>;
    if (src === "env")
      return <span className="smifs-admin-pill smifs-admin-pill--skip" data-testid="erelay-source-pill"><Info size={11} /> Env (legacy fallback)</span>;
    return <span className="smifs-admin-pill smifs-admin-pill--warn" data-testid="erelay-source-pill"><ShieldAlert size={11} /> Unconfigured</span>;
  };

  return (
    <div className="smifs-admin-panel" data-testid="email-relay-tab">
      <div className="smifs-admin-panel-head">
        <h2><Mail size={18} style={{ verticalAlign: "-3px", marginRight: 8 }} />SMTP / Email Relay</h2>
        {stored && sourcePill(stored.source)}
      </div>

      {loading ? (
        <div className="smifs-admin-loading">Loading configuration…</div>
      ) : (
        <>
          {/* Panel A — Configuration */}
          <section className="smifs-kb-api-panel" data-testid="erelay-config-panel">
            <header className="smifs-kb-api-head">
              <div>
                <h3 className="smifs-kb-api-title">Configuration</h3>
                <p className="smifs-kb-api-sub">
                  Paste your Office 365 (or any SMTP) credentials here. The password is encrypted
                  at rest with Fernet before being written to Mongo. Source updates take effect
                  on the very next send — no restart required.
                </p>
              </div>
            </header>

            <div className="smifs-erelay-grid" data-testid="erelay-form">
              <label>SMTP host
                <input type="text" value={form.host} onChange={(e) => onField("host", e.target.value)}
                       placeholder="smtp.office365.com" data-testid="erelay-host" />
              </label>
              <label>Port
                <input type="number" value={form.port} onChange={(e) => onField("port", e.target.value)}
                       placeholder="587" data-testid="erelay-port" />
              </label>
              <label className="smifs-erelay-toggle">
                <input type="checkbox" checked={!!form.starttls}
                       onChange={(e) => onField("starttls", e.target.checked)}
                       data-testid="erelay-starttls" />
                STARTTLS
              </label>
              <label>Username
                <input type="text" value={form.user} onChange={(e) => onField("user", e.target.value)}
                       placeholder="wealth.guidance@smifs.com" data-testid="erelay-user" />
              </label>
              <label>Password
                <input type="password" value={form.password}
                       onChange={(e) => { onField("password", e.target.value); setPwTouched(true); }}
                       placeholder={stored?.password_set
                         ? `${stored.password_masked || "***"} (leave blank to keep)`
                         : "Enter SMTP password"}
                       data-testid="erelay-password" autoComplete="new-password" />
              </label>
              <label>From email
                <input type="text" value={form.from_email}
                       onChange={(e) => onField("from_email", e.target.value)}
                       placeholder="wealth.guidance@smifs.com" data-testid="erelay-from-email" />
              </label>
              <label>From name
                <input type="text" value={form.from_name}
                       onChange={(e) => onField("from_name", e.target.value)}
                       placeholder="SMIFS Wealth Guidance" data-testid="erelay-from-name" />
              </label>
              <label className="smifs-erelay-wide">Fixed Ops CC (comma-separated)
                <input type="text" value={form.cc_ops_fixed}
                       onChange={(e) => onField("cc_ops_fixed", e.target.value)}
                       placeholder="ho.operations@smifs.com, insurance.bpo@smifs.com, …"
                       data-testid="erelay-cc-ops" />
              </label>
            </div>

            <div className="smifs-admin-drawer-actions" style={{ marginTop: 16 }}>
              <button onClick={save} disabled={saving} className="smifs-admin-btn-primary"
                      data-testid="erelay-save">
                <Save size={14} /> {saving ? "Saving…" : "Save configuration"}
              </button>
              <button onClick={resetToEnv} disabled={resetting} className="smifs-admin-btn-ghost"
                      data-testid="erelay-reset">
                <Trash2 size={14} /> {resetting ? "Clearing…" : "Reset to env (clear DB config)"}
              </button>
            </div>

            <p className="smifs-admin-dim" style={{ marginTop: 10 }}>
              Source: <b>{stored?.source || "none"}</b>
              {stored?.updated_at && <> · last updated {fmtRelative(stored.updated_at)}</>}
              {" · "}password set: <b>{stored?.password_set ? "Yes" : "No"}</b>
              {stored?.password_masked && <> ({stored.password_masked})</>}
            </p>
          </section>

          {/* Panel B — Tests */}
          <section className="smifs-kb-api-panel" style={{ marginTop: 12 }} data-testid="erelay-test-panel">
            <header className="smifs-kb-api-head">
              <div>
                <h3 className="smifs-kb-api-title">Test the relay</h3>
                <p className="smifs-kb-api-sub">
                  <b>Connection</b> opens TCP → STARTTLS → AUTH → QUIT (no message).
                  <b> Test send</b> delivers a 1-paragraph branded email to one address.
                </p>
              </div>
            </header>

            <div className="smifs-erelay-test-row">
              <button onClick={runTestConn} disabled={testConnBusy} className="smifs-admin-btn-ghost"
                      data-testid="erelay-test-connection">
                <Zap size={14} /> {testConnBusy ? "Testing…" : "Test connection"}
              </button>
              {testConn && (
                <span className={`smifs-admin-pill smifs-admin-pill--${testConn.ok ? "ok" : "warn"}`}
                      data-testid="erelay-test-connection-result">
                  {testConn.ok
                    ? <><CheckCircle2 size={11} /> AUTH succeeded</>
                    : <><AlertCircle size={11} /> {testConn.error_kind}</>}
                </span>
              )}
            </div>
            {testConn && !testConn.ok && (
              <pre className="smifs-erelay-error" data-testid="erelay-test-connection-error">
                {testConn.error_message}
              </pre>
            )}

            <div className="smifs-erelay-test-row" style={{ marginTop: 16 }}>
              <input type="text" placeholder="recipient@example.com"
                     value={testRecipient}
                     onChange={(e) => setTestRecipient(e.target.value)}
                     className="smifs-erelay-recipient" data-testid="erelay-test-recipient" />
              <button onClick={runTestSend} disabled={testSendBusy}
                      className="smifs-admin-btn-primary" data-testid="erelay-test-send">
                <Send size={14} /> {testSendBusy ? "Sending…" : "Test send"}
              </button>
              {testSendResult && (
                <span className={`smifs-admin-pill smifs-admin-pill--${testSendResult.ok ? "ok" : "warn"}`}
                      data-testid="erelay-test-send-result">
                  {testSendResult.ok
                    ? <><MailCheck size={11} /> Sent at {(testSendResult.sent_at || "").slice(11,19)}</>
                    : <><MailWarning size={11} /> {testSendResult.error_kind}</>}
                </span>
              )}
            </div>
            {testSendResult && !testSendResult.ok && (
              <pre className="smifs-erelay-error" data-testid="erelay-test-send-error">
                {testSendResult.error_message}
              </pre>
            )}
          </section>

          {/* Panel C — Status & Recent activity */}
          {status && (
            <section className="smifs-kb-api-panel" style={{ marginTop: 12 }} data-testid="erelay-status-panel">
              <header className="smifs-kb-api-head">
                <div>
                  <h3 className="smifs-kb-api-title">
                    Status & recent activity
                    <span className={`smifs-admin-pill smifs-admin-pill--${status.configured ? "ok" : "warn"}`} style={{ marginLeft: 8 }}>
                      {status.configured ? "Live" : "Unconfigured"}
                    </span>
                  </h3>
                  <p className="smifs-kb-api-sub">
                    {status.host}:{status.port} · STARTTLS {status.starttls ? "on" : "off"} ·
                    user <code>{status.user || "—"}</code> · from <code>{status.from_email || "—"}</code>
                  </p>
                </div>
                <button onClick={loadAll} className="smifs-admin-btn-ghost" data-testid="erelay-refresh">
                  <RefreshCw size={14} /> Refresh
                </button>
              </header>

              {Array.isArray(status.recent_attempts) && status.recent_attempts.length > 0 ? (
                <div className="smifs-table-wrap" data-testid="erelay-recent-table">
                  <table className="smifs-table">
                    <thead>
                      <tr>
                        <th>Submission</th>
                        <th>TO</th>
                        <th>CC</th>
                        <th>Chain</th>
                        <th>Status</th>
                        <th>When</th>
                      </tr>
                    </thead>
                    <tbody>
                      {status.recent_attempts.map((r, idx) => {
                        const meta = STATUS_META[r.reason] || { tone: "skip", icon: MailX, label: r.reason };
                        const Icon = meta.icon;
                        return (
                          <tr key={idx} data-testid={`erelay-attempt-${idx}`}>
                            <td><code>{r.submission_id}</code></td>
                            <td>{(r.to && r.to[0]) || "—"}</td>
                            <td>{r.cc_count ?? 0}</td>
                            <td>{r.chain_levels ?? 0}</td>
                            <td>
                              <span className={`smifs-admin-pill smifs-admin-pill--${meta.tone}`}
                                    title={r.reason === "smtp_auth_disabled" ? "Tenant Basic Auth is disabled — enable authenticated SMTP on the mailbox" : ""}>
                                <Icon size={11} /> {meta.label}
                              </span>
                            </td>
                            <td className="smifs-admin-dim">{(r.ended_at || "").slice(11, 19)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="smifs-admin-dim" style={{ padding: "8px 4px" }}>
                  No send attempts in the current process. Hit <b>Test send</b> above to record one.
                </p>
              )}
            </section>
          )}
        </>
      )}

      {toast && (
        <div className={`smifs-toast smifs-toast--${toast.kind}`} data-testid="erelay-toast">
          {toast.kind === "ok" ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
          {toast.msg}
        </div>
      )}
    </div>
  );
}
