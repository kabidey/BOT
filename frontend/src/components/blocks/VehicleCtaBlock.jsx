import { FileSignature } from "lucide-react";

/**
 * Phase 16.2 — Vehicle Factsheet CTA block.
 *
 * Emitted as a TOP-LEVEL block in the `/api/agent/turn` response by the
 * orchestrator (`agents/rag_agent._build_vehicle_cta_blocks`). The backend
 * dedupes by `vehicle_id` and caps at 2 per turn. We render each one as a
 * pill-shaped chip beneath the text block. The chip is keyboard-focusable and
 * carries a stable `data-testid` so headless DOM checks can grep for it.
 *
 * Block shape:
 *   { type: "vehicle_cta",
 *     vehicle_id: "uuid",
 *     vehicle_name: "PURPLE STYLE LABS | DEBT FUNDING",
 *     vehicle_type: "NCD" | "AIF" | "PMS" | "MF" | null,
 *     label: "Open the vehicle factsheet · <name>",
 *     action: "handoff_or_factsheet" }
 *
 * Click handler currently emits an in-app event so the parent Chat can route
 * to the citation popover anchored to this vehicle (which already renders the
 * full passage). Future: deep-link to a dedicated factsheet route.
 */
export default function VehicleCtaBlock({ block, msgIdx, onClick }) {
  const vid = block.vehicle_id;
  const label = block.label || `Open the vehicle factsheet · ${block.vehicle_name || "this vehicle"}`;
  return (
    <div className="smifs-cta-row" data-testid={`vehicle-cta-row-${msgIdx}`}>
      <button
        type="button"
        className="smifs-cta-chip"
        onClick={() => onClick?.(block)}
        data-testid={`vehicle-cta-${msgIdx}`}
        data-vehicle-id={vid}
        data-vehicle-type={block.vehicle_type || ""}
        title={label}
      >
        <FileSignature size={11} strokeWidth={2.25} />
        <span className="smifs-cta-label">{label}</span>
      </button>
    </div>
  );
}
