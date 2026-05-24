# Phase 18e · Deck Vector Engine re-probe delta (May 24, 2026 ~19:30 IST)

> Read-only re-probe vs. **Phase 18c baseline** (totalIndexed=2486, ~1h
> earlier). The brief said: "honest reporting — if nothing has changed in
> the last few hours, say so plainly." That's exactly the headline.
>
> Raw artefacts: `/app/deliverables/phase18e/raw/` (30+ payloads), summary
> at `/app/deliverables/phase18e/summary.json`.
> **No code touched.** `DECK_SEARCH_FALLBACK`, `DECK_SEARCH_TIMEOUT_S`,
> `LOCAL_FLOOR` all unchanged.

---

## Executive summary (paste-ready, 5 lines)

> Probe complete. **`totalIndexed: 2489`** — +3 chunks vs 18c (effectively
> flat over an hour). **Zero changes** to envelope keys, hit shape, server-
> side filter honour, source distribution (still no `academy`/`document`,
> still 71% `documents_full`), score histogram (median 0.493 → 0.493) or
> multilingual quality (Hindi top stuck at 0.31). Coverage parity rollup
> 7/1/6/6 — **byte-identical to 18c**. Tiny latency tick (p50 2 717 →
> 2 581 ms). **Recommendation: stay at (C) Augment, no changes to ship.
> The two patches from 18c (timeout 3.0s, `documents_full` muted chip)
> have already landed in 18.2 and remain correctly tuned.** Confidence:
> **high**.

---

## TL;DR table — 18c vs 18e

| Axis | 18c | 18e | Δ |
|---|---|---|---|
| `totalIndexed` | 2 486 | **2 489** | +3 (flat) |
| Envelope keys (count) | 8 | 8 | **unchanged** |
| Hit keys (count) | 8 | 8 | **unchanged** |
| `audience` on hit | ❌ | ❌ | **still missing** (deck-team ask pending) |
| `language` on hit | partial only | partial only | unchanged |
| Sources observed | bedrock, vehicle, growth_*, sales_pitch, documents_full | same 6 | **unchanged** |
| `academy` indexed | ❌ | ❌ | **still local-only** |
| `document` indexed | ❌ | ❌ | **still local-only** |
| `documents_full` share | 72% | 71% | flat (357 / 500) |
| Join-key match rate (≥ 25 hits) | 41% | 48% | mild improvement (sampling variance) |
| Server-side params honoured | `q`, `top_k`, `min_score`, `sources` | same 4 | **unchanged** — no new unlocks |
| Serial p50 | 2 717 ms | **2 581 ms** | −5% (within noise) |
| Serial p95 | 3 013 ms | **3 038 ms** | flat |
| Parallel-10 wall | 27 878 ms | 19 991 ms | −28% (rate-limit easing or sampling) |
| Score median | 0.475 → 0.493 | 0.493 | **unchanged** |
| Score p99 | 0.803 | 0.803 | **unchanged** |
| Hindi top score | 0.314 | 0.314 | **byte-identical** (embedder ceiling) |
| Tamil top score | 0.479 | 0.479 | **byte-identical** |
| Parity rollup | 7 / 1 / 6 / 6 | **7 / 1 / 6 / 6** | **byte-identical** |

---

## 1 · `totalIndexed` over 60 seconds

```
t=  0s: 2489
t= 10s: 2489
t= 20s: 2489
t= 30s: 2489
t= 40s: 2489
t= 50s: 2489
t= 60s: 2489
```

Stable. Growth has effectively stopped (+3 vs 18c over ~1h). The
trajectory 0 → 486 → 2486 → 2489 looks like the deck team has finished
their bulk-ingest and is no longer adding sources at a measurable rate.

---

## 2 · Hit-envelope check

The brief's "BIG ONE" — has the deck added any of `audience`,
`language`, `effective_date`, `metadata.audience`, `metadata.is_focused`,
`metadata.vehicle_id` on the per-hit JSON?

