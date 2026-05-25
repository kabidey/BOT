import { useState } from "react";
import { TrendingUp, Briefcase, BarChart3, Landmark, Shield, FileText, Compass } from "lucide-react";

/**
 * Phase 14 — Product picker. Phase 21 — extended to 7 tiles (added SIF).
 *
 * Props:
 *   data: { products?: [{id, label, subtitle, icon}] }
 *   onPick(product): sends the choice up to chat shell.
 */
const DEFAULT_PRODUCTS = [
  { id: "mutual_fund", label: "Mutual Fund",        icon: TrendingUp },
  { id: "aif",         label: "AIF",                icon: Briefcase  },
  { id: "pms",         label: "PMS",                icon: BarChart3  },
  { id: "fd",          label: "Fixed Deposit",      icon: Landmark   },
  { id: "insurance",   label: "Insurance",          icon: Shield     },
  { id: "ncd_primary", label: "NCD Primary Issue",  icon: FileText,
    subtitle: "Public issue NCD application" },
  { id: "sif",         label: "SIF",                icon: Compass,
    subtitle: "Specialised Investment Fund" },
];

export default function ProductChoiceBlock({ data, onPick, disabled }) {
  const [picked, setPicked] = useState(null);
  const products = (data && data.products) || DEFAULT_PRODUCTS;
  return (
    <div className="smifs-block smifs-product-choice" data-testid="product-choice-block">
      <div className="smifs-product-choice__title">Which product?</div>
      <div className="smifs-product-choice__grid">
        {products.map((p) => {
          const Icon = p.icon || TrendingUp;
          const isPicked = picked === p.id;
          return (
            <button
              key={p.id}
              type="button"
              data-testid={`product-choice-${p.id}`}
              disabled={!!picked || disabled}
              onClick={() => { setPicked(p.id); onPick && onPick(p); }}
              className={`smifs-product-choice__btn ${isPicked ? "is-picked" : ""}`}
            >
              <Icon size={20} />
              <span>{p.label}</span>
              {p.subtitle && <span className="smifs-product-choice__sub">{p.subtitle}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
