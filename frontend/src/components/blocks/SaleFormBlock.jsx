import { useState } from "react";
import axios from "axios";
import { Loader2, CheckCircle2 } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;
const PAN_RE = /^[A-Z]{5}\d{4}[A-Z]$/;
const EMAIL_RE = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

const PRODUCT_LABEL = {
  mutual_fund: "Mutual Fund",
  aif: "AIF",
  pms: "PMS",
  fd: "Fixed Deposit",
  insurance: "Insurance",
};

const COMMON = [
  { key: "client_name",          label: "Client name",        type: "text",  required: true },
  { key: "client_pan",           label: "Client PAN",         type: "text",  required: true, placeholder: "ABCDE1234F" },
  { key: "client_phone",         label: "Client phone",       type: "tel",   required: true, placeholder: "10-digit" },
  { key: "client_email",         label: "Client email",       type: "email", required: true },
  { key: "amount_inr",           label: "Amount (₹)",         type: "number", required: true, min: 1000 },
  { key: "expected_login_date",  label: "Expected login date", type: "date", required: true },
  { key: "expected_payment_date",label: "Expected payment",   type: "date",  required: true },
  { key: "remarks",              label: "Remarks (optional)", type: "textarea" },
];

const AMC_OPTIONS = ["HDFC AMC","ICICI Prudential","SBI","Axis","Nippon","Mirae","Kotak","Aditya Birla SL","UTI","DSP","Other"];

const PRODUCT_FIELDS = {
  mutual_fund: [
    { key: "amc_name",            label: "AMC", type: "select", required: true, options: AMC_OPTIONS },
    { key: "scheme_name",         label: "Scheme name", type: "text", required: true },
    { key: "scheme_type",         label: "Scheme type", type: "radio", required: true,
      options: ["SIP", "Lump sum", "SWP", "STP"] },
    { key: "frequency",           label: "Frequency", type: "select",
      options: ["Monthly","Quarterly","Annually"],
      showIf: (v) => v.scheme_type && v.scheme_type !== "Lump sum" },
    { key: "folio_number",        label: "Folio number (existing)", type: "text" },
    { key: "arn_distributor_code",label: "ARN / Distributor code",  type: "text" },
  ],
  aif: [
    { key: "aif_name",                label: "AIF name", type: "text", required: true },
    { key: "category",                label: "Category", type: "radio", required: true,
      options: ["Cat I", "Cat II", "Cat III"] },
    { key: "commitment_amount_inr",   label: "Commitment amount (₹)", type: "number", required: true, min: 0 },
    { key: "drawdown_schedule",       label: "Drawdown schedule",   type: "textarea", required: true,
      placeholder: "e.g. 100% upfront, OR phased over 3 years (40 / 30 / 30)" },
    { key: "fund_manager",            label: "Fund manager", type: "text", required: true },
  ],
  pms: [
    { key: "pms_provider",        label: "PMS provider", type: "text", required: true },
    { key: "strategy_name",       label: "Strategy name", type: "text", required: true },
    { key: "corpus_inr",          label: "Corpus (₹)", type: "number", required: true, min: 5000000 },
    { key: "fee_structure",       label: "Fee structure", type: "radio", required: true,
      options: ["Fixed only", "Variable only", "Hybrid"] },
    { key: "fixed_fee_pct",       label: "Fixed fee %", type: "number", step: 0.01, min: 0, max: 10,
      showIf: (v) => v.fee_structure === "Fixed only" || v.fee_structure === "Hybrid" },
    { key: "performance_fee_pct", label: "Performance fee %", type: "number", step: 0.01, min: 0, max: 50,
      showIf: (v) => v.fee_structure === "Variable only" || v.fee_structure === "Hybrid" },
  ],
  fd: [
    { key: "issuer_name",      label: "Issuer (bank / NBFC)", type: "text", required: true },
    { key: "issuer_type",      label: "Issuer type", type: "radio", required: true,
      options: ["Bank", "NBFC", "Corporate FD"] },
    { key: "tenure_months",    label: "Tenure (months)", type: "number", required: true, min: 1, max: 120 },
    { key: "interest_rate_pct",label: "Interest rate (%)", type: "number", required: true, step: 0.01, min: 0, max: 15 },
    { key: "payout_frequency", label: "Payout frequency", type: "select", required: true,
      options: ["Monthly","Quarterly","Half-yearly","Annual","On maturity"] },
    { key: "fd_type",          label: "FD type", type: "radio", required: true,
      options: ["Cumulative", "Non-cumulative"] },
  ],
  insurance: [
    { key: "carrier",          label: "Carrier", type: "text", required: true, placeholder: "LIC, HDFC Life, …" },
    { key: "product_type",     label: "Product type", type: "radio", required: true,
      options: ["Term", "ULIP", "Endowment", "Money-back", "Health", "Annuity"] },
    { key: "policy_term_years",label: "Policy term (years)", type: "number", required: true, min: 1, max: 50 },
    { key: "premium_frequency",label: "Premium frequency", type: "select", required: true,
      options: ["Single","Annual","Half-yearly","Quarterly","Monthly"] },
    { key: "sum_assured_inr",  label: "Sum assured (₹)", type: "number", required: true, min: 0 },
  ],
};