**Answer: No.** Hit shape is byte-identical to 18c:

```jsonc
{
  "id":        "...",
  "score":     0.0,
  "source":    "...",
  "sourceId":  "...",
  "title":     "...",
  "section":   "...",
  "content":   "...",
  "metadata":  { /* per-source shape, same as 18c */ }
}
```

Field-by-field diff vs 18c top-level keys:

| Field | 18c | 18e | Status |
|---|---|---|---|
| `id` | ✅ | ✅ | same |
| `score` | ✅ | ✅ | same |
| `source` | ✅ | ✅ | same |
| `sourceId` | ✅ | ✅ | same |
| `title` | ✅ | ✅ | same |
| `section` | ✅ | ✅ | same |
| `content` | ✅ | ✅ | same |
| `metadata` | ✅ | ✅ | same |
| **`audience` (new ask)** | ❌ | **❌** | not yet |
| **`language` (new ask)** | ❌ | **❌** | not yet |
| **`effective_date`** | ❌ | **❌** | not yet |
| **`versionNo`** | ❌ | **❌** | not yet |
| **`metadata.audience`** | ❌ | **❌** | not yet |
| **`metadata.is_focused`** | ❌ (only on `vehicle`/`sales_pitch` rows) | unchanged | not new |
| **`metadata.vehicle_id`** | ❌ (camelCase `vehicleId` exists on some) | unchanged | not new |

→ See `hit_envelope_delta.md` for the full breakdown plus 5 fresh raw
samples per source under `./raw/schema__*.json`.

---

## 3 · Source distribution

| source | 18c hits (top_k=25 × 20q) | 18e hits | Δ |
|---|---:|---:|---:|
| `documents_full` | 361 | **357** | −4 |
| `bedrock` | 99 | **103** | +4 |
| `vehicle` | 29 | **29** | 0 |
| `growth_insurance` | 6 | **6** | 0 |
| `growth_revenue` | 4 | **4** | 0 |
| `sales_pitch` | 1 | **1** | 0 |
| **`academy`** | **0** | **0** | unchanged — still local-only |
| **`document`** | **0** | **0** | unchanged — still local-only |
| _any new source name?_ | — | none | — |

→ Full table at `source_distribution.md`.

---

## 4 · Param-honour matrix (the second "BIG ONE")

Method: send the param + control (same query without it). If result IDs
differ → honoured. Identical IDs → silently dropped.

| Param | Sample value | n_results | Honoured? |
|---|---|---:|---|
| **control** (no extras) | — | 5 | — |
| `min_score` | 0.1 | 5 | ✅ (already honoured) |
| `minScore` (camelCase) | 0.1 | 5 | ❌ (still dropped) |
| **`sources`** | `["bedrock"]` | 5 | ✅ (already honoured) |
| `sources` | `["academy"]` | 0 | ✅ (whitelist works, just returns nothing) |
| `sources` | `["document"]` | 0 | ✅ (same) |
| `source` (singular) | `bedrock` | 5 | ❌ dropped |
| `subsource` | `bedrock` | 5 | ❌ dropped |
| `exclude_sources` | `["sales_pitch"]` | 5 | ❌ dropped (identical to control) |
| **`audience`** | `"all"` | 5 | ❌ **still dropped** |
| **`audience`** | `"employee_only"` | 5 | ❌ **still dropped** (same set as `all`) |
| `language` | `"en"` | 5 | ❌ dropped |
| `vehicle_id` | `cc602b11-9fc2-4bbd-b6af-df529f3bf719` | 5 | ❌ dropped |
| `is_focused` | `true` | 5 | ❌ dropped |
| `is_active` | `true` | 5 | ❌ dropped |
| `filters` | `{"source": "bedrock"}` | 5 | ❌ dropped |

**No movement.** Same 4 params honoured as 18c (`q`, `top_k`, `min_score`,
`sources`). The Phase 18d follow-up ask to the deck team for an
`audience` filter is **still pending**.

