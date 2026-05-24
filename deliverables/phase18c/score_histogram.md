# Phase 18c · Score distribution + threshold defence

Dataset: 150 hit scores collected across 30 probe queries on the post-bulk-
load deck (`totalIndexed=2486`).

## Distribution

```
n=150, min=0.2846, max=0.8079

bucket  count  bar
 0.2:     0
 0.3:    16   ########
 0.4:    23   ###########
 0.5:    77   ######################################  (51%)
 0.6:    27   #############
 0.7:     4   ##
 0.8:     3   #
```

## Percentiles — 18b vs 18c

| Pct | 18b | 18c | Δ |
|---:|---:|---:|---:|
| min | 0.262 | 0.285 | +0.023 |
| p10 | 0.314 | 0.388 | +0.074 |
| p25 | 0.448 | 0.453 | +0.005 |
| **p50** | **0.475** | **0.493** | **+0.018** |
| p75 | 0.542 | 0.539 | −0.003 |
| p90 | 0.564 | 0.611 | +0.047 |
| p99 | 0.803 | 0.803 | 0 |
| max | 0.808 | 0.808 | 0 |

**Observation:** modest upward drift in the low–mid band. The corpus 5×'d
but real top-scoring matches haven't appeared at higher absolute scores —
p99 is unchanged at 0.803. The mass migrated up by ~0.02 in the median.

## Top 10 — high-precision band (no surprises)

| Score | Source | Title |
|---:|---|---|
| 0.8079 | bedrock | Retirement Planning — Slide 1 |
| 0.8028 | bedrock | Retirement Planning — Slide 2 |
| 0.7493 | bedrock | (Retirement Planning v2 chunk) |
| 0.6231 | bedrock | Systematic Investment Plan — Slide 1 |
| 0.6193 | bedrock | Systematic Investment Plan — Slide 2 |
| 0.6113 | vehicle | PURPLE STYLE LABS · DEBT FUNDING |
| 0.6045 | bedrock | Category III AIFs — Slide 1 |
| 0.5957 | bedrock | Portfolio Rebalancing — Slide 1 |
| 0.5757 | bedrock | SMIFS Fortnightly Offerings · May 2026 |
| 0.5645 | growth_revenue | AIF |

> Top-10 looks identical to 18b. The same bedrock/vehicle chunks dominate —
> `documents_full` does NOT crack the high-score band.

## Bottom 10 — noise tail

| Score | Source | Title |
|---:|---|---|
| 0.2846 | growth_insurance | Aditya Birla |
| 0.2855 | bedrock | (Fortnightly fallback chunk) |
| 0.2932 | growth_insurance | BAJAJ |
| 0.2980 | bedrock | (Fortnightly fallback chunk) |
| 0.3022 | growth_insurance | Bandhan |
| 0.3104 | sales_pitch | PURPLE STYLE LABS — Sales Pitch (Marathi) |
| 0.3141 | vehicle | (low-relevance Hindi probe hit) |
| 0.3146 | growth_insurance | ICICI Pru |
| 0.3209 | bedrock | (Fortnightly fallback chunk) |
| 0.3304 | growth_insurance | HDFC |

> The noise pattern is unchanged: insurance-provider "growth_insurance"
> stubs and the recurring "Fortnightly Offerings" fallback fill the
> < 0.35 band. `MIN_SCORE=0.45` cleanly excludes all of these.

## Threshold cut-off — preserved at 0.45

| Threshold | Hits retained (18c, n=150) | % | Notes |
|---:|---:|---:|---|
| 0.30 | 134 | 89% | Too permissive |
| 0.40 | 111 | 74% | Lets in insurance noise |
| **0.45** | **97** | **65%** | **Recommended — unchanged from 18b** |
| 0.50 | 73 | 49% | Drops some borderline-OK hits |
| 0.55 | 35 | 23% | Too aggressive |

### Why hold at 0.45

1. **No bottom-band drift:** the < 0.45 noise pattern is the same. Same
   sources, same recurring stub chunks. Lowering the threshold lets noise
   back in.
2. **Conservative bias is the entire point of (C) Augment.** The deck only
   gets a turn when local is empty. A high-precision threshold ensures
   that when it DOES contribute, it contributes a real answer — not a
   "best of bad options" miss.
3. **p99 unchanged at 0.803.** No new "deck only knows" insights at the
   top of the score band that would justify lowering the bar.
4. **Calibration debt is the bigger lever.** The mean top-1 calibration
   gap is now +0.158 (vs +0.159 in 18b — unchanged). When/if we move to
   (B) Hybrid, we'd add +0.15 to deck scores before merging — not lower
   the absolute threshold.

## Recommendation: **no change to `DECK_SEARCH_MIN_SCORE` for Phase 18c**

Keep `DECK_SEARCH_MIN_SCORE=0.45`. Revisit only if a future probe shows
either:

* A meaningful population of hits in `[0.40, 0.45)` that are objectively
  useful (manual review needed), OR
* The deck team adds a re-ranker that compresses score scale upward, OR
* The score scale changes (the deck switches embedder or normalization).

## Score-scale comparison vs local cosine (unchanged)

| Engine | Mean top-1 (18b) | Mean top-1 (18c) | Spread (18c) |
|---|---:|---:|---|
| Local | 0.668 | 0.673 | 0.46 – 0.91 |
| Deck | 0.509 | 0.515 | 0.39 – 0.75 |
| **Calibration offset (local − deck)** | **+0.159** | **+0.158** | held |

If we ever build the merge ranker for (B) Hybrid, add **+0.158** to every
deck score before sorting against local hits. The calibration constant is
stable across the index 5×.