function todayPlus(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export default function SaleFormBlock({ data, sessionId, onSubmitted, disabled }) {
  const product = (data && data.product) || "mutual_fund";
  const productFields = PRODUCT_FIELDS[product] || [];

  const [values, setValues] = useState({
    expected_login_date: todayPlus(2),
    expected_payment_date: todayPlus(5),
  });
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [submitErr, setSubmitErr] = useState("");

  const set = (k, v) => {
    setValues((s) => ({ ...s, [k]: v }));
    setErrors((s) => ({ ...s, [k]: null }));
  };

  const validate = () => {
    const e = {};
    for (const f of COMMON) {
      if (f.required && (!values[f.key] || String(values[f.key]).trim() === "")) {
        e[f.key] = "Required.";
      }
    }
    if (values.client_pan) {
      const p = values.client_pan.toUpperCase().replace(/[\s-]/g, "");
      if (!PAN_RE.test(p)) e.client_pan = "PAN must be ABCDE1234F.";
    }
    if (values.client_email && !EMAIL_RE.test(values.client_email)) {
      e.client_email = "Provide a valid email.";
    }
    if (values.client_phone && (("" + values.client_phone).replace(/\D/g, "").length < 10)) {
      e.client_phone = "Phone must be at least 10 digits.";
    }
    if (values.amount_inr && Number(values.amount_inr) < 1000) e.amount_inr = "Min ₹1,000.";
    if (values.expected_login_date && values.expected_payment_date
        && values.expected_payment_date < values.expected_login_date) {
      e.expected_payment_date = "Must be on/after login date.";
    }
    for (const f of productFields) {
      if (f.required && (f.showIf ? f.showIf(values) : true)
          && (!values[f.key] || String(values[f.key]).trim() === "")) {
        e[f.key] = "Required.";
      }
    }
    return e;
  };

  const submit = async (ev) => {
    ev.preventDefault();
    const e = validate();
    setErrors(e);
    if (Object.keys(e).length) return;
    setSubmitting(true);
    setSubmitErr("");
    try {
      const cleaned = {
        ...values,
        client_pan: values.client_pan.toUpperCase().replace(/[\s-]/g, ""),
        client_phone: ("" + values.client_phone).replace(/\D/g, "").slice(-10),
      };
      const { data: resp } = await axios.post(`${API}/sales`, {
        form_type: product,
        session_id: sessionId,
        fields: cleaned,
      });
      onSubmitted && onSubmitted(resp);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      if (detail && Array.isArray(detail.errors)) {
        const fieldErrors = {};
        for (const e of detail.errors) fieldErrors[e.field] = e.error;
        setErrors(fieldErrors);
      } else {
        setSubmitErr(typeof detail === "string" ? detail : "Submission failed. Try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const renderField = (f) => {
    if (f.showIf && !f.showIf(values)) return null;
    const err = errors[f.key];
    const id = `sale-${f.key}`;
    const common = {
      id, "data-testid": id,
      value: values[f.key] ?? "",
      onChange: (e) => set(f.key, e.target.value),
      disabled: disabled || submitting,
      placeholder: f.placeholder || "",
      className: `smifs-sale-form__input ${err ? "is-error" : ""}`,
    };
    let control;
    if (f.type === "textarea") {
      control = <textarea rows={3} {...common} />;
    } else if (f.type === "select") {
      control = (
        <select {...common}>
          <option value="">— select —</option>
          {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      );
    } else if (f.type === "radio") {
      control = (
        <div className="smifs-sale-form__radio-row">
          {f.options.map((o) => (
            <label key={o} className={`smifs-sale-form__radio ${values[f.key] === o ? "is-active" : ""}`}>
              <input type="radio" name={f.key} value={o} checked={values[f.key] === o}
                     onChange={() => set(f.key, o)} disabled={disabled || submitting} />
              {o}
            </label>
          ))}
        </div>
      );
    } else {
      control = <input type={f.type || "text"} step={f.step} min={f.min} max={f.max} {...common} />;
    }
    return (
      <div key={f.key} className="smifs-sale-form__field">
        <label htmlFor={id} className="smifs-sale-form__label">
          {f.label}{f.required ? " *" : ""}
        </label>
        {control}
        {err && <div className="smifs-sale-form__err">{err}</div>}
      </div>
    );
  };

  return (
    <form className="smifs-block smifs-sale-form" onSubmit={submit} data-testid="sale-form-block">
      <div className="smifs-sale-form__heading">
        Log a new <span>{PRODUCT_LABEL[product] || product}</span> sale
      </div>

      <div className="smifs-sale-form__section">Client details</div>
      <div className="smifs-sale-form__grid">
        {COMMON.slice(0, 4).map(renderField)}
      </div>

      <div className="smifs-sale-form__section">{PRODUCT_LABEL[product]} specifics</div>
      <div className="smifs-sale-form__grid">
        {productFields.map(renderField)}
      </div>

      <div className="smifs-sale-form__section">Commercials &amp; timeline</div>
      <div className="smifs-sale-form__grid">
        {COMMON.slice(4).map(renderField)}
      </div>

      {submitErr && (
        <div className="smifs-sale-form__alert" data-testid="sale-form-error">{submitErr}</div>
      )}

      <div className="smifs-sale-form__actions">
        <button type="submit" disabled={submitting || disabled}
                data-testid="sale-form-submit" className="smifs-sale-form__submit">
          {submitting ? <Loader2 size={16} className="animate-spin"/> : <CheckCircle2 size={16}/>}
          {submitting ? "Submitting…" : "Submit sale"}
        </button>
      </div>
    </form>
  );
}
