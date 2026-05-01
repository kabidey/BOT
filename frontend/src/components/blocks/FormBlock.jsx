import { useState } from "react";
import axios from "axios";
import { Check, Send } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

export default function FormBlock({ block, sessionId, msgIdx }) {
  const schema = block?.schema;
  // Phase 11 bug-1 fix — defensive: if a malformed/partial form block
  // slips through (e.g. during a stop-mid-stream), tolerate a missing
  // schema instead of crashing the tree.
  const fields = Array.isArray(schema?.fields) ? schema.fields : [];
  const [values, setValues] = useState(() => {
    const init = {};
    for (const f of fields) init[f.name] = "";
    return init;
  });
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(null); // { lead_id, message } | null

  const setField = (name, val) => {
    setValues((prev) => ({ ...prev, [name]: val }));
    if (errors[name]) setErrors((prev) => ({ ...prev, [name]: undefined }));
  };

  const validate = () => {
    const errs = {};
    for (const f of fields) {
      const v = (values[f.name] ?? "").trim();
      if (f.required && !v) {
        errs[f.name] = "Required";
        continue;
      }
      if (v && f.pattern) {
        try {
          if (!new RegExp(f.pattern).test(v)) errs[f.name] = "Invalid format";
        } catch (_) { /* ignore bad regex */ }
      }
      if (v && f.type === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) {
        errs[f.name] = "Invalid email";
      }
    }
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const submit = async (e) => {
    e?.preventDefault();
    if (!validate() || submitting) return;
    setSubmitting(true);
    try {
      const { data } = await axios.post(`${API}/leads`, {
        form_type: schema?.form_type,
        fields: values,
        context: schema?.context || {},
        session_id: sessionId,
      });
      setDone({ lead_id: data.lead_id, message: data.message });
    } catch (err) {
      const detail = err?.response?.data?.detail || err.message;
      setErrors({ __submit: detail });
    } finally {
      setSubmitting(false);
    }
  };

  // Render nothing if we never received a valid schema.
  if (!schema || fields.length === 0) return null;

  if (done) {
    return (
      <div className="smifs-form-card smifs-form-card--done" data-testid={`form-success-${msgIdx}`}>
        <div className="smifs-form-success">
          <Check size={18} strokeWidth={2.5} />
          <div>
            <p className="smifs-form-success-title">Request received</p>
            <p className="smifs-form-success-body">{done.message}</p>
            <p className="smifs-form-success-id">Reference · {done.lead_id.slice(0, 8)}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <form
      className="smifs-form-card"
      onSubmit={submit}
      data-testid={`form-block-${msgIdx}`}
      data-form-type={schema?.form_type}
    >
      <div className="smifs-form-head">
        <p className="smifs-form-eyebrow">Private inquiry</p>
        <h3 className="smifs-form-title">{schema?.title || "Request a callback"}</h3>
        {schema?.subtitle && <p className="smifs-form-subtitle">{schema.subtitle}</p>}
      </div>

      <div className="smifs-form-fields">
        {fields.map((f) => (
          <label key={f.name} className="smifs-form-field">
            <span className="smifs-form-label">
              {f.label}
              {f.required && <span className="smifs-form-req"> *</span>}
            </span>
            {f.type === "select" ? (
              <select
                className={`smifs-form-input ${errors[f.name] ? "smifs-form-input--err" : ""}`}
                value={values[f.name] || ""}
                onChange={(e) => setField(f.name, e.target.value)}
                data-testid={`form-${schema?.form_type}-${f.name}`}
              >
                <option value="">Select…</option>
                {(f.options || []).map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
            ) : (
              <input
                type={f.type === "tel" ? "tel" : f.type === "email" ? "email" : "text"}
                className={`smifs-form-input ${errors[f.name] ? "smifs-form-input--err" : ""}`}
                value={values[f.name] || ""}
                onChange={(e) => setField(f.name, e.target.value)}
                placeholder={f.placeholder || ""}
                data-testid={`form-${schema?.form_type}-${f.name}`}
                autoComplete="off"
              />
            )}
            {errors[f.name] && (
              <span className="smifs-form-err" data-testid={`form-${schema?.form_type}-${f.name}-err`}>
                {errors[f.name]}
              </span>
            )}
          </label>
        ))}
      </div>

      {errors.__submit && (
        <div className="smifs-form-err smifs-form-err--block">{errors.__submit}</div>
      )}

      <div className="smifs-form-actions">
        <button
          type="submit"
          className="smifs-form-submit"
          disabled={submitting}
          data-testid={`form-${schema?.form_type}-submit`}
        >
          {submitting ? "Submitting…" : (schema?.submit_label || "Submit")}
          <Send size={14} strokeWidth={2.25} />
        </button>
        {schema?.context?.asset_class && (
          <span className="smifs-form-tag">Re: {schema.context.asset_class}</span>
        )}
      </div>
    </form>
  );
}
