import { PhoneCall, ArrowRight } from "lucide-react";

export default function EscalationBlock({ block, msgIdx, onRequestCallback }) {
  const reason = block?.data?.reason;
  return (
    <div className="smifs-esc-card" data-testid={`escalation-card-${msgIdx}`}>
      <div className="smifs-esc-head">
        <PhoneCall size={16} strokeWidth={2.25} />
        <p className="smifs-esc-eyebrow">Connect with a human advisor</p>
      </div>
      <h3 className="smifs-esc-title">A senior advisor will take it from here.</h3>
      <p className="smifs-esc-body">
        {reason === "client_not_found"
          ? "We couldn't match the details on file. Our team will verify and respond personally."
          : "Some questions are best handled in conversation. We'll arrange a callback within one business day."}
      </p>
      <button
        type="button"
        className="smifs-esc-cta"
        onClick={onRequestCallback}
        data-testid={`escalation-cta-${msgIdx}`}
      >
        Request a callback <ArrowRight size={14} strokeWidth={2.25} />
      </button>
    </div>
  );
}