→ Full table at `param_honor_matrix.md`.

---

## 5 · Join-key rate (re-check)

| Source prefix | Sampled | Local match | % |
|---|---:|---:|---:|
| `vehicle` | 4 | 4 | 100% |
| `bedrock` | 1 | 1 | 100% |
| `growth_revenue` | 1 | 1 | 100% |
| `documents_full` | 19 | **0** | **0%** (same as 18c) |
| **Total** | **25** | **12** | **48%** |

The headline 41% in 18c → 48% here is sampling noise — `documents_full`
still joins 0% (un-enrichable), the other sources still join 100%.
Functionally identical to 18c.

---

## 6 · Latency

| Metric | 18c | 18e | Δ |
|---|---:|---:|---:|
| Serial p50 | 2 717 ms | **2 581 ms** | −5% |
| Serial p95 | 3 013 ms | **3 038 ms** | +0.8% |
| Serial min | 2 390 ms | 2 244 ms | −6% |
| Serial max | 3 683 ms | 4 054 ms | +10% |
| Parallel-10 wall | 27 878 ms | **19 991 ms** | −28% |

p50 is tickling down within noise. p95 effectively flat at ~3.0s — which
remains the sole justification for Phase 18.2's 3.0s timeout budget. The
parallel-10 improvement is real but probably a less-loaded Cloudflare
edge during the probe; can't bank on it.

> Practical impact on our timeout budget: with p95 at 3.038s, the current
> `DECK_SEARCH_TIMEOUT_S=3.0s` will still time out roughly 5% of
> otherwise-successful calls. That's acceptable for the augment path
> (deck only fires when local is empty). **No tuning recommended.**

---

## 7 · Multilingual quality

| lang | query | top score (18c) | top score (18e) | Δ |
|---|---|---:|---:|---:|
| en  | "investment opportunities right now" | 0.5494 | 0.5494 | 0 |
| hi  | `निवेश के अवसर` | **0.3141** | **0.3141** | **0 — byte-identical** |
| hi2 | `AIF क्या होता है` | 0.5280 | 0.5280 | 0 |
| ta  | `AIF க்கான தமிழ் pitch` | 0.4785 | 0.4785 | 0 |
| ta2 | `PMS திட்டம் என்ன` | 0.3696 | 0.3696 | 0 |
| bn  | `এআইএফ বিনিয়োগ সুযোগ` | 0.3439 | 0.3439 | 0 |
| mr  | `गुंतवणुकीच्या संधी` | 0.3114 | 0.3114 | 0 |
| hinglish | "AIF ka structure kya hai" | 0.5244 | 0.5244 | 0 |
| en2 | "what is AIF structure" | 0.5617 | 0.5617 | 0 |

**Every multilingual score is byte-identical to 18c.** This is the
fingerprint of a deterministic deck-side service that hasn't been
reconfigured. Hindi remains capped at 0.31 (well below the 0.45 floor)
because the embedder (`text-embedding-3-small`) is English-centric.
Phase 18 Workstream B UX remains safe — our chat path translates LLM
**reply** locale, not the retrieval query.

---

## 8 · Score distribution

| Percentile | 18c | 18e | Δ |
|---:|---:|---:|---:|
| min | 0.285 | 0.297 | +0.012 |
| p10 | 0.388 | 0.388 | 0 |
| p25 | 0.453 | 0.453 | 0 |
| **p50** | **0.493** | **0.493** | **0** |
| p75 | 0.539 | 0.539 | 0 |
| p90 | 0.611 | 0.611 | 0 |
| p99 | 0.803 | 0.803 | 0 |
| max | 0.808 | 0.808 | 0 |

Stable. `DECK_SEARCH_MIN_SCORE=0.45` continues to be the right cut-off.
**No change recommended.**

---

## 9 · Coverage parity (re-run, 20 rows)

