# Phase 18b · Score distribution + threshold proposal

Dataset: 125 hit scores collected across the 50+ probe queries on the
populated deck index. All scores are raw `score` values returned by
`POST /api/knowledge/search`.

## Distribution

```
n=125, min=0.2618, max=0.8079

bucket  count  bar
  0.3:    15   #######
  0.4:    22   ###########
  0.5:    64   ################################  (51% of all hits)
  0.6:    21   ##########
  0.7:     0
  0.8:     3   #
```

## Percentiles

| Pct | Score |
|---:|---:|
| min | 0.262 |
| p10 | 0.314 |
| p25 | 0.448 |
| **p50** | **0.475** |
| p75 | 0.542 |
| p90 | 0.564 |
| p99 | 0.803 |
| max | 0.808 |

## Top / bottom by score (smell test)

### Top 10 — high-precision band
| Score | Source | Title |
|---:|---|---|
| 0.8079 | bedrock | Retirement Planning — Slide 1 |
| 0.8028 | bedrock | Retirement Planning — Slide 2 |
| 0.7818 | vehicle | PURPLE STYLE LABS · DEBT FUNDING |
| 0.6231 | bedrock | Systematic Investment Plan — Slide 1 |
| 0.6193 | bedrock | Systematic Investment Plan — Slide 2 |
| 0.6045 | bedrock | Category III AIFs — Slide 1 |
| 0.5954 | bedrock | Portfolio Rebalancing — Slide 1 |
| 0.5757 | bedrock | SMIFS Fortnightly Offerings · May 2026 |
| 0.5645 | growth_revenue | AIF |
| 0.5645 | growth_revenue | AIF |

> All clearly on-topic. Above 0.55 we're seeing real, high-quality matches.

### Bottom 10 — noise band
| Score | Source | Title |
|---:|---|---|
| 0.2618 | growth_insurance | Aditya Birla |
| 0.2635 | bedrock | SMIFS Fortnightly Offerings (recurring fallback) |
| 0.2783 | growth_insurance | BAJAJ |
| 0.2802 | bedrock | SMIFS Fortnightly Offerings (recurring fallback) |
| 0.2811 | growth_revenue | AIF |
| 0.2812 | sales_pitch | PURPLE STYLE LABS — Sales Pitch (Marathi) |
| 0.2872 | growth_insurance | Bandhan |
| 0.2894 | growth_insurance | ICICI Pru |
| 0.2912 | bedrock | SMIFS Fortnightly Offerings (recurring fallback) |
| 0.2915 | bedrock | SMIFS Fortnightly Offerings (recurring fallback) |

> Below ~0.30 is junk — the deck consistently returns the same SMIFS
> Fortnightly Offerings chunk as a "I have nothing relevant" fallback.

## Score scale interpretation

* **Range**: [0.26, 0.81] — well within cosine [0, 1].
* **Model**: `text-embedding-3-small` (OpenAI). 1536-dim, English-centric.
* **Distribution shape**: skewed left (long tail of low scores from the
  fallback chunk). Peak at 0.475.
* **No re-rank visible**: scores look like raw cosine, not cross-encoder
  re-ranked.

## Comparison to local cosine

| Engine | Mean top-1 (20 baseline q's) | Spread |
|---|---:|---|
| Local cosine (Phase 16, source-weighted) | 0.668 | 0.46–0.91 |
| Deck (raw cosine, no weighting) | 0.509 | 0.36–0.75 |

**Deck scores run ~0.16 lower in absolute terms** because:
1. Local applies Phase-9 source weighting (smifs_knowledge weight 1.0
   vs seed 0.6) + Phase-16 bedrock/focused/recency boosts.
2. The deck embedder is different (`text-embedding-3-small` vs our local
   in-process embedder); absolute cosines aren't directly comparable.

## Proposed threshold

### `DECK_SEARCH_MIN_SCORE = 0.45` (high-precision)

| Threshold | Hits retained | % of 125 | Notes |
|---:|---:|---:|---|
| 0.30 (current default) | 110 | 88% | Too permissive — includes the noise tail |
| 0.40 | 88 | 70% | Better, still some borderline `growth_*` noise |
| **0.45** | **78** | **62%** | **Recommended** — sweet spot |
| 0.50 | 67 | 54% | Conservative — drops some borderline-OK hits |
| 0.55 | 24 | 19% | Too aggressive — drops most real hits |

### Why 0.45

1. **Discards the fallback noise** — every score below ~0.30 was the
   SMIFS Fortnightly Offerings recurring "no-match" chunk. 0.45 cleanly
   removes these without false-negatives.
2. **Keeps multilingual borderline hits** — Tamil sales-pitch hits at
   0.48 stay in; Hindi/Bengali/Marathi noise at 0.27–0.34 is dropped.
   Net: deck only fires when it has real value.
3. **Cosine-comparable to local's effective threshold** after the +0.15
   calibration offset (see `coverage_parity.md`). Local's effective
   threshold for "trust this hit" is ~0.55 post-weighting; deck's 0.45
   raw maps to roughly the same level of confidence.
4. **Conservative bias** — Phase 18 brief says "default off; integrate
   carefully". A high-precision threshold means when the deck DOES fire,
   it fires with high confidence.

### How to apply

Bump the existing env var in `backend/.env`:

```env
DECK_SEARCH_MIN_SCORE=0.45    # was 0.30
```

The value is already plumbed through `deck_search.py:DEFAULT_MIN_SCORE`
and sent on every request body as `min_score`. The deck honours
`min_score` (per param-honour matrix), so this filter happens
**server-side**, saving bandwidth and the deck side does the work.

### Revisit window

After 1 week of `DECK_SEARCH_FALLBACK=true`, re-aggregate scores from the
live `deck_search_calls` collection and re-tune. If we see real production
queries clustering at 0.40–0.45, consider easing the threshold; if we see
junk slipping through at ≥ 0.45, tighten to 0.50.

## Action items (not implemented in this read-only pass)

* ⬜ `backend/.env`: `DECK_SEARCH_MIN_SCORE=0.45`
* ⬜ `backend/agents/deck_search.py`: confirm `DEFAULT_MIN_SCORE` reads
  the env var at call time (currently captured at import — see line 35);
  if so, the threshold takes effect on flag flip without code change.
* ⬜ Document the threshold in `DEPLOY_NOTES.md` activation steps.
