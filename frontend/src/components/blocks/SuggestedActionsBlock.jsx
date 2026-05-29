import React, { useState } from "react";

/**
 * Phase 29b — Suggested follow-up chips (always exactly 3).
 *
 * Props:
 *   block:     { type: "suggested_actions", options: [{id, label}] }
 *   onSelect:  (label: string) => void  — submits as next user message
 *   disabled:  bool — if the parent is mid-streaming the next turn
 *
 * UX:
 *   - 3 pill-shaped chips, horizontally scrollable on narrow screens,
 *     wrap into 2 lines on mobile if needed.
 *   - On click: that chip "fires" (haptic-style press), all 3 disable to
 *     prevent double-submit.
 *   - Removed once the next assistant turn begins (parent unmounts via
 *     keying on the current message).
 */
const SuggestedActionsBlock = ({ block, onSelect, disabled }) => {
  const [picked, setPicked] = useState(null);
  const options = Array.isArray(block?.options) ? block.options.slice(0, 3) : [];
  if (options.length === 0) return null;

  const handleClick = (opt) => {
    if (picked || disabled) return;
    setPicked(opt.id);
    if (typeof onSelect === "function") onSelect(opt.label);
  };

  return (
    <div
      data-testid="suggested-actions-block"
      className="mt-3 flex flex-wrap gap-2 sm:flex-nowrap sm:overflow-x-auto sm:overflow-y-hidden sm:-mx-1 sm:px-1"
      role="group"
      aria-label="Suggested follow-up questions"
    >
      {options.map((opt) => {
        const isPicked = picked === opt.id;
        const isDimmed = (picked && !isPicked) || disabled;
        return (
          <button
            key={opt.id}
            type="button"
            data-testid={`suggested-action-${opt.id}`}
            disabled={!!picked || disabled}
            onClick={() => handleClick(opt)}
            className={[
              "shrink-0 rounded-full px-3.5 py-2 text-sm leading-tight",
              "border transition-all duration-150 select-none",
              "max-w-[260px] truncate text-left",
              isPicked
                ? "bg-emerald-400/20 border-emerald-400/60 text-emerald-200 cursor-default"
                : isDimmed
                ? "bg-white/[0.03] border-white/10 text-white/40 cursor-not-allowed"
                : "bg-white/[0.06] border-white/15 text-white/85 hover:bg-emerald-400/15 hover:border-emerald-400/40 hover:text-emerald-100 active:scale-[0.98]",
            ].join(" ")}
            title={opt.label}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
};

export default SuggestedActionsBlock;
