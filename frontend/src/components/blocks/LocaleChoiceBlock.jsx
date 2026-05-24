import { Globe, Check } from "lucide-react";

/**
 * Phase 18 — Workstream B (multilingual UX).
 *
 * Three-locale v1 picker (English / हिंदी / தமிழ்). Renders as either an
 * inline chat block OR a compact header popover — the variant is controlled
 * by the `variant` prop (`"block" | "popover"`).
 *
 * Forms + structured data stay in English by design — only chat prose is
 * localised, so the LocaleChoiceBlock itself is written in each script.
 */
const LOCALE_OPTIONS = [
  { id: "en", label: "English",  native: "English",   hint: "Default" },
  { id: "hi", label: "Hindi",    native: "हिंदी",      hint: "Devanagari" },
  { id: "ta", label: "Tamil",    native: "தமிழ்",      hint: "Tamil script" },
];

export default function LocaleChoiceBlock({
  data,
  current = "en",
  onChoice,
  disabled,
  variant = "block",
}) {
  const title = (data && data.title) ||
    "Choose the language for your replies (forms remain in English)";

  const handle = (opt) => {
    if (disabled) return;
    if (opt.id === current) return;
    onChoice && onChoice(opt);
  };

  if (variant === "popover") {
    return (
      <div className="smifs-locale-popover" data-testid="locale-popover" role="dialog">
        <div className="smifs-locale-popover__title">
          <Globe size={13} strokeWidth={2.25} />
          <span>Reply language</span>
        </div>
        <ul className="smifs-locale-popover__list">
          {LOCALE_OPTIONS.map((opt) => {
            const isPicked = current === opt.id;
            return (
              <li key={opt.id}>
                <button
                  type="button"
                  data-testid={`locale-popover-${opt.id}`}
                  className={`smifs-locale-popover__item ${isPicked ? "is-picked" : ""}`}
                  onClick={() => handle(opt)}
                  disabled={disabled}
                  aria-current={isPicked ? "true" : "false"}
                >
                  <span className="smifs-locale-popover__native">{opt.native}</span>
                  <span className="smifs-locale-popover__hint">{opt.label} · {opt.hint}</span>
                  {isPicked ? <Check size={13} strokeWidth={2.5} aria-hidden /> : null}
                </button>
              </li>
            );
          })}
        </ul>
        <p className="smifs-locale-popover__foot">
          Numbers, PAN, UCC, NAV stay in English.
        </p>
      </div>
    );
  }

  return (
    <div className="smifs-block smifs-locale-choice" data-testid="locale-choice-block">
      <div className="smifs-locale-choice__head">
        <Globe size={14} strokeWidth={2.25} />
        <span className="smifs-locale-choice__title">{title}</span>
      </div>
      <div className="smifs-locale-choice__row" role="radiogroup" aria-label="Reply language">
        {LOCALE_OPTIONS.map((opt) => {
          const isPicked = current === opt.id;
          return (
            <button
              key={opt.id}
              type="button"
              data-testid={`locale-choice-${opt.id}`}
              role="radio"
              aria-checked={isPicked}
              disabled={disabled}
              onClick={() => handle(opt)}
              className={`smifs-locale-choice__chip ${isPicked ? "is-picked" : ""}`}
            >
              <span className="smifs-locale-choice__chip-native">{opt.native}</span>
              <span className="smifs-locale-choice__chip-label">{opt.label}</span>
              {isPicked ? <Check size={12} strokeWidth={2.5} aria-hidden /> : null}
            </button>
          );
        })}
      </div>
      <p className="smifs-locale-choice__foot">
        Technical terms (PAN, UCC, NAV, AUM, ARN, SIP, NCD) and submitted forms
        stay in English regardless of language.
      </p>
    </div>
  );
}

export { LOCALE_OPTIONS };