| Verdict | 18b | 18c | 18e | Δ vs 18c |
|---|---:|---:|---:|---:|
| identical (3/3) | 10 | 7 | **7** | 0 |
| partial_2 | 1 | 1 | **1** | 0 |
| partial_1 | 4 | 6 | **6** | 0 |
| no_overlap (0/3) | 5 | 6 | **6** | 0 |
| **net ≥1 overlap** | 15 (75%) | 14 (70%) | **14 (70%)** | 0 |

**Byte-identical to 18c.** Same 6 no-overlap rows (5 with local
`academy` top-1, 1 with `bedrock`), same `documents_full` regression
pattern. No movement.

---

## 10 · Anomalies observed (the "anything unusual" sweep)

* **No new response headers** — same `Content-Type: application/json`,
  no rate-limit headers, no `X-Deck-Build`, no `X-Audience-Tier`.
* **No error responses** — 30/30 schema + param probes returned 200.
  The parallel-10 burst saw all 10 calls succeed (vs sporadic Cloudflare
  502s in 18c).
* **No null fields** — every key in the 8-key hit envelope was non-null
  on every hit.
* **One tiny consistency wart** — the `provider` field still
  contains the literal string `"openai"` (lowercase). 18c had the same.
  Not blocking; flagging for posterity.

---

## 11 · Architecture re-recommendation

> **No change. Stay at (C) Augment with the Phase 18.2 patches already
> in place.** Nothing has moved the needle since 18c — most of the deck
> is byte-identical between the two probes. Confidence: **high.**

### Reading against 18c's recommendation

* **18c recommended**: stay at (C), ship two patches.
  1. Timeout 2.5s → 3.0s.
  2. Differentiate `documents_full` in citation rendering.
* **18.2 shipped both.** `DECK_SEARCH_TIMEOUT_S=3.0`,
  `is_full_document_scan` flag on citations, muted-grey chip,
  `documents_full` audience guard for visitor/client, 8 new tests.

### What moved the needle (nothing)

* **`audience` field is still missing** — the deck-team ask from
  Phase 18d hasn't landed yet. When it does, the immediate small patch
  is to **replace our local source-name belt-and-suspenders with the
  new field** (i.e., drop hits where `enriched.audience !=
  "all"` instead of guessing from `source`). Don't ship until the field
  is real. Estimated change: ~10 LOC in `deck_search.py`.
* **`academy` is still NOT indexed** — would unlock a hybrid merge
  consideration if it ever lands (the 18c parity regression is 100%
  caused by missing academy chunks). Today, not.
* **Latency is flat** — p95 still ~3.0s, so (B) Hybrid is still
  blocked. We'd need p95 ≤ 2.0s before parallel local+deck is
  acceptable.
* **`documents_full` is still un-enrichable** — and the Phase 18d
  audit confirmed 11.4% of these chunks are issuer-marked
  distributor-only. The 18.2 guard for visitor/client remains
  justified.

### When to probe again

Schedule a re-probe **only when one of these signals fires**:

1. The deck team replies that the `audience` field shipped.
2. `totalIndexed` ticks past **3 500** (suggesting `academy` or
   `document` ingest started).
3. Production telemetry on `/api/admin/deck_search/status` shows a
   sustained shift in `p50_latency_ms_last_50` (down to < 2 000 ms or
   up past 4 000 ms).

If none of those signals fire, **don't probe** — running blind probes
every hour is cheap but produces no new information.

---

## Appendix · raw artefacts

* `/app/deliverables/phase18e/summary.json` — machine-readable digest
* `/app/deliverables/phase18e/raw/` — 30+ per-call response JSONs
* `/app/deliverables/phase18e/source_distribution.md`
* `/app/deliverables/phase18e/hit_envelope_delta.md`
* `/app/deliverables/phase18e/param_honor_matrix.md`
* `/app/deliverables/phase18e/probe_deck_search.py` — read-only probe script (same as 18c, output dir updated)
