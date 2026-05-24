import { CheckCircle2, Plus, MessageCircle } from "lucide-react";

/**
 * Phase 14 — confirmation card shown after a successful sale submission.
 *
 * Props:
 *   data: { submission_id, message, product, amount_inr, client_name, client_pan_masked }
 *   onAgain(action): "another_sale" | "ask_question"
 */
const PROD_LABEL = {
  mutual_fund: "Mutual Fund", aif: "AIF", pms: "PMS",
  fd: "Fixed Deposit", insurance: "Insurance",
  ncd_primary: "NCD Primary Issue",
};

function fmtINR(n) {
  if (!n) return "—";
  const v = Number(n);
  if (v >= 1e7) return `₹${(v/1e7).toFixed(2)} Cr`;
  if (v >= 1e5) return `₹${(v/1e5).toFixed(2)} L`;
  return `₹${v.toLocaleString("en-IN")}`;
}

export default function SaleConfirmationBlock({ data, onAgain, disabled }) {
  const d = data || {};
  return (
    <div className="smifs-block smifs-sale-conf" data-testid="sale-confirmation-block">
      <div className="smifs-sale-conf__top">
        <CheckCircle2 size={22} />
        <div className="smifs-sale-conf__title">Sale logged</div>
      </div>
      <div className="smifs-sale-conf__ref">
        Reference: <span data-testid="sale-conf-ref">{d.submission_id}</span>
      </div>
      <div className="smifs-sale-conf__row">
        <span>Product</span><b>{PROD_LABEL[d.product] || d.product}</b>
      </div>
      <div className="smifs-sale-conf__row">
        <span>Amount</span><b>{fmtINR(d.amount_inr)}</b>
      </div>
      <div className="smifs-sale-conf__row">
        <span>Client</span><b>{d.client_name} &middot; {d.client_pan_masked}</b>
      </div>
      <div className="smifs-sale-conf__msg">
        Sales Ops will follow up shortly. Anything else?
      </div>
      <div className="smifs-sale-conf__cta">
        <button type="button" data-testid="sale-conf-another"
                disabled={disabled}
                onClick={() => onAgain && onAgain({ id: "another_sale", label: "Log another sale" })}
                className="smifs-sale-conf__btn is-primary">
          <Plus size={16}/> Log another sale
        </button>
        <button type="button" data-testid="sale-conf-ask"
                disabled={disabled}
                onClick={() => onAgain && onAgain({ id: "ask_question", label: "Ask a question" })}
                className="smifs-sale-conf__btn is-secondary">
          <MessageCircle size={16}/> Ask a question
        </button>
      </div>
    </div>
  );
}
