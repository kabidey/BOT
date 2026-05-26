import { useState } from "react";
import axios from "axios";
import { Check, Send, Star, AlertTriangle } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

/**
 * Phase 26c — Dynamic form renderer.
 *
 * Renders any of the 5 form types (demand_capture, referral_capture,
 * feedback_capture, complaint_capture, callback_request) from a JSON
 * schema. Field types: text, email, tel, textarea, select, rating.
 *
 * Submits to /api/forms/submit, then locks the card with the
 * server-returned success_message.
 */
export default function DynamicFormBlock({ block, sessionId, msgIdx }) {
  const fields = Array.isArray(block?.fields) ? block.fields : [];
  const formId = block?.form_id || "form";
  const isComplaint = formId === "complaint_capture";

  const [values, setValues] = useState(() => {
    const init = {};
    for (const f of fields) init[f.name] = f.default || "";
    return init;
  });
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(null);

  const setField = (name, val) => {
    setValues((prev) => ({ ...prev, [name]: val }));
    if (errors[name]) setErrors((prev) => ({ ...prev, [name]: undefined }));
  };

  const validate = () => {
    const errs = {};
    for (const f of fields) {
      const v = (typeof values[f.name] === "string" ? values[f.name] : String(values[f.name] ?? "")).trim();
      if (f.required && !v) { errs[f.name] = "Required"; continue; }
      if (v && f.type === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) errs[f.name] = "Invalid email";
      if (v && f.type === "tel" && !/^[+0-9 \-]{10,18}$/.test(v)) errs[f.name] = "Invalid phone";
      if (f.minLength && v.length < f.minLength) errs[f.name] = `At least ${f.minLength} characters`;
    }
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const submit = async (e) => {
    e?.preventDefault();
    if (!validate() || submitting) return;
    setSubmitting(true);
    try {
      const { data } = await axios.post(`${API}/forms/submit`, {
        form_id: formId,
        form_data: values,
        session_id: sessionId,
        context: block?.context || {},
      });
      setDone({
        submission_id: data.submission_id,
        message: data.message || block?.success_message || "Submitted.",
      });
    } catch (err) {
      const detail = err?.response?.data?.detail || err.message;
      setErrors({ __submit: detail });
    } finally {
      setSubmitting(false);
    }
  };

  if (!fields.length) return null;

  if (done) {
    return (
      <div className="smifs-form-card smifs-form-card--done" data-testid={`dynform-success-${formId}-${msgIdx}`}>
        <div className="smifs-form-success">
          <Check size={18} strokeWidth={2.5} />
          <div>
            <p className="smifs-form-success-title">Submitted</p>
            <p className="smifs-form-success-body">{done.message}</p>
            {done.submission_id && (
              <p className="smifs-form-success-id">Reference · {String(done.submission_id).slice(0, 8)}</p>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <form
      className={`smifs-form-card ${isComplaint ? "smifs-form-card--urgent" : ""}`}
      onSubmit={submit}
      data-testid={`dynform-${formId}-${msgIdx}`}
      data-form-id={formId}
    >
      <div className="smifs-form-head">
        <p className="smifs-form-eyebrow">
          {isComplaint
            ? <><AlertTriangle size={12} strokeWidth={2.5} /> Priority — Complaint</>
            : "Private inquiry"}
        </p>
        <h3 className="smifs-form-title">{block?.title || "Tell us a bit more"}</h3>
        {block?.subtitle && <p className="smifs-form-subtitle">{block.subtitle}</p>}
      </div>

      <div className="smifs-form-fields">
        {fields.map((f) => {
          const errKey = `dynform-${formId}-${f.name}-err`;
          const inputKey = `dynform-${formId}-${f.name}`;
          const errClass = errors[f.name] ? "smifs-form-input--err" : "";

          if (f.type === "select") {
            return (
              <label key={f.name} className="smifs-form-field">
                <span className="smifs-form-label">
                  {f.label}{f.required && <span className="smifs-form-req"> *</span>}
                </span>
                <select
                  className={`smifs-form-input ${errClass}`}
                  value={values[f.name] || ""}
                  onChange={(e) => setField(f.name, e.target.value)}
                  data-testid={inputKey}
                >
                  <option value="">Select…</option>
                  {(f.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
                {errors[f.name] && <span className="smifs-form-err" data-testid={errKey}>{errors[f.name]}</span>}
              </label>
            );
          }

          if (f.type === "textarea") {
            return (
              <label key={f.name} className="smifs-form-field">
                <span className="smifs-form-label">
                  {f.label}{f.required && <span className="smifs-form-req"> *</span>}
                </span>
                <textarea
                  rows={f.rows || 3}
                  className={`smifs-form-input smifs-form-textarea ${errClass}`}
                  value={values[f.name] || ""}
                  onChange={(e) => setField(f.name, e.target.value)}
                  placeholder={f.placeholder || ""}
                  data-testid={inputKey}
                />
                {errors[f.name] && <span className="smifs-form-err" data-testid={errKey}>{errors[f.name]}</span>}
              </label>
            );
          }

          if (f.type === "rating") {
            const max = f.max || 5;
            const current = parseInt(values[f.name] || "0", 10);
            return (
              <label key={f.name} className="smifs-form-field">
                <span className="smifs-form-label">
                  {f.label}{f.required && <span className="smifs-form-req"> *</span>}
                </span>
                <div className="smifs-form-rating" data-testid={inputKey}>
                  {Array.from({ length: max }, (_, i) => i + 1).map((n) => (
                    <button
                      key={n}
                      type="button"
                      className={`smifs-form-star ${n <= current ? "smifs-form-star--on" : ""}`}
                      onClick={() => setField(f.name, String(n))}
                      data-testid={`${inputKey}-${n}`}
                      aria-label={`${n} star`}
                    >
                      <Star size={20} strokeWidth={2} fill={n <= current ? "currentColor" : "none"} />
                    </button>
                  ))}
                </div>
                {errors[f.name] && <span className="smifs-form-err" data-testid={errKey}>{errors[f.name]}</span>}
              </label>
            );
          }

          // text, email, tel — generic input
          const inputType = f.type === "email" ? "email" : f.type === "tel" ? "tel" : "text";
          return (
            <label key={f.name} className="smifs-form-field">
              <span className="smifs-form-label">
                {f.label}{f.required && <span className="smifs-form-req"> *</span>}
              </span>
              <input
                type={inputType}
                className={`smifs-form-input ${errClass}`}
                value={values[f.name] || ""}
                onChange={(e) => setField(f.name, e.target.value)}
                placeholder={f.placeholder || ""}
                data-testid={inputKey}
                autoComplete="off"
              />
              {errors[f.name] && <span className="smifs-form-err" data-testid={errKey}>{errors[f.name]}</span>}
            </label>
          );
        })}
      </div>

      {errors.__submit && <div className="smifs-form-err smifs-form-err--block">{errors.__submit}</div>}

      <div className="smifs-form-actions">
        <button
          type="submit"
          className={`smifs-form-submit ${isComplaint ? "smifs-form-submit--urgent" : ""}`}
          disabled={submitting}
          data-testid={`dynform-${formId}-submit`}
        >
          {submitting ? "Submitting…" : (block?.submit_label || "Submit")}
          <Send size={14} strokeWidth={2.25} />
        </button>
      </div>
    </form>
  );
}
