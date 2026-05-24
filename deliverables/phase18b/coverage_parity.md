# Phase 18b · Coverage parity — local cosine vs deck (20 rows)

Same 20 questions from `local_baseline.json`. Compared top-3 doc titles.

| # | Question | Local top-1 (sub, score) | Deck top-1 (src, score) | Overlap top-3 | Verdict | Which retrieval is better |
|---|---|---|---|---:|---|---|
|  1 | What is in the Mackertich ONE Sapphire AIF Cat-II factsheet? | Category III AIFs- Slide 1 (bedrock, 0.624) | Category III AIFs- Slide 1 (bedrock, 0.499) | 3/3 | identical | Tie — both engines find same passage. Use local for speed. |
|  2 | Pull the latest fortnightly offering bedrock. | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.650) | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.521) | 3/3 | identical | Tie — both engines find same passage. Use local for speed. |
|  3 | Walk me through the AIF sales pitch script. | Alternative Investment Fund · Target C (academy, 0.779) | Bharat Value Fund Series VI (Fund Of F (vehicle, 0.461) | 0/3 | no_overlap | ⚠️ Local wins — academy chunk; deck has no equivalent. |
|  4 | What insurance providers does SMIFS distribute? | SMIFS Business Partner PPT (bedrock, 0.713) | SMIFS Business Partner PPT (bedrock, 0.577) | 3/3 | identical | Tie — both engines find same passage. Use local for speed. |
|  5 | Show me the FY26 Q3 revenue dashboard summary. | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.462) | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.358) | 3/3 | identical | Tie — both engines find same passage. Use local for speed. |
|  6 | Walk me through the AIF sales pitch. | Alternative Investment Fund · Target C (academy, 0.790) | AIF (growth_revenue, 0.478) | 0/3 | no_overlap | ⚠️ Local wins — academy chunk; deck has no equivalent. |
|  7 | What is the SMIFS house view on PMS? | Portfolio Management Services · Produc (academy, 0.598) | SMIFS Corporate PPT (bedrock, 0.464) | 2/3 | partial_2 | Close — local has 1 extra useful hit. Local edges out. |
|  8 | Latest SMIFS market view document. | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.757) | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.615) | 3/3 | identical | Tie — both engines find same passage. Use local for speed. |
|  9 | Compare bedrock vs document chunk for the same offering. | Yield Enhancement on Core Holdings- Sl (bedrock, 0.626) | Yield Enhancement on Core Holdings- Sl (bedrock, 0.501) | 3/3 | identical | Tie — both engines find same passage. Use local for speed. |
| 10 | Tell me about the Bharat NCD primary issue. | PURPLE STYLE LABS \| DEBT FUNDING · PSL (document, 0.502) | PURPLE STYLE LABS \| DEBT FUNDING — Sal (sales_pitch, 0.386) | 1/3 | partial_1 | Mixed — both miss something. |
| 11 | Who last updated the bedrock fortnightly offering? | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.625) | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.500) | 3/3 | identical | Tie — both engines find same passage. Use local for speed. |
| 12 | AIF returns guarantee. | Alternative Investment Fund · Objectio (academy, 0.620) | AIF (growth_revenue, 0.499) | 1/3 | partial_1 | Local wins — academy chunk missing from deck. |
| 13 | What is my last NAV? | Navi Mutual Fund (vehicle, 0.548) | Navi Mutual Fund (vehicle, 0.459) | 3/3 | identical | Tie — both engines find same passage. Use local for speed. |
| 14 | What is an AIF? | Alternative Investment Fund · Objectio (academy, 0.783) | AIF (growth_revenue, 0.607) | 0/3 | no_overlap | ⚠️ Local wins — academy chunk; deck has no equivalent. |
| 15 | PURPLE STYLE LABS NCD focused vehicle details | PURPLE STYLE LABS \| DEBT FUNDING (vehicle, 0.752) | PURPLE STYLE LABS \| DEBT FUNDING (vehicle, 0.610) | 3/3 | identical | Tie — both engines find same passage. Use local for speed. |
| 16 | Retirement planning bedrock slide v2 | Retirement Planning- Slide 1 (bedrock, 0.912) | Retirement Planning- Slide 1 (bedrock, 0.749) | 3/3 | identical | Tie — both engines find same passage. Use local for speed. |
| 17 | How does ARN transfer work in mutual funds? | SMIFS Business Partner PPT (bedrock, 0.606) | SMIFS Business Partner PPT (bedrock, 0.483) | 1/3 | partial_1 | Mixed — both miss something. |
| 18 | KYC onboarding process | KYC, Compliance, and Onboarding (None, 0.638) | SMIFS Corporate PPT (bedrock, 0.367) | 0/3 | no_overlap | ⚠️ Diverged — review case-by-case. |
| 19 | SEBI Category II investor disclosure | SMIFS Business Partner PPT (bedrock, 0.690) | SMIFS Business Partner PPT (bedrock, 0.556) | 1/3 | partial_1 | Mixed — both miss something. |
| 20 | tax implications LTCG AIF | Alternative Investment Fund · Product  (academy, 0.688) | AIF (growth_revenue, 0.490) | 0/3 | no_overlap | ⚠️ Local wins — academy chunk; deck has no equivalent. |

## Rollup

| Verdict | Count | % |
|---|---:|---:|
| identical | 10 | 50% |
| partial_2 | 1 | 5% |
| partial_1 | 4 | 20% |
| no_overlap | 5 | 25% |
| **net ≥1 overlap** | **15** | **75%** |

## Score-scale comparison
- Local top-1 score: min=0.462, max=0.912, mean=0.668
- Deck top-1 score:  min=0.358, max=0.749, mean=0.509
- **Calibration offset to add to deck before merging: +0.16**

## Notable regressions (no_overlap rows)

- **Walk me through the AIF sales pitch script.**
  - Local: Alternative Investment Fund · Target Client Profiling & Lead (academy, 0.779)
  - Deck:  Bharat Value Fund Series VI (Fund Of Fund) (vehicle, 0.461)
  - Root cause: `academy` chunk not indexed by deck.
- **Walk me through the AIF sales pitch.**
  - Local: Alternative Investment Fund · Target Client Profiling & Lead (academy, 0.790)
  - Deck:  AIF (growth_revenue, 0.478)
  - Root cause: `academy` chunk not indexed by deck.
- **What is an AIF?**
  - Local: Alternative Investment Fund · Objection Handling Masterclass (academy, 0.783)
  - Deck:  AIF (growth_revenue, 0.607)
  - Root cause: `academy` chunk not indexed by deck.
- **KYC onboarding process**
  - Local: KYC, Compliance, and Onboarding (None, 0.638)
  - Deck:  SMIFS Corporate PPT (bedrock, 0.367)
- **tax implications LTCG AIF**
  - Local: Alternative Investment Fund · Product Masterclass: Know Your (academy, 0.688)
  - Deck:  AIF (growth_revenue, 0.490)
  - Root cause: `academy` chunk not indexed by deck.

## Conclusion

Deck has **75% top-3 overlap** with local. 50% of queries are identical.
The 25% no-overlap rows are dominated by `academy` corpus gaps — deck
does not index our educational/literacy chunks. For brand-specific and
vehicle-specific queries (where deck and local both have the chunk),
local consistently scores ~0.15 higher in absolute cosine. **The deck
is a viable augment, not a viable replacement.**
