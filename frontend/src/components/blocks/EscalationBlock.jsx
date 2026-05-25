import { useState } from "react";
import { PhoneCall, ArrowRight, MessageCircle, Mail, CheckCircle2 } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

export default function EscalationBlock({ block, msgIdx, onRequestCallback, sessionId, userQuestion, contextSnippet }) {
  const reason = block?.data?.reason;
  const data = block?.data || {};
  const hasRM = !!(data.rm_name && (data.rm_mobile || data.rm_email));
  const rmFirst = (data.rm_name || "").split(" ")[0] || "your RM";

  const [notified, setNotified] = useState(null);   // "whatsapp" | "email" | null
  const [busy, setBusy] = useState(null);
  const [errorMsg, setErrorMsg] = useState("");

  // For clients: target=rm, has_contact via rm_mobile/rm_email.
  // For visitors/advisor: target_has_contact=false → backend returns should_callback_form=true.
  const channel_target = reason === "rm_required" ? "rm" : "advisor";

  const initiate = async (kind) => {
    setErrorMsg("");
    if (!sessionId) { setErrorMsg("Session not ready yet."); return; }
    setBusy(kind);
    try {
      const res = await fetch(`${API}/handoff`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getFingerprintHeaders(),
        },
        body: JSON.stringify({
          session_id: sessionId,
          handoff_type: kind,
          channel_target,
          user_question: userQuestion || "",
          context_snippet: contextSnippet || "",
        }),
      });
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt.slice(0, 160));
      }
      const payload = await res.json();
      if (payload.should_callback_form) {
        // No direct contact — fall back to callback form path.
        onRequestCallback?.();
        return;
      }
      if (payload.deep_link) {
        window.open(payload.deep_link, "_blank", "noopener,noreferrer");
        setNotified(kind);
      } else if (payload.fallback_link) {
        window.open(payload.fallback_link, "_blank", "noopener,noreferrer");
        setNotified(kind);
      } else {
        onRequestCallback?.();
      }
    } catch (e) {
      setErrorMsg(e.message || "Could not reach the handoff service.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="smifs-esc-card" data-testid={`escalation-card-${msgIdx}`}>
      <div className="smifs-esc-head">
        <PhoneCall size={16} strokeWidth={2.25} />
        <p className="smifs-esc-eyebrow">
          {hasRM ? `Your Wealth Manager — ${data.rm_name}` : "Connect with a human advisor"}
        </p>
      </div>
      <h3 className="smifs-esc-title">
        {hasRM
          ? "Keep the conversation going with your RM."
          : "A senior advisor will take it from here."}
      </h3>
      <p className="smifs-esc-body">
        {hasRM
          ? <>One tap and {rmFirst} gets a prefilled message with your question and your UCC — no details for you to type out.</>
          : (reason === "client_not_found"
              ? "We couldn't match the details on file. Our team will verify and respond personally."
              : "Some questions are best handled in conversation. We'll arrange a callback within one business day.")}
      </p>
      {hasRM && (
        <div className="smifs-esc-contact">
          {data.rm_mobile_display && (
            <span className="smifs-esc-contact-pill" data-testid={`escalation-rm-mobile-${msgIdx}`}>
              <PhoneCall size={10} strokeWidth={2.5} /> {data.rm_mobile_display}
            </span>
          )}
          {data.rm_email_display && (
            <span className="smifs-esc-contact-pill" data-testid={`escalation-rm-email-${msgIdx}`}>
              <Mail size={10} strokeWidth={2.5} /> {data.rm_email_display}
            </span>
          )}
        </div>
      )}
      <div className="smifs-esc-ctas">
        <button
          type="button"
          className="smifs-esc-cta smifs-esc-cta--primary"
          onClick={() => initiate("whatsapp")}
          disabled={busy === "whatsapp" || notified === "whatsapp"}
          data-testid={`escalation-whatsapp-${msgIdx}`}
        >
          {notified === "whatsapp"
            ? <><CheckCircle2 size={12} strokeWidth={2.5} /> Notified on WhatsApp</>
            : <><MessageCircle size={13} strokeWidth={2.5} /> {busy === "whatsapp" ? "Opening…" : "Continue on WhatsApp"}</>}
        </button>
        <button
          type="button"
          className="smifs-esc-cta smifs-esc-cta--ghost"
          onClick={() => initiate("email")}
          disabled={busy === "email" || notified === "email"}
          data-testid={`escalation-email-${msgIdx}`}
        >
          {notified === "email"
            ? <><CheckCircle2 size={12} strokeWidth={2.5} /> Emailed to advisor</>
            : <><Mail size={13} strokeWidth={2.5} /> {busy === "email" ? "Opening…" : "Email this to my advisor"}</>}
        </button>
        <button
          type="button"
          className="smifs-esc-cta smifs-esc-cta--tertiary"
          onClick={onRequestCallback}
          data-testid={`escalation-cta-${msgIdx}`}
        >
          Request a callback <ArrowRight size={12} strokeWidth={2.25} />
        </button>
      </div>
      {errorMsg && (
        <p className="smifs-esc-err" data-testid={`escalation-err-${msgIdx}`}>{errorMsg}</p>
      )}
    </div>
  );
}
