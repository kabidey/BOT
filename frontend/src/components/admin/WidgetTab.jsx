import { useEffect, useMemo, useState } from "react";
import { Copy, RotateCcw, Save, Plus, X, Eye } from "lucide-react";
import { toast } from "sonner";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const PUBLIC_HOST = BACKEND_URL; // same host serves /widget.js and /embed
const HEX_RE = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

const COLOR_FIELDS = [
  ["primary",          "Primary",          "Top bar / brand"],
  ["accent",           "Accent",           "Gold tone — bubble border"],
  ["background",       "Background",       "Iframe surface"],
  ["user_bubble",      "User bubble",      "Outgoing message"],
  ["assistant_bubble", "Assistant bubble", "Incoming message"],
  ["text",             "Text",             "Body copy"],
  ["header_bg",        "Header bg",        "Iframe header"],
  ["header_text",      "Header text",      "Iframe header text"],
];

export default function WidgetTab({ api }) {
  const [cfg, setCfg] = useState(null);
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);

  const load = async () => {
    try {
      const { data } = await api.get("/admin/widget/config");
      setCfg(data);
      setDraft(data);
    } catch (e) {
      toast.error("Failed to load widget config: " + (e?.response?.data?.detail || e.message));
    }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const setField = (k, v) => setDraft((d) => ({ ...d, [k]: v }));
  const setTheme = (k, v) => setDraft((d) => ({ ...d, theme: { ...(d.theme || {}), [k]: v } }));
  const setChip = (i, v) => setDraft((d) => {
    const next = [...(d.suggestion_chips || [])];
    next[i] = v;
    return { ...d, suggestion_chips: next };
  });
  const addChip = () => setDraft((d) => ({ ...d, suggestion_chips: [...(d.suggestion_chips || []), ""].slice(0, 5) }));
  const removeChip = (i) => setDraft((d) => {
    const next = [...(d.suggestion_chips || [])];
    next.splice(i, 1);
    return { ...d, suggestion_chips: next };
  });

  const validation = useMemo(() => {
    if (!draft) return { ok: false, msg: "Loading…" };
    if (!draft.brand_name?.trim()) return { ok: false, msg: "brand_name is required" };
    if (!draft.welcome_message?.trim()) return { ok: false, msg: "welcome_message is required" };
    for (const [k] of COLOR_FIELDS) {
      const v = draft.theme?.[k];
      if (v && !HEX_RE.test(v)) return { ok: false, msg: `theme.${k} must be hex like #C9A86A` };
    }
    if (!["bottom-right", "bottom-left"].includes(draft.position)) return { ok: false, msg: "position invalid" };
    return { ok: true, msg: "OK" };
  }, [draft]);

  const save = async () => {
    if (!validation.ok) { toast.error(validation.msg); return; }
    setSaving(true);
    try {
      const allowed = (typeof draft.allowed_origins === "string"
        ? draft.allowed_origins.split(",").map(s => s.trim()).filter(Boolean)
        : (draft.allowed_origins || []));
      const payload = { ...draft, allowed_origins: allowed };
      const { data } = await api.put("/admin/widget/config", payload);
      setCfg(data); setDraft(data);
      toast.success("Widget config saved.");
    } catch (e) {
      toast.error("Save failed: " + (e?.response?.data?.detail || e.message));
    } finally { setSaving(false); }
  };

  const reset = async () => {
    if (!window.confirm("Reset widget config to defaults?")) return;
    try {
      const { data } = await api.post("/admin/widget/reset");
      setCfg(data); setDraft(data);
      toast.success("Widget config reset to defaults.");
    } catch (e) {
      toast.error("Reset failed: " + (e?.response?.data?.detail || e.message));
    }
  };

  const snippet = `<!-- Mackertich ONE chat widget -->\n<script src="${PUBLIC_HOST}/widget.js" defer></script>`;
  const copySnippet = () => {
    navigator.clipboard.writeText(snippet).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => toast.error("Clipboard not available"));
  };

  if (!draft) {
    return <div className="smifs-admin-tabbody"><p className="smifs-text-muted">Loading widget configuration…</p></div>;
  }

  const allowedAsString = Array.isArray(draft.allowed_origins) ? draft.allowed_origins.join(", ") : (draft.allowed_origins || "");

  return (
    <div className="smifs-admin-tabbody smifs-widget-tab" data-testid="admin-widget-tab">
      <header className="smifs-admin-tabhead">
        <div>
          <h2 className="smifs-admin-tabtitle">Widget</h2>
          <p className="smifs-admin-tabsubtitle">Configure the embed snippet your website team pastes onto smifs.com.</p>
        </div>
        <div className="smifs-widget-actions">
          <button type="button" className="smifs-btn smifs-btn--ghost" onClick={reset} data-testid="widget-reset-button"><RotateCcw size={14}/> Reset</button>
          <button type="button" className="smifs-btn smifs-btn--primary" disabled={saving || !validation.ok} onClick={save} data-testid="widget-save-button"><Save size={14}/> {saving ? "Saving…" : "Save changes"}</button>
        </div>
      </header>

      <section className="smifs-widget-snippet" data-testid="widget-snippet">
        <p className="smifs-eyebrow">Embed snippet</p>
        <pre><code>{snippet}</code></pre>
        <button type="button" className="smifs-btn smifs-btn--ghost" onClick={copySnippet} data-testid="widget-snippet-copy">
          <Copy size={14}/> {copied ? "Copied!" : "Copy"}
        </button>
        <ol className="smifs-widget-steps">
          <li>Copy the snippet above.</li>
          <li>Paste it just before the closing <code>&lt;/body&gt;</code> tag on every page of smifs.com you want the bubble to appear on.</li>
          <li>(Optional) add <code>data-position="bottom-left"</code> to the script tag to flip sides.</li>
          <li>If your site domain is restricted, add it to the <strong>Allowed origins</strong> field below — blank means allow any origin.</li>
          <li>Save. The bubble re-fetches config every minute via <code>Cache-Control: max-age=60</code>.</li>
        </ol>
      </section>

      <div className="smifs-widget-grid">
        <div className="smifs-widget-form">
          <fieldset>
            <legend>Branding</legend>
            <label>Brand name<input type="text" value={draft.brand_name || ""} onChange={(e) => setField("brand_name", e.target.value)} maxLength={80} data-testid="widget-brand-name"/></label>
            <label>Subtitle<input type="text" value={draft.subtitle || ""} onChange={(e) => setField("subtitle", e.target.value)} maxLength={120} data-testid="widget-subtitle"/></label>
            <label>Welcome message<textarea value={draft.welcome_message || ""} onChange={(e) => setField("welcome_message", e.target.value)} maxLength={500} rows={3} data-testid="widget-welcome"/></label>
            <label>Bubble icon (emoji or short text)<input type="text" value={draft.bubble_icon || ""} onChange={(e) => setField("bubble_icon", e.target.value)} maxLength={8} style={{ width: 90 }} data-testid="widget-bubble-icon"/></label>
            <div className="smifs-widget-radio-row">
              <span className="smifs-form-label">Position</span>
              <label><input type="radio" name="position" value="bottom-right" checked={draft.position === "bottom-right"} onChange={(e) => setField("position", e.target.value)} data-testid="widget-position-br"/> Bottom right</label>
              <label><input type="radio" name="position" value="bottom-left"  checked={draft.position === "bottom-left"}  onChange={(e) => setField("position", e.target.value)} data-testid="widget-position-bl"/> Bottom left</label>
            </div>
            <label className="smifs-widget-checkbox">
              <input type="checkbox" checked={!!draft.show_branding_footer} onChange={(e) => setField("show_branding_footer", e.target.checked)} data-testid="widget-footer-toggle"/>
              Show "Powered by Mackertich ONE" footer
            </label>
          </fieldset>

          <fieldset>
            <legend>Colours</legend>
            <div className="smifs-widget-colors">
              {COLOR_FIELDS.map(([key, label, hint]) => (
                <div key={key} className="smifs-widget-color">
                  <label>
                    <span>{label}</span>
                    <span className="smifs-widget-color-hint">{hint}</span>
                  </label>
                  <div className="smifs-widget-color-controls">
                    <input type="color" value={draft.theme?.[key] || "#FFFFFF"} onChange={(e) => setTheme(key, e.target.value)} data-testid={`widget-color-${key}`}/>
                    <input type="text" value={draft.theme?.[key] || ""} onChange={(e) => setTheme(key, e.target.value)} maxLength={7} className="smifs-widget-hex"/>
                  </div>
                </div>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>Suggestion chips (max 5)</legend>
            {(draft.suggestion_chips || []).map((c, i) => (
              <div key={i} className="smifs-widget-chip-row">
                <input type="text" value={c} onChange={(e) => setChip(i, e.target.value)} maxLength={120} data-testid={`widget-chip-${i}`}/>
                <button type="button" className="smifs-btn smifs-btn--ghost smifs-btn--icon" onClick={() => removeChip(i)} aria-label="Remove chip"><X size={14}/></button>
              </div>
            ))}
            {(draft.suggestion_chips || []).length < 5 && (
              <button type="button" className="smifs-btn smifs-btn--ghost" onClick={addChip} data-testid="widget-add-chip"><Plus size={14}/> Add chip</button>
            )}
          </fieldset>

          <fieldset>
            <legend>CORS allowlist</legend>
            <label>Allowed origins (comma-separated, blank = allow any)
              <input type="text" value={allowedAsString} onChange={(e) => setField("allowed_origins", e.target.value)}
                     placeholder="https://smifs.com, https://www.smifs.com" data-testid="widget-allowed-origins"/>
            </label>
            <p className="smifs-text-muted">When set, requests to <code>/api/widget/config</code> from any other origin return 403.</p>
          </fieldset>
        </div>

        <div className="smifs-widget-preview" data-testid="widget-preview">
          <p className="smifs-eyebrow"><Eye size={12} style={{ verticalAlign: "-2px" }}/> Live preview</p>
          <div className="smifs-widget-mocksite" style={{ background: "#f6f4ee" }}>
            <div className="smifs-widget-mocksite-content">
              <p className="smifs-eyebrow">A pretend smifs.com page</p>
              <h3>Wealth that compounds, quietly.</h3>
              <p>Click the floating bubble (lower-{draft.position === "bottom-left" ? "left" : "right"}) to open the chat preview.</p>
            </div>
            {/* Inline rendered bubble using the in-progress draft theme */}
            <div
              className="smifs-widget-preview-bubble"
              data-testid="widget-preview-bubble"
              style={{
                background: draft.theme?.primary || "#0B1B2B",
                borderColor: draft.theme?.accent || "#C9A86A",
                [draft.position === "bottom-left" ? "left" : "right"]: 14,
              }}
            >{(draft.bubble_icon || "💬").slice(0, 2)}</div>
            <div
              className="smifs-widget-preview-bubble-text"
              style={{
                color: draft.theme?.header_text || "#FFFFFF",
                background: draft.theme?.header_bg || draft.theme?.primary || "#0B1B2B",
                [draft.position === "bottom-left" ? "left" : "right"]: 80,
              }}
            >
              <strong>{draft.brand_name || "Mackertich ONE"}</strong>
              <span>{draft.subtitle || ""}</span>
            </div>
          </div>
          <details className="smifs-widget-jsonpreview">
            <summary>JSON contract preview (what /api/widget/config returns to widget.js)</summary>
            <pre>{JSON.stringify({
              brand_name: draft.brand_name, subtitle: draft.subtitle, welcome_message: draft.welcome_message,
              bubble_icon: draft.bubble_icon, position: draft.position, theme: draft.theme,
              suggestion_chips: draft.suggestion_chips, show_branding_footer: draft.show_branding_footer,
            }, null, 2)}</pre>
          </details>
        </div>
      </div>
    </div>
  );
}
