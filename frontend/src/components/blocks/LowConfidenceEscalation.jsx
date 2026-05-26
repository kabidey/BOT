import React from "react";
import { Headset, AlertTriangle } from "lucide-react";

/**
 * Phase 24b — Low-confidence Escalation Rail block.
 *
 * Renders when the bot decided NOT to fabricate an answer. Body comes from
 * `block.user_facing_text` (LLM-agnostic; copy chosen server-side). The
 * accompanying `handoff_request` block (separate block in the list) renders
 * the actual callback CTA — this card is the "why" + reassurance.
 */
export default function LowConfidenceEscalation({ block }) {
  if (!block) return null;
  const intent = block.intent || "";
  const conf = block.confidence || {};
  return (
    <div
      className="smifs-rail smifs-rail--lowconf"
      data-testid="low-confidence-escalation"
    >
      <div className="smifs-rail-icon" aria-hidden>
        <Headset size={18} />
      </div>
      <div className="smifs-rail-body">
        <p className="smifs-rail-eyebrow" data-testid="rail-eyebrow">
          <AlertTriangle size={12} aria-hidden />
          Routing to advisor
        </p>
        <p className="smifs-rail-text" data-testid="rail-text">
          {block.user_facing_text}
        </p>
        {intent && (
          <p className="smifs-rail-intent" data-testid="rail-intent">
            <span className="smifs-rail-intent-label">Your question:</span>{" "}
            <span className="smifs-rail-intent-value">{intent}</span>
          </p>
        )}
        {typeof conf.top_score === "number" && (
          <p className="smifs-rail-meta" data-testid="rail-meta">
            Confidence: {conf.confidence} · top relevance {conf.top_score.toFixed(2)} ·{" "}
            {conf.citation_count} candidate{conf.citation_count === 1 ? "" : "s"}
          </p>
        )}
      </div>
    </div>
  );
}
