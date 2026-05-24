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

// Phase 17.1 — AMC_OPTIONS retired. The MF `amc_name` field is now a
// vehicle-locked text input (auto-filled + read-only on vehicle pick); the
// deck is the source of truth and the previous static dropdown could not
// represent every AMC the deck exposes.

const PRODUCT_FIELDS = {
  mutual_fund: [
    { key: "amc_name",            label: "AMC", type: "text", required: true, lockedByVehicle: true },
    { key: "scheme_name",         label: "Scheme name", type: "text", required: true, lockedByVehicle: true },
    { key: "scheme_type",         label: "Scheme type", type: "radio", required: true,
      options: ["SIP", "Lump sum", "SWP", "STP"] },
    { key: "frequency",           label: "Frequency", type: "select",
      options: ["Monthly","Quarterly","Annually"],
      showIf: (v) => v.scheme_type && v.scheme_type !== "Lump sum" },
    { key: "folio_number",        label: "Folio number (existing)", type: "text" },
    { key: "arn_distributor_code",label: "ARN / Distributor code",  type: "text" },
  ],
  aif: [
    { key: "aif_name",                label: "AIF name", type: "text", required: true, lockedByVehicle: true },
    { key: "category",                label: "Category", type: "radio", required: true,
      options: ["Cat I", "Cat II", "Cat III"] },
    { key: "commitment_amount_inr",   label: "Commitment amount (₹)", type: "number", required: true, min: 0 },
    { key: "drawdown_schedule",       label: "Drawdown schedule",   type: "textarea", required: true,
      placeholder: "e.g. 100% upfront, OR phased over 3 years (40 / 30 / 30)" },
    { key: "fund_manager",            label: "Fund manager", type: "text", required: true },
  ],
  pms: [
    { key: "pms_provider",        label: "PMS provider", type: "text", required: true, lockedByVehicle: true },
    { key: "strategy_name",       label: "Strategy name", type: "text", required: true, lockedByVehicle: true },
    { key: "corpus_inr",          label: "Corpus (₹)", type: "number", required: true, min: 5000000 },
    { key: "fee_structure",       label: "Fee structure", type: "radio", required: true,
      options: ["Fixed only", "Variable only", "Hybrid"] },
    { key: "fixed_fee_pct",       label: "Fixed fee %", type: "number", step: 0.01, min: 0, max: 10,
      showIf: (v) => v.fee_structure === "Fixed only" || v.fee_structure === "Hybrid" },
    { key: "performance_fee_pct", label: "Performance fee %", type: "number", step: 0.01, min: 0, max: 50,
      showIf: (v) => v.fee_structure === "Variable only" || v.fee_structure === "Hybrid" },
  ],
  fd: [
    { key: "issuer_name",      label: "Issuer (bank / NBFC)", type: "text", required: true, lockedByVehicle: true },
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
    { key: "carrier",          label: "Carrier", type: "text", required: true, lockedByVehicle: true, placeholder: "LIC, HDFC Life, …" },
    { key: "product_type",     label: "Product type", type: "radio", required: true,
      options: ["Term", "ULIP", "Endowment", "Money-back", "Health", "Annuity"] },
    { key: "policy_term_years",label: "Policy term (years)", type: "number", required: true, min: 1, max: 50 },
    { key: "premium_frequency",label: "Premium frequency", type: "select", required: true,
      options: ["Single","Annual","Half-yearly","Quarterly","Monthly"] },
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

// Phase 17 — auto-fill mapping: when a deck vehicle is picked, the
// product-specific identity field(s) get pre-populated with `vehicle_name`
// and then LOCKED (read-only) so a free-text override path can't reintroduce
// off-deck submissions. See `backend/sales_api.create_sale` cross-type check.
// Phase 17.1 — every "scheme/issuer/AMC/provider/carrier"-style identity field
// that the deck row encompasses is in this list. The same `vehicle_name`
// string is mirrored into each field; the deck doesn't expose a separate
// provider/scheme split (vehicle_name IS the identity).
const VEHICLE_AUTOFILL_BY_PRODUCT = {
  mutual_fund: ["amc_name", "scheme_name"],
  aif:         ["aif_name"],
  pms:         ["pms_provider", "strategy_name"],
  fd:          ["issuer_name"],
  insurance:   ["carrier"],
  ncd_primary: ["issuer_name"],
};

// ARN Transfer field defs (kept in this file rather than `PRODUCT_FIELDS` so
// the renderer can swap them in/out cleanly on toggle without polluting the
// non-ARN MF flow).
const ARN_FIELDS = [
  { key: "existing_arn",            label: "Existing ARN code",  type: "text", required: true,
    placeholder: "ARN-XXXXX or 4-7 alphanumeric" },
  { key: "new_arn",                 label: "New ARN code",       type: "text", required: true,
    placeholder: "ARN-XXXXX or 4-7 alphanumeric" },
  { key: "folio_numbers",           label: "Folio number(s)",    type: "text", required: true,
    placeholder: "comma-separated folios" },
  { key: "amc_name",                label: "AMC name (locked)",  type: "text", required: true, lockedByVehicle: true },
  { key: "scheme_name",             label: "Scheme name (locked)", type: "text", required: true, lockedByVehicle: true },
  { key: "transfer_effective_date", label: "Transfer effective date", type: "date", required: true },
  { key: "aum_inr",                 label: "AUM being transferred (₹)", type: "number", required: true, min: 1000 },
  { key: "arn_remarks",             label: "Remarks (optional)", type: "textarea" },
];

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

  // ARN Transfer toggle (MF only). When true, swap the body to ARN_FIELDS.
  const [arnTransfer, setArnTransfer] = useState(false);
  const isArnFlow = product === "mutual_fund" && arnTransfer;

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
    if (isArnFlow) {
      // ARN-branch validation (existing non-ARN MF specifics are skipped).
      for (const f of ARN_FIELDS) {
        if (f.required && (!values[f.key] || String(values[f.key]).trim() === "")) {
          e[f.key] = "Required.";
        }
      }
      const ea = (values.existing_arn || "").toUpperCase();
      const na = (values.new_arn || "").toUpperCase();
      if (ea && !ARN_RE.test(ea)) e.existing_arn = "ARN must be 4-7 alphanumeric (optionally ARN-prefixed).";
      if (na && !ARN_RE.test(na)) e.new_arn = "ARN must be 4-7 alphanumeric (optionally ARN-prefixed).";
      if (ea && na && ea === na) e.new_arn = "New ARN must differ from existing ARN.";
      if (values.aum_inr && Number(values.aum_inr) < 1000) e.aum_inr = "Min ₹1,000.";
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
      // Phase 17 — MF ARN-Transfer: pack the ARN-specific keys into a
      // nested `arn_transfer_fields` object so the backend's
      // `_validate_mf_arn` discriminator picks them up cleanly.
      if (isArnFlow) {
        cleaned.arn_transfer = true;
        cleaned.arn_transfer_fields = {
          existing_arn: (values.existing_arn || "").toUpperCase(),
          new_arn: (values.new_arn || "").toUpperCase(),
          folio_numbers: values.folio_numbers || "",
          amc_name: values.amc_name || "",
          scheme_name: values.scheme_name || "",
          transfer_effective_date: values.transfer_effective_date || "",
          aum_inr: Number(values.aum_inr || 0),
          arn_remarks: values.arn_remarks || "",
        };
        // Mirror AUM into the common amount_inr so the common-block
        // validation min ₹1,000 passes off the single ARN AUM input.
        cleaned.amount_inr = Number(values.aum_inr || 0);
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

      {/* Phase 17 — MF ARN Transfer toggle (only on MF, only when a vehicle is picked). */}
      {product === "mutual_fund" && selectedVehicle && (
        <label className="smifs-sale-form__arn-toggle" data-testid="mf-arn-transfer-row">
          <input type="checkbox" checked={arnTransfer}
                 data-testid="mf-arn-transfer-toggle"
                 onChange={(e) => setArnTransfer(e.target.checked)} />
          <Repeat size={14} strokeWidth={2.25}/>
          <span>This is an <strong>ARN Transfer</strong></span>
        </label>
      )}

      <div className="smifs-sale-form__section">Client details</div>
      <div className="smifs-sale-form__grid">
        {COMMON.slice(0, 4).map(renderField)}
      </div>

      <div className="smifs-sale-form__section" data-testid="product-specifics-heading">
        {isArnFlow ? "ARN Transfer details" : `${PRODUCT_LABEL[product]} specifics`}
      </div>
      <div className="smifs-sale-form__grid">
        {(isArnFlow ? ARN_FIELDS : productFields).map(renderField)}
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
