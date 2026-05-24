# Phase 18c · Coverage parity — local cosine vs deck (20 rows, re-probed)

Same 20 questions from `/app/deliverables/phase18/deck_search_probe/local_baseline.json`.
Both engines queried with `top_k=10`. Compared top-3 doc titles.

| # | Question | Local top-1 (sub, score) | Deck top-1 (src, score) | 3-overlap | Verdict |
|---|---|---|---|---:|---|
|  1 | What is in the Mackertich ONE Sapphire AIF Cat-II facts | Category III AIFs- Slide 1 (bedrock, 0.624) | Category III AIFs- Slide 1 (bedrock, 0.499) | 3/3 | identical |
|  2 | Pull the latest fortnightly offering bedrock. | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.650) | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.521) | 3/3 | identical |
|  3 | Walk me through the AIF sales pitch script. | Alternative Investment Fund · Target C (academy, 0.779) | ICICI Prudential Office Yield Optimise (documents_full, 0.481) | 0/3 | no_overlap |
|  4 | What insurance providers does SMIFS distribute? | SMIFS Business Partner PPT (bedrock, 0.713) | SMIFS Business Partner PPT (bedrock, 0.577) | 3/3 | identical |
|  5 | Show me the FY26 Q3 revenue dashboard summary. | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.462) | ICICI Pru Growth Leaders Fund-Series V (documents_full, 0.406) | 0/3 | no_overlap |
|  6 | Walk me through the AIF sales pitch. | Alternative Investment Fund · Target C (academy, 0.790) | ICICI Prudential Office Yield Optimise (documents_full, 0.498) | 0/3 | no_overlap |
|  7 | What is the SMIFS house view on PMS? | Portfolio Management Services · Produc (academy, 0.598) | SMIFS Corporate PPT (bedrock, 0.464) | 2/3 | partial_2 |
|  8 | Latest SMIFS market view document. | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.757) | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.615) | 3/3 | identical |
|  9 | Compare bedrock vs document chunk for the same offering | Yield Enhancement on Core Holdings- Sl (bedrock, 0.626) | Yield Enhancement on Core Holdings- Sl (bedrock, 0.501) | 3/3 | identical |
| 10 | Tell me about the Bharat NCD primary issue. | PURPLE STYLE LABS \| DEBT FUNDING · PSL (document, 0.502) | PURPLE STYLE LABS \| DEBT FUNDING — Sal (sales_pitch, 0.386) | 1/3 | partial_1 |
| 11 | Who last updated the bedrock fortnightly offering? | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.625) | SMIFS Fortnightly Offerings — May 2026 (bedrock, 0.500) | 3/3 | identical |
| 12 | AIF returns guarantee. | Alternative Investment Fund · Objectio (academy, 0.620) | AIF (growth_revenue, 0.499) | 1/3 | partial_1 |
| 13 | What is my last NAV? | Navi Mutual Fund (vehicle, 0.548) | Navi Mutual Fund (vehicle, 0.459) | 1/3 | partial_1 |
| 14 | What is an AIF? | Alternative Investment Fund · Objectio (academy, 0.783) | AIF (growth_revenue, 0.607) | 0/3 | no_overlap |
| 15 | PURPLE STYLE LABS NCD focused vehicle details | PURPLE STYLE LABS \| DEBT FUNDING (vehicle, 0.752) | PURPLE STYLE LABS \| DEBT FUNDING (vehicle, 0.611) | 1/3 | partial_1 |
| 16 | Retirement planning bedrock slide v2 | Retirement Planning- Slide 1 (bedrock, 0.912) | Retirement Planning- Slide 1 (bedrock, 0.749) | 3/3 | identical |
| 17 | How does ARN transfer work in mutual funds? | SMIFS Business Partner PPT (bedrock, 0.606) | SMIFS Business Partner PPT (bedrock, 0.483) | 1/3 | partial_1 |
| 18 | KYC onboarding process | KYC, Compliance, and Onboarding (None, 0.638) | ASK Special Opportunities Portfolio (S (documents_full, 0.436) | 0/3 | no_overlap |
| 19 | SEBI Category II investor disclosure | SMIFS Business Partner PPT (bedrock, 0.690) | SMIFS Business Partner PPT (bedrock, 0.556) | 1/3 | partial_1 |
| 20 | tax implications LTCG AIF | Alternative Investment Fund · Product  (academy, 0.688) | Aditya Birla Global Bluechip Equity Fu (documents_full, 0.528) | 0/3 | no_overlap |

## Rollup

| Verdict | 18b | 18c | Δ |
|---|---:|---:|---:|
| identical | 10 | 7 | -3 |
| partial_2 | 1 | 1 | +0 |
| partial_1 | 4 | 6 | +2 |
| no_overlap | 5 | 6 | +1 |
| **net ≥1 overlap** | **15/20 (75%)** | **14/20 (70%)** | **-1** |

## Score-scale comparison (vs 18b)

| Engine | Mean top-1 (18b) | Mean top-1 (18c) | Δ |
|---|---:|---:|---:|
| Local cosine | 0.668 | 0.668 | -0.000 |
| Deck | 0.509 | 0.519 | +0.010 |
| **Calibration offset** (local − deck) | **+0.159** | **+0.149** | — |

## Where the deck won vs lost

### Identical (3/3 overlap) — 7 rows
- _What is in the Mackertich ONE Sapphire AIF Cat-II factsheet?_ — both engines find the same passage in their top-3.
- _Pull the latest fortnightly offering bedrock._ — both engines find the same passage in their top-3.
- _What insurance providers does SMIFS distribute?_ — both engines find the same passage in their top-3.
- _Latest SMIFS market view document._ — both engines find the same passage in their top-3.
- _Compare bedrock vs document chunk for the same offering._ — both engines find the same passage in their top-3.
- _Who last updated the bedrock fortnightly offering?_ — both engines find the same passage in their top-3.
- _Retirement planning bedrock slide v2_ — both engines find the same passage in their top-3.

### No-overlap (0/3 overlap) — 6 rows

- **Walk me through the AIF sales pitch script.**
  - Local: _Alternative Investment Fund · Target Client Profiling & Lead_ (academy, 0.779)
  - Deck:  _ICICI Prudential Office Yield Optimiser Fund · Office Yield Optimiser Fund II.pdf_ (documents_full, 0.481)
  - Root cause: `academy` corpus missing from deck.
- **Show me the FY26 Q3 revenue dashboard summary.**
  - Local: _SMIFS Fortnightly Offerings — May 2026 · Edition 2_ (bedrock, 0.462)
  - Deck:  _ICICI Pru Growth Leaders Fund-Series VI · ICICI Pru Growth Leaders Fund Series VI - Presentation(Regular)_Feb 26 (1).pdf_ (documents_full, 0.406)
  - Root cause: `documents_full` PDF text noise crowding out the right answer.
- **Walk me through the AIF sales pitch.**
  - Local: _Alternative Investment Fund · Target Client Profiling & Lead_ (academy, 0.790)
  - Deck:  _ICICI Prudential Office Yield Optimiser Fund · Office Yield Optimiser Fund II.pdf_ (documents_full, 0.498)
  - Root cause: `academy` corpus missing from deck.
- **What is an AIF?**
  - Local: _Alternative Investment Fund · Objection Handling Masterclass_ (academy, 0.783)
  - Deck:  _AIF_ (growth_revenue, 0.607)
  - Root cause: `academy` corpus missing from deck.
- **KYC onboarding process**
  - Local: _KYC, Compliance, and Onboarding_ (None, 0.638)
  - Deck:  _ASK Special Opportunities Portfolio (SOP) · ASK Special Opportunities Portfolio Presentation.pdf_ (documents_full, 0.436)
  - Root cause: `documents_full` PDF text noise crowding out the right answer.
- **tax implications LTCG AIF**
  - Local: _Alternative Investment Fund · Product Masterclass: Know Your_ (academy, 0.688)
  - Deck:  _Aditya Birla Global Bluechip Equity Fund · ABSL Global Bluechip Equity Fund (IFSC) _Deck.pdf_ (documents_full, 0.528)
  - Root cause: `academy` corpus missing from deck.

## Conclusion

Net top-3 overlap dropped from 75% → 70%. The regression is entirely on
queries where the deck now surfaces a `documents_full` PDF chunk (an
unrelated vehicle's brochure) in place of a more on-topic `bedrock` or
`vehicle` chunk. The fix is NOT to suppress `documents_full` outright
(it does carry real signal for vehicle-specific queries) but to rank it
down vs more focused sources — out of scope for this read-only probe.
