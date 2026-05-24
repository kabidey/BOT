import { useState } from "react";
import { CheckCircle2, MessageCircle } from "lucide-react";

/**
 * Phase 14 — Yes/No choice block (e.g. "Log a sale?" or "Submit another?").
 *
 * Props:
 *   data: {
 *     title: string,
 *     options: [{id, label, intent}]
 *   }
 *   onChoice(option): sends the choice up to the chat shell.
 */
export default function RoleChoiceBlock({ data, onChoice, disabled }) {
  const [picked, setPicked] = useState(null);
  const options = (data && data.options) || [];
  const title = (data && data.title) || "What would you like to do?";

  const handle = (opt) => {
    if (picked || disabled) return;
    setPicked(opt.id);
    onChoice && onChoice(opt);
  };

  return (
    <div className="smifs-block smifs-role-choice" data-testid="role-choice-block">
      <div className="smifs-role-choice__title">{title}</div>
      <div className="smifs-role-choice__row">
        {options.map((opt) => {
          const isPicked = picked === opt.id;
          const isYes = (opt.id || "").includes("yes") || (opt.id || "").includes("log");
          return (
            <button
              key={opt.id}
              type="button"
              data-testid={`role-choice-${opt.id}`}
              disabled={!!picked || disabled}
              onClick={() => handle(opt)}
              className={`smifs-role-choice__btn ${isYes ? "is-primary" : "is-secondary"} ${isPicked ? "is-picked" : ""}`}
            >
              {isYes ? <CheckCircle2 size={16} /> : <MessageCircle size={16} />}
              <span>{opt.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
