import { useState, useEffect, useMemo, useRef } from "react";
import axios from "axios";
import { Loader2, CheckCircle2, Star, ChevronDown, X as XIcon, Repeat } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;
const PAN_RE = /^[A-Z]{5}\d{4}[A-Z]$/;
const EMAIL_RE = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;
const ARN_RE = /^ARN-[A-Za-z0-9]{4,7}$|^[A-Za-z0-9]{4,7}$/;

const PRODUCT_LABEL = {
  mutual_fund: "Mutual Fund",
  aif: "AIF",
  pms: "PMS",
  fd: "Fixed Deposit",
  insurance: "Insurance",
  ncd_primary: "NCD Primary Issue",
  sif: "SIF",
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

// Phase 21 — Field-cleanup pass + SIF + extended ARN/APRN transfer:
//   • MF standard: dropped `folio_number`, `arn_distributor_code`.
//   • AIF: dropped `category`, `drawdown_schedule`, `fund_manager`.
//   • PMS: dropped `fee_structure`, `fixed_fee_pct`, `performance_fee_pct`.
//   • FD: dropped `interest_rate_pct`.
//   • Insurance: `product_type` flipped radio → free-text; added
//     `premium_paying_term_years` + `premium_amount_inr`.
//   • NCD Primary: dropped `coupon_rate_pct`, `tenure_years`.
//   • NEW product `sif` (Specialised Investment Fund).
// Old sales rows keep their dropped fields in Mongo `product_details` — the
// admin drawer surfaces them under a "Legacy fields" collapsible.
const PRODUCT_FIELDS = {
  mutual_fund: [
    { key: "amc_name",            label: "AMC", type: "text", required: true, lockedByVehicle: true },
    { key: "scheme_name",         label: "Scheme name", type: "text", required: true, lockedByVehicle: true },
    { key: "scheme_type",         label: "Scheme type", type: "radio", required: true,
      options: ["SIP", "Lump sum", "SWP", "STP"] },
    { key: "frequency",           label: "Frequency", type: "select",
      options: ["Monthly","Quarterly","Annually"],
      showIf: (v) => v.scheme_type && v.scheme_type !== "Lump sum" },
  ],
  aif: [
    { key: "aif_name",                label: "AIF name", type: "text", required: true, lockedByVehicle: true },
    { key: "commitment_amount_inr",   label: "Commitment amount (₹)", type: "number", required: true, min: 0 },
  ],
  pms: [
    { key: "pms_provider",        label: "PMS provider", type: "text", required: true, lockedByVehicle: true },
    { key: "strategy_name",       label: "Strategy name", type: "text", required: true, lockedByVehicle: true },
    { key: "corpus_inr",          label: "Corpus (₹)", type: "number", required: true, min: 5000000 },
  ],
  fd: [
    { key: "issuer_name",      label: "Issuer (bank / NBFC)", type: "text", required: true, lockedByVehicle: true },
    { key: "issuer_type",      label: "Issuer type", type: "radio", required: true,
      options: ["Bank", "NBFC", "Corporate FD"] },
    { key: "tenure_months",    label: "Tenure (months)", type: "number", required: true, min: 1, max: 120 },
    { key: "payout_frequency", label: "Payout frequency", type: "select", required: true,
      options: ["Monthly","Quarterly","Half-yearly","Annual","On maturity"] },
    { key: "fd_type",          label: "FD type", type: "radio", required: true,
      options: ["Cumulative", "Non-cumulative"] },
  ],
  insurance: [
    { key: "carrier",          label: "Carrier", type: "text", required: true, lockedByVehicle: true, placeholder: "LIC, HDFC Life, …" },
    // Phase 21 — free-text (was radio with 6 fixed options).
    { key: "product_type",     label: "Product type", type: "text", required: true,
      placeholder: "Term, ULIP, Endowment, Health, Annuity, Custom hybrid plan, …" },
    { key: "policy_term_years",        label: "Policy term (years)", type: "number", required: true, min: 1, max: 50,
      helper: "Total duration the policy stays in force." },
    { key: "premium_paying_term_years",label: "Premium paying term (years)", type: "number", required: true, min: 1, max: 50,
      helper: "Years the client actually pays premium (may be shorter than policy term)." },
    { key: "premium_frequency",label: "Premium frequency", type: "select", required: true,
      options: ["Single","Annual","Half-yearly","Quarterly","Monthly"] },
    { key: "premium_amount_inr",label: "Premium amount (₹)", type: "number", required: true, min: 0,
      helper: "Per-period premium — separate from the sum assured." },
    { key: "sum_assured_inr",  label: "Sum assured (₹)", type: "number", required: true, min: 0 },
  ],
  ncd_primary: [
    { key: "issuer_name",            label: "Issuer / Issue name", type: "text", required: true, lockedByVehicle: true,
      placeholder: "auto-filled from picked vehicle" },
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
    { key: "interest_frequency",     label: "Interest payment frequency", type: "select",
      required: true,
      options: ["Monthly","Quarterly","Annual","Cumulative"] },
    { key: "asba_upi_reference",     label: "ASBA / UPI reference", type: "text",
      placeholder: "(optional)" },
  ],
  sif: [
    { key: "sif_name",         label: "SIF name", type: "text", required: true, lockedByVehicle: true,
      placeholder: "auto-filled from picked vehicle" },
    { key: "strategy_theme",   label: "Strategy / theme", type: "text", required: true,
      placeholder: "e.g. Long-Short Equity, Quant Multi-Cap, Sector Rotation" },
    { key: "investment_type",  label: "Investment type", type: "radio", required: true,
      options: ["Lump sum", "Staggered (SIP-equivalent)", "Open-ended subscription"] },
    { key: "frequency",        label: "Frequency", type: "select",
      options: ["Monthly","Quarterly","Annually"],
      showIf: (v) => v.investment_type === "Staggered (SIP-equivalent)" },
    { key: "lock_in_months",   label: "Lock-in period (months)", type: "number", min: 0, max: 120,
      helper: "Optional. 0 if open-ended." },
  ],
};

function todayPlus(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

// Phase 17 — auto-fill mapping. Phase 21 — added `sif`.
const VEHICLE_AUTOFILL_BY_PRODUCT = {
  mutual_fund: ["amc_name", "scheme_name"],
  aif:         ["aif_name"],
  pms:         ["pms_provider", "strategy_name"],
  fd:          ["issuer_name"],
  insurance:   ["carrier"],
  ncd_primary: ["issuer_name"],
  sif:         ["sif_name"],
};

// ARN/APRN Transfer field defs. Phase 21 simplified MF ARN (dropped existing/
// new ARN codes + transfer effective date) and added AIF/SIF/PMS variants.
// All four sub-flows share the locked-identity-+-account-id-+-amount-+-remarks
// shape so the renderer can swap them in/out cleanly on toggle.
const ARN_FIELDS = [   // MF folio transfer
  { key: "folio_numbers", label: "Folio number(s)", type: "text", required: true,
    placeholder: "comma-separated folios" },
  { key: "amc_name",      label: "AMC name (locked)", type: "text", required: true, lockedByVehicle: true },
  { key: "scheme_name",   label: "Scheme name (locked)", type: "text", required: true, lockedByVehicle: true },
  { key: "aum_inr",       label: "AUM being transferred (₹)", type: "number", required: true, min: 1000 },
  { key: "arn_remarks",   label: "Remarks (optional)", type: "textarea" },
];

const AIF_ARN_FIELDS = [
  { key: "aif_name",               label: "AIF name (locked)", type: "text", required: true, lockedByVehicle: true },
  { key: "commitment_account_id",  label: "Commitment account ID", type: "text", required: true },
  { key: "aum_inr",                label: "AUM being transferred (₹)", type: "number", required: true, min: 1000 },
  { key: "arn_remarks",            label: "Remarks (optional)", type: "textarea" },
];

const SIF_ARN_FIELDS = [
  { key: "sif_name",         label: "SIF name (locked)", type: "text", required: true, lockedByVehicle: true },
  { key: "folio_account_id", label: "Folio / account ID", type: "text", required: true },
  { key: "aum_inr",          label: "AUM being transferred (₹)", type: "number", required: true, min: 1000 },
  { key: "arn_remarks",      label: "Remarks (optional)", type: "textarea" },
];

const PMS_APRN_FIELDS = [
  { key: "pms_provider",         label: "PMS provider (locked)", type: "text", required: true, lockedByVehicle: true },
  { key: "strategy_name",        label: "Strategy name (locked)", type: "text", required: true, lockedByVehicle: true },
  { key: "portfolio_account_id", label: "Portfolio account ID", type: "text", required: true },
  { key: "corpus_inr",           label: "Corpus being transferred (₹)", type: "number", required: true, min: 1000 },
  { key: "aprn_remarks",         label: "Remarks (optional)", type: "textarea" },
];

// Which products expose which sub-flow toggle.
const SUBFLOW_BY_PRODUCT = {
  mutual_fund: { kind: "arn",  label: "ARN Transfer",  fields: ARN_FIELDS,     testidToggle: "mf-arn-transfer-toggle"   },
  aif:         { kind: "arn",  label: "ARN Transfer",  fields: AIF_ARN_FIELDS, testidToggle: "aif-arn-transfer-toggle"  },
  sif:         { kind: "arn",  label: "ARN Transfer",  fields: SIF_ARN_FIELDS, testidToggle: "sif-arn-transfer-toggle"  },
  pms:         { kind: "aprn", label: "APRN Transfer", fields: PMS_APRN_FIELDS, testidToggle: "pms-aprn-transfer-toggle" },
};

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

  // ----- Phase 17 — deck-pegged vehicle picker (stage 2) -----
  const [catalogBucket, setCatalogBucket] = useState(null); // null = loading, [] = empty deck
  const [catalogErr, setCatalogErr] = useState("");
  const [selectedVehicle, setSelectedVehicle] = useState(null);
  const [pickerQuery, setPickerQuery] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef(null);

  // Sub-flow toggle (Phase 17 MF; Phase 21 extended to AIF/SIF/PMS). When
  // active, swap the body to the appropriate transfer fields. `subFlowKind`
  // mirrors the backend subtype: "arn" for MF/AIF/SIF, "aprn" for PMS.
  const [transferActive, setTransferActive] = useState(false);
  const subFlowDef = SUBFLOW_BY_PRODUCT[product] || null;
  const isTransferFlow = !!(subFlowDef && transferActive);
  const isArnFlow = isTransferFlow; // legacy alias (used in a few sites below)
  const subFlowFields = subFlowDef ? subFlowDef.fields : [];

  // Fetch deck catalog once per session/product mount.
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    (async () => {
      try {
        const { data: resp } = await axios.get(
          `${API}/sales/catalog`, { params: { session_id: sessionId } },
        );
        if (cancelled) return;
        const bucket = (resp && resp.buckets && resp.buckets[product]) || [];
        setCatalogBucket(bucket);
      } catch (e) {
        if (cancelled) return;
        setCatalogErr(e?.response?.data?.detail || "Failed to load vehicle deck.");
        setCatalogBucket([]);
      }
    })();
    return () => { cancelled = true; };
  }, [sessionId, product]);

  // Close picker dropdown on outside click.
  useEffect(() => {
    function onClick(e) {
      if (!pickerRef.current) return;
      if (!pickerRef.current.contains(e.target)) setPickerOpen(false);
    }
    if (pickerOpen) document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [pickerOpen]);

  const filteredVehicles = useMemo(() => {
    if (!catalogBucket) return [];
    const q = pickerQuery.trim().toLowerCase();
    if (!q) return catalogBucket;
    return catalogBucket.filter((v) => (v.vehicle_name || "").toLowerCase().includes(q));
  }, [catalogBucket, pickerQuery]);

  // When a vehicle is picked, auto-fill the product-specific identity field(s)
  // and lock them (renderField checks `lockedKeys` for read-only state).
  const lockedKeys = useMemo(() => {
    if (!selectedVehicle) return new Set();
    const ks = VEHICLE_AUTOFILL_BY_PRODUCT[product] || [];
    return new Set(ks);
  }, [product, selectedVehicle]);

  const onPickVehicle = (v) => {
    setSelectedVehicle(v);
    setPickerOpen(false);
    setPickerQuery(v.vehicle_name);
    // Push auto-fill values into every locked identity field for this product.
    const autofillKeys = VEHICLE_AUTOFILL_BY_PRODUCT[product] || [];
    setValues((s) => {
      const next = { ...s, vehicle_id: v.vehicle_id, vehicle_name: v.vehicle_name };
      for (const k of autofillKeys) next[k] = v.vehicle_name;
      return next;
    });
    setErrors((s) => ({ ...s, vehicle_id: null }));
  };

  const clearVehicle = () => {
    setSelectedVehicle(null);
    setPickerQuery("");
    setValues((s) => {
      const next = { ...s, vehicle_id: undefined, vehicle_name: undefined };
      const autofillKeys = VEHICLE_AUTOFILL_BY_PRODUCT[product] || [];
      for (const k of autofillKeys) next[k] = "";
      return next;
    });
  };

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
    // Phase 17 — vehicle pick is mandatory when the deck has rows. If the
    // bucket is empty, the form is blocked entirely (separate guard below).
    if (catalogBucket && catalogBucket.length > 0 && !values.vehicle_id) {
      e.vehicle_id = "Pick a vehicle from the deck.";
    }
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
    if (isTransferFlow) {
      // Transfer-branch validation (product-specific transfer field set).
      for (const f of subFlowFields) {
        if (f.required && (!values[f.key] || String(values[f.key]).trim() === "")) {
          e[f.key] = "Required.";
        }
      }
      // Per-flow numeric minimums.
      const aum = values.aum_inr ?? values.corpus_inr;
      if (subFlowDef.kind === "arn"  && values.aum_inr && Number(values.aum_inr) < 1000) e.aum_inr = "Min ₹1,000.";
      if (subFlowDef.kind === "aprn" && values.corpus_inr && Number(values.corpus_inr) < 1000) e.corpus_inr = "Min ₹1,000.";
    } else {
      for (const f of productFields) {
        if (f.required && (f.showIf ? f.showIf(values) : true)
            && (!values[f.key] || String(values[f.key]).trim() === "")) {
          e[f.key] = "Required.";
        }
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
      // Phase 17 / 21 — Transfer sub-flows. Pack the transfer-specific keys
      // into a nested object (`arn_transfer_fields` for ARN family,
      // `aprn_transfer_fields` for PMS) so the backend validators pick them
      // up cleanly. AUM/Corpus is mirrored into the common `amount_inr`
      // for the min ₹1,000 check.
      if (isTransferFlow) {
        const subKeys = subFlowFields.map((f) => f.key);
        const subPayload = {};
        for (const k of subKeys) subPayload[k] = values[k] ?? "";
        // Coerce numeric fields.
        if ("aum_inr"    in subPayload) subPayload.aum_inr    = Number(values.aum_inr || 0);
        if ("corpus_inr" in subPayload) subPayload.corpus_inr = Number(values.corpus_inr || 0);
        if (subFlowDef.kind === "arn") {
          cleaned.arn_transfer = true;
          cleaned.arn_transfer_fields = subPayload;
          cleaned.amount_inr = Number(values.aum_inr || 0);
        } else {
          cleaned.aprn_transfer = true;
          cleaned.aprn_transfer_fields = subPayload;
          cleaned.amount_inr = Number(values.corpus_inr || 0);
        }
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
    // Phase 17 — vehicle-driven auto-fill locks specific fields read-only.
    const locked = !!f.lockedByVehicle || lockedKeys.has(f.key);
    const common = {
      id, "data-testid": id,
      value: values[f.key] ?? "",
      onChange: (e) => set(f.key, e.target.value),
      disabled: disabled || submitting,
      readOnly: locked,
      placeholder: f.placeholder || "",
      className: `smifs-sale-form__input ${err ? "is-error" : ""} ${locked ? "is-readonly" : ""}`,
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

  // Picker UX
  const renderPicker = () => {
    if (catalogBucket === null) {
      return (
        <div className="smifs-sale-form__picker smifs-sale-form__picker--loading"
             data-testid="vehicle-picker-loading">
          <Loader2 size={14} className="animate-spin"/> Loading deck…
        </div>
      );
    }
    if (catalogBucket.length === 0) {
      return (
        <div className="smifs-sale-form__picker smifs-sale-form__picker--empty"
             data-testid="vehicle-picker-empty">
          <strong>No {PRODUCT_LABEL[product]} in current deck.</strong>
          {" "}Contact your RM / Sales Ops to add one before logging this sale.
          {catalogErr && <div className="smifs-sale-form__err">{catalogErr}</div>}
        </div>
      );
    }
    return (
      <div className="smifs-sale-form__picker" ref={pickerRef} data-testid="vehicle-picker">
        <label className="smifs-sale-form__label" htmlFor="vehicle-picker-input">Vehicle (deck-driven) *</label>
        <div className={`smifs-sale-form__picker-input ${errors.vehicle_id ? "is-error" : ""}`}>
          <input
            id="vehicle-picker-input"
            data-testid="vehicle-picker-input"
            type="text"
            value={pickerQuery}
            placeholder={`Search ${PRODUCT_LABEL[product]} vehicles…`}
            onFocus={() => setPickerOpen(true)}
            onChange={(e) => { setPickerQuery(e.target.value); setPickerOpen(true); if (selectedVehicle) clearVehicle(); }}
            disabled={disabled || submitting}
            autoComplete="off"
          />
          {selectedVehicle ? (
            <button type="button" className="smifs-sale-form__picker-clear"
                    data-testid="vehicle-picker-clear"
                    onClick={clearVehicle} title="Clear vehicle">
              <XIcon size={14}/>
            </button>
          ) : (
            <ChevronDown size={14} className="smifs-sale-form__picker-caret"
                         onClick={() => setPickerOpen((s) => !s)} />
          )}
        </div>
        {pickerOpen && (
          <ul className="smifs-sale-form__picker-list" data-testid="vehicle-picker-list">
            {filteredVehicles.length === 0 ? (
              <li className="smifs-sale-form__picker-empty-row">No matches.</li>
            ) : filteredVehicles.slice(0, 50).map((v) => {
              const isSelected = selectedVehicle && selectedVehicle.vehicle_id === v.vehicle_id;
              return (
                <li key={v.vehicle_id}
                    className={`smifs-sale-form__picker-row ${isSelected ? "is-selected" : ""}`}
                    data-testid={`vehicle-picker-option-${v.vehicle_id}`}
                    onClick={() => onPickVehicle(v)}>
                  {v.is_focused && <Star size={11} className="smifs-sale-form__picker-star" data-testid={`vehicle-focused-${v.vehicle_id}`}/>}
                  <span className="smifs-sale-form__picker-name">{v.vehicle_name}</span>
                  <span className="smifs-sale-form__picker-type">{v.vehicle_type}</span>
                </li>
              );
            })}
          </ul>
        )}
        {errors.vehicle_id && <div className="smifs-sale-form__err">{errors.vehicle_id}</div>}
        {selectedVehicle && (
          <div className="smifs-sale-form__picker-selected" data-testid="vehicle-picker-selected">
            <span>Selected: <strong>{selectedVehicle.vehicle_name}</strong> · {selectedVehicle.vehicle_type}
              {selectedVehicle.is_focused ? " · Focused" : ""}</span>
          </div>
        )}
      </div>
    );
  };

  const deckBlocked = catalogBucket !== null && catalogBucket.length === 0;

  return (
    <form className="smifs-block smifs-sale-form" onSubmit={submit} data-testid="sale-form-block">
      <div className="smifs-sale-form__heading">
        Log a new <span>{PRODUCT_LABEL[product] || product}</span> sale
      </div>

      {/* Phase 17 — stage 2: deck-pegged vehicle picker */}
      <div className="smifs-sale-form__section">Vehicle (from deck)</div>
      {renderPicker()}

      {/* Phase 17 / 21 — Transfer sub-flow toggle (MF/AIF/SIF → ARN; PMS → APRN). */}
      {subFlowDef && selectedVehicle && (
        <label className={`smifs-sale-form__arn-toggle ${subFlowDef.kind === "aprn" ? "is-aprn" : ""}`}
               data-testid={`${product}-${subFlowDef.kind}-transfer-row`}>
          <input type="checkbox" checked={transferActive}
                 data-testid={subFlowDef.testidToggle}
                 onChange={(e) => setTransferActive(e.target.checked)} />
          <Repeat size={14} strokeWidth={2.25}/>
          <span>This is an <strong>{subFlowDef.label}</strong></span>
        </label>
      )}

      <div className="smifs-sale-form__section">Client details</div>
      <div className="smifs-sale-form__grid">
        {COMMON.slice(0, 4).map(renderField)}
      </div>

      <div className="smifs-sale-form__section" data-testid="product-specifics-heading">
        {isTransferFlow ? `${subFlowDef.label} details` : `${PRODUCT_LABEL[product]} specifics`}
      </div>
      <div className="smifs-sale-form__grid">
        {(isTransferFlow ? subFlowFields : productFields).map(renderField)}
      </div>

      <div className="smifs-sale-form__section">Commercials &amp; timeline</div>
      <div className="smifs-sale-form__grid">
        {COMMON.slice(4).map(renderField)}
      </div>

      {submitErr && (
        <div className="smifs-sale-form__alert" data-testid="sale-form-error">{submitErr}</div>
      )}

      <div className="smifs-sale-form__actions">
        <button type="submit" disabled={submitting || disabled || deckBlocked}
                data-testid="sale-form-submit" className="smifs-sale-form__submit">
          {submitting ? <Loader2 size={16} className="animate-spin"/> : <CheckCircle2 size={16}/>}
          {submitting ? "Submitting…" : (deckBlocked ? "Deck unavailable" : "Submit sale")}
        </button>
      </div>
    </form>
  );
}
