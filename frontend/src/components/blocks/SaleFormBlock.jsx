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
  ncd_primary: "NCD Primary Issue",
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
  ncd_primary: [
    { key: "issuer_name",            label: "Issuer / Issue name", type: "text", required: true,
      placeholder: "e.g. Muthoot Finance NCD Tranche IV" },
    { key: "series_option",          label: "Series / Option", type: "text", required: true,
      placeholder: "e.g. Series III — 5Y Quarterly" },
    { key: "application_amount_inr", label: "Application amount (₹)", type: "number",
      required: true, min: 10000, step: 1000,
      helper: "Multiple of ₹1,000 — NCDs are issued in ₹1,000 face-value lots." },
    { key: "number_of_ncds",         label: "Number of NCDs", type: "computed",
      from: "application_amount_inr",
      compute: (v) => (v && Number(v) > 0 && Number(v) % 1000 === 0)
        ? String(Math.floor(Number(v) / 1000)) : "—",
      helper: "Auto-computed (application amount ÷ ₹1,000)." },
    { key: "coupon_rate_pct",        label: "Coupon rate (% p.a.)", type: "number",
      required: true, min: 1, max: 20, step: 0.01 },
    { key: "tenure_years",           label: "Tenure (years)", type: "number",
      required: true, min: 1, max: 15 },
    { key: "interest_frequency",     label: "Interest payment frequency", type: "select",
      required: true,
      options: ["Monthly","Quarterly","Annual","Cumulative"] },
    { key: "asba_upi_reference",     label: "ASBA / UPI reference", type: "text",
      placeholder: "(optional)" },
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
    setValues((s) => {
      const next = { ...s, [k]: v };
      // Phase 15 — NCD: keep the common amount_inr in sync with the
      // product-specific application_amount_inr so validate() passes the
      // common-block min ₹1,000 check off a single user input.
      if (product === "ncd_primary" && k === "application_amount_inr") {
        next.amount_inr = v;
      }
      return next;
    });
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
      // Phase 15 — NCD primary issue: the product-specific
      // `application_amount_inr` IS the common `amount_inr`. Mirror it so
      // the user only types the amount once. Also persist the computed
      // number of NCDs into the payload (server re-computes it anyway).
      if (product === "ncd_primary" && cleaned.application_amount_inr) {
        const n = Number(cleaned.application_amount_inr);
        cleaned.amount_inr = n;
        cleaned.number_of_ncds = (n > 0 && n % 1000 === 0) ? Math.floor(n / 1000) : undefined;
      }
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
    } else if (f.type === "computed") {
      // Read-only computed field — derives its value from another input.
      const src = values[f.from];
      const computed = f.compute ? f.compute(src) : (src ?? "");
      control = (
        <input type="text" readOnly value={computed}
               id={id} data-testid={id}
               className="smifs-sale-form__input is-readonly"
               disabled={disabled || submitting} />
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
        {f.helper && !err && <div className="smifs-sale-form__helper">{f.helper}</div>}
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
