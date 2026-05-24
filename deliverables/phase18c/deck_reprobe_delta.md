# Phase 18c — Deck Vector Engine re-probe delta (May 24, 2026, ~18:40 IST)

> Read-only re-probe vs. **Phase 18b baseline** (totalIndexed=486, ~5 hours earlier).
> Today's `current_totalIndexed_seen` was reported as 2486 (+412% growth).
> Raw artefacts: `/app/deliverables/phase18c/raw/` (30+ payloads) plus 6
> representative hits in `./deck_hits_shape/`.
>
> **No code touched.** `DECK_SEARCH_FALLBACK=true` remains as-is; the
> 2.5s timeout and `LOCAL_FLOOR=0.10` are unchanged.

---

## TL;DR (read this first)

1. **Index has stopped growing.** `totalIndexed=2486` is **stable across a 60-second sampling window** and a 5-minute total probe duration. The 5x growth observed earlier has settled.
2. **A new top-level source has been added: `documents_full`.** This is the biggest change since 18b. It accounts for **361/500 (72%)** of all hits across 20 broad probe queries. Pre-18c we had 5 sources (bedrock, vehicle, growth_insurance, growth_revenue, sales_pitch); we now have 6.
3. **`documents_full` is NOT joinable to our local `doc_chunks.smifs_id`** — 0/58 = **0% match**. The IDs follow the same `<source>:<uuid>:<ord>` scheme, but the UUIDs are deck-side-only and have never been synced via `/api/knowledge/sync`. The other 5 sources still match **100%** (40/40 sampled).
4. **`academy` and `document` are STILL missing from the deck.** The new `documents_full` source is NOT a replacement — it's full vehicle-document text extraction (e.g., ICICI Prudential fund decks, ASK fund presentations), not our SMIFS academy/literacy corpus.
5. **Latency improved substantially.** Serial p50 **4 703ms → 2 717ms (−42%)**; p95 **7 020ms → 3 013ms (−57%)**. But p50 is **still above our 2.5s timeout budget** — the deck currently times out ~50% of the time in production.
6. **Coverage parity regressed.** Net top-3 overlap: **75% (18b) → 70% (18c)**. Identical: 10 → 7. The `documents_full` source is winning slots formerly held by `bedrock`/`vehicle` hits, but on niche queries (e.g. "Walk me through the AIF sales pitch script") it surfaces irrelevant ICICI/ASK fund PDFs and pushes the right answer off the top-3.
7. **Server-side filter params: STILL only `sources=[…]` works.** `audience`, `subsource`, `exclude_sources`, `language`, `vehicle_id`, `is_focused`, `is_active`, `filters{…}` — all silently dropped (echoed_sources=[], result set identical to no-param baseline). The "audience filter unlock" we were hoping for has NOT happened.
8. **Multilingual quality essentially unchanged.** Hindi top score still 0.31 (weak); Tamil 0.48 (OK); Hinglish 0.52 (works). The 5x corpus growth did not move multilingual scores meaningfully — confirms the embedder (text-embedding-3-small) is the bottleneck, not coverage.
9. **Score distribution shifted slightly down.** Median 0.475 → **0.493**, p90 0.564 → **0.611**, p99 0.803 → **0.803** (unchanged). Mass is broader. **`DECK_SEARCH_MIN_SCORE=0.45` is still the right threshold** — no change recommended.
10. **Recommendation: stay at (C) Augment with two small tuning patches.** The data tells us NOT to move to (B) Hybrid yet, and DEFINITELY not (A) Replace — the new `documents_full` source is both un-enrichable and noisy on academy-style queries. Detailed plan in §10. Confidence: **high**.

---

## 1 · `totalIndexed` window (60 seconds)

```
t=  0s: totalIndexed = 2486
t= 10s: totalIndexed = 2486
t= 20s: totalIndexed = 2486
t= 30s: totalIndexed = 2486
t= 40s: totalIndexed = 2486
t= 50s: totalIndexed = 2486
t= 60s: totalIndexed = 2486
```

During the longer 4-minute parity re-run we did observe a tiny tick to
`2488` (+2 chunks). The deck team continues to ingest at a trickle but
the bulk-load that took us from 486 → 2486 has completed.

---

## 2 · Source distribution (20 broad queries, top_k=25)

| source | count | % of hits | join to local? |
|---|---:|---:|---:|
| `documents_full` | 361 | 72% | **0%** ← NEW source, un-joinable |
| `bedrock` | 99 | 20% | 100% |
| `vehicle` | 29 | 6% | 100% |
| `growth_insurance` | 6 | 1% | 100% |
| `growth_revenue` | 4 | 1% | 100% |
| `sales_pitch` | 1 | <1% | 100% |
| `academy` | **0** | 0% | n/a (NEVER in deck) |
| `document` | **0** | 0% | n/a (NEVER in deck) |

> The deck has roughly tripled in size (486 → 2486) by adding `documents_full`
> rather than ingesting our literacy/academy corpus. The educational backbone
> the bot serves on visitor turns is still local-only.

---

## 3 · Hit shape (sanity re-check)

Envelope unchanged from 18b — 8 top-level keys:

```
query, model, provider, sources, topK, minScore, results, totalIndexed
```

Hit shape unchanged — same 8 keys present on every result:

```
id, score, source, sourceId, title, section, content, metadata
```

### New `metadata` shape: `documents_full`

```jsonc
{
  "id": "documents_full:34fa200d-3d34-46a9-bcc5-65065a89eb29:72",
  "score": 0.5525,
  "source": "documents_full",
  "sourceId": "34fa200d-3d34-46a9-bcc5-65065a89eb29",
  "title": "ICICI Prudential Office Yield Optimiser Fund · Office Yield Optimiser Fund II.pdf",
  "section": "Vehicle Document · AIF",
  "content": "AIF II\nTransformation of Commercial Real Estate\n…",
  "metadata": {
    "vehicleId":   "0afb1dcd-bf0c-41b7-9a53-3488b25a6391",   // ← link to a vehicle
    "vehicleName": "ICICI Prudential Office Yield Optimiser Fund",
    "vehicleType": "AIF",
    "fileType":    "pdf",
    "fileName":    "Office Yield Optimiser Fund II.pdf",
    "fileSize":    503677,
    "ordinal":     72,
    "kind":        "body"
  }
}
```

> `documents_full` does carry `metadata.vehicleId` and `vehicleName` directly
> on the hit — so for the vehicle-related half of these chunks we COULD enrich
> server-side without a `doc_chunks` join (just trust the metadata). But our
> codebase doesn't currently have a `vehicles` collection (vehicle records live
> as rows inside `doc_chunks` with `subsource=vehicle`). So enrichment would
> still need to go through `doc_chunks` via a `vehicle_id` lookup, not `smifs_id`.

---

## 4 · Join-key verification (worse than 18b)

| Prefix | Sampled hits | Match in `doc_chunks.smifs_id` | Verdict |
|---|---:|---:|---|
| `vehicle` | 18 | 18 | **100%** |
| `bedrock` | 19 | 19 | **100%** |
| `growth_revenue` | 2 | 2 | **100%** |
| `growth_insurance` | 1 | 1 | **100%** |
| `documents_full` | 58 | **0** | **0%** |
| **Total** | **98** | **40** | **41%** |

**Headline:** the 100% join-key rate held for every source that existed in
18b. The **new** `documents_full` source pulls overall join rate down to 41%
because it accounts for the majority of hits today.

**Implication for `deck_search.py` enrichment:** `_enrich_with_local()`
currently joins on `smifs_id`. For `documents_full` hits, the lookup misses,
the enriched audience falls back to `"all"` (our default), and the
belt-and-suspenders source-name fallback (covering `sales_pitch`/`growth_*`)
keeps these hits viewable for visitors. **No new audience-leak risk** —
`documents_full` is vehicle-document text which is already public-facing
brochure content. But we lose vehicle CTA / version badge / focused-state
enrichment on the 72% of deck hits that come from this source.

---

## 5 · Coverage parity (re-run, 20 rows)

> Methodology: same 20 baseline questions from
> `/app/deliverables/phase18/deck_search_probe/local_baseline.json`.
> Both engines queried with `top_k=10`; compared top-3 doc titles.

| Verdict | 18b | 18c | Δ |
|---|---:|---:|---:|
| identical (3/3) | 10 | **7** | −3 |
| partial_2 | 1 | 1 | 0 |
| partial_1 | 4 | 6 | +2 |
| no_overlap (0/3) | 5 | **6** | +1 |
| **net ≥1 overlap** | **15/20 (75%)** | **14/20 (70%)** | **−5 pp** |

### Why the regression

All 6 no-overlap rows have a local `academy`/`bedrock` top-1 that the deck
cannot reach. In 3 of them the deck top-1 is a `documents_full` PDF chunk
that is on-topic in a loose sense (ICICI Prudential Office Yield Optimiser
for "tax implications LTCG AIF") but objectively worse for the user
question. Examples:

| Question | Local top-1 | Deck top-1 | Drift |
|---|---|---|---|
| "Walk me through the AIF sales pitch script" | Academy literacy chunk (0.79) | ICICI Office Yield Optimiser PDF (0.48) | wrong product |
| "What is an AIF?" | Academy literacy chunk (0.78) | growth_revenue AIF stub (0.61) | shorter / less explanatory |
| "tax implications LTCG AIF" | Academy chunk (0.69) | ABSL Global Bluechip PDF (0.53) | unrelated fund |
| "KYC onboarding process" | KYC seed chunk (0.64) | ASK Special Opps PDF (0.44) | unrelated fund |

The deck's `documents_full` corpus is "broad but shallow" for general
financial-literacy questions. For brand- / vehicle-specific questions
(Sapphire AIF, Purple Style NCD, Retirement Planning slide) coverage
stays at 18b levels (3/3 overlap).

See `coverage_parity.md` for the full row-by-row table.

---

## 6 · Latency

| Metric | 18b (May 24 ~14:00) | 18c (May 24 ~18:40) | Δ |
|---|---:|---:|---:|
| Serial p50 | 4 703 ms | **2 717 ms** | −42% |
| Serial p95 | 7 020 ms | **3 013 ms** | −57% |
| Serial min | 584 ms | 2 390 ms | +309% (no fast path now) |
| Serial max | 10 133 ms | 3 683 ms | −64% |
| Parallel-10 wall | 6 496 ms | 27 878 ms | +329% (NEW: rate-limited) |

**Two non-obvious observations:**

1. **Tighter distribution, higher floor.** The 18b probe had a fast cold-cache
   path (~500ms min) that no longer exists; today even the fastest call is
   2.4s. The deck is consistent now (`p95-p50` band shrunk from 2 317ms to
   296ms) but every call pays the ~2.5s base.
2. **Parallel runs throttle.** 10 parallel queries took 27.9s wall (vs ~3-4×
   serial p50 expected). Either Cloudflare rate-limiting or single-tenant
   GPU contention. Practical takeaway: **never issue parallel deck calls**
   from the orchestrator.

**Impact on our 2.5s timeout budget:**

* p50 (2 717 ms) is **above** the budget — we time out on roughly half the
  successful-deck calls.
* p95 (3 013 ms) is also above.
* The 18.1 timeout guard is doing its job (no user is waiting more than
  2.5s), but at the cost of ~50% of deck calls returning empty.

> Reasonable next step: revisit `DECK_SEARCH_TIMEOUT_S` upward to 3.0s.
> Specifically NOT recommending that today because the brief says "no code
> changes; just measure". Flagging for the next greenlight.

---

## 7 · Score distribution

```
n=150 hit scores (30 queries × 5 hits each)

bucket  count  bar
 0.2:     0
 0.3:    16   ########
 0.4:    23   ###########
 0.5:    77   ######################################  (51%)
 0.6:    27   #############
 0.7:     4   ##
 0.8:     3   #
```

| Percentile | 18b | 18c | Δ |
|---:|---:|---:|---:|
| min | 0.262 | 0.285 | +0.023 |
| p10 | 0.314 | 0.388 | +0.074 |
| p25 | 0.448 | 0.453 | +0.005 |
| **p50** | **0.475** | **0.493** | **+0.018** |
| p75 | 0.542 | 0.539 | −0.003 |
| p90 | 0.564 | 0.611 | +0.047 |
| p99 | 0.803 | 0.803 | 0 |
| max | 0.808 | 0.808 | 0 |

**Verdict:** small upward drift in the low-mid band (more chunks → more
chances of a near-match), but the top-end and the bulk of the distribution
are essentially unchanged. **`MIN_SCORE=0.45` remains correct** — it still
cleanly removes the "no real match" floor without trimming legitimate
mid-range hits.

See `score_histogram.md` for the full analysis + a defensive case for
holding the threshold steady.

---

## 8 · Parameter honour re-check

Same matrix as 18b. **Zero new params are now respected.**

| Param | 18b | 18c | Status |
|---|---|---|---|
| `q` / `top_k` / `min_score` | ✅ | ✅ | unchanged |
| `sources` (whitelist) | ✅ | ✅ | unchanged — single working filter |
| `source` (singular) | ❌ | ❌ | still dropped |
| `subsource` | ❌ | ❌ | still dropped |
| `audience` | ❌ | ❌ | **still dropped** — no server-side audience filter |
| `audience: employee_only` | ❌ | ❌ | identical result set as `audience: all` |
| `exclude_sources` | ❌ | ❌ | still dropped |
| `language` | ❌ | ❌ | still dropped |
| `vehicle_id` | ❌ | ❌ | still dropped |
| `is_focused` / `is_active` | ❌ | ❌ | still dropped |
| `filters{…}` / nested | ❌ | ❌ | still dropped |

> The `audience` filter unlock that would have moved us toward (A) Replace
> did **not** happen. Our client-side belt-and-suspenders gate remains the
> authoritative audience boundary.

---

## 9 · Multilingual quality

| lang | query | n | top score | top src | Δ vs 18b |
|---|---|---:|---:|---|---:|
| en  | "investment opportunities right now" | 5 | 0.5494 | documents_full | +0.065 |
| hi  | `निवेश के अवसर` | 5 | **0.3141** | vehicle | 0 (unchanged) |
| hi2 | `AIF क्या होता है` (Hinglish-ish) | 5 | 0.5280 | growth_revenue | n/a (new probe) |
| ta  | `AIF க்கான தமிழ் pitch` | 5 | 0.4785 | growth_revenue | +0.001 |
| ta2 | `PMS திட்டம் என்ன` | 5 | **0.3696** | documents_full | n/a (new probe) |
| bn  | `এআইএফ বিনিয়োগ সুযোগ` | 5 | 0.3439 | sales_pitch | 0 (unchanged) |
| mr  | `गुंतवणुकीच्या संधी` | 5 | 0.3114 | documents_full | +0.022 |
| hinglish | "AIF ka structure kya hai" | 5 | 0.5244 | growth_revenue | 0 (unchanged) |
| en2 | "what is AIF structure" | 5 | 0.5617 | growth_revenue | 0 (unchanged) |

**Verdict (unchanged from 18b):**

* **Hindi remains weak** (0.31 top, well below the 0.45 floor) — even with the
  index 5x'd. The embedder is the bottleneck, not coverage. **Important
  product implication:** our Phase 18 Workstream B multilingual UX is
  **safe** because we only translate the LLM reply prose; retrieval queries
  travel internally in English (the LLM does cross-lingual reasoning on
  English context). If we ever change that and start sending Hindi/Bn/Mr
  queries to the deck directly, expect retrieval misses.
* **Tamil OK** (0.48 with Tamil sales-pitch corpus). Same as 18b.
* **Hinglish + transliterated tokens** work well (0.52). Same as 18b.

---

## 10 · Revised architecture recommendation

> **Stay at (C) Augment. Two minor tuning patches recommended for the next
> greenlight (not in this pass).**

**Confidence: high.** The 18c probe gives us a stable, post-bulk-load
picture, and the data on three independent axes (join key, server-side
filters, latency budget) is unambiguous about which way NOT to move.

### Why NOT (A) Replace — moved further out of reach

1. **Academy/document gap unchanged.** The corpus that powers 50% of our
   visitor turns is still local-only.
2. **`documents_full` is un-enrichable.** 72% of deck hits today join 0% to
   local `doc_chunks` — we lose audience/vehicle CTA/version badge metadata
   on the majority of deck citations.
3. **No server-side audience filter.** The single biggest unlock we needed
   for (A) Replace did not happen.
4. **Latency p50 is above our timeout.** Even with the 42% improvement, deck
   p50 is still 2.7s vs local 50–80ms. Replacing means waiting on deck for
   every turn.

### Why NOT (B) Hybrid yet

1. **The "augment value" question is still unanswered.** We turned the flag
   ON yesterday afternoon. We don't yet have a week of telemetry showing
   how often the augment path even fires in production traffic.
2. **Score-scale gap held.** Local top-1 mean 0.668, deck top-1 mean 0.509.
   Same +0.16 calibration debt as 18b. A naive merge ranks local above
   deck even when deck is right — needs a calibrated merger we haven't
   built.
3. **Coverage parity regressed (75% → 70%).** Until we understand WHY the
   deck started losing slots (new `documents_full` noise vs old
   `bedrock`/`vehicle` signal), merging deck into the local ranker would
   propagate that regression to all users.

### Stay at (C) Augment — proposed tuning patches

**Patch 1 — Bump timeout budget to 3.0s** (`DECK_SEARCH_TIMEOUT_S=3.0`).
Justification: today's p95 is 3.013s. A 3.0s budget would convert ~half
of the current 50% timeout-rate into real hits without meaningfully
hurting UX (3s is at the edge of acceptable for a chat reply, and the
augment path only fires when local has nothing). 2.5s was tuned against
18b's 4.7s p50; today's 2.7s p50 deserves a small slack.

**Patch 2 — Tighten the visitor `sources` whitelist** to exclude
`documents_full` for non-product-topic queries. Today our whitelist is
`[bedrock, vehicle, academy, sales_pitch, document]` — `academy` and
`document` aren't in the deck anyway (no-op), and `documents_full`
isn't in the whitelist so it's already filtered out for visitors. **No
change needed for visitors.** For verified employees the recommendation
is to keep the unrestricted call but rank `documents_full` hits down
client-side (e.g., −0.10 score adjustment) — since they're broad
PDFs that often crowd out more focused bedrock/vehicle chunks.

**Patch 3 — Add `documents_full` recognition in citation FE rendering.**
The citation chip should distinguish "deck full-document scan" from
"local vehicle masterclass". The `source_engine` is already set to
`deck_search`; one more flag `is_full_document_scan: true` would let
the FE render a different chip color. Cheap, optional, defers UX
ambiguity work until we see real production telemetry.

### Re-decision criteria (when to revisit)

We move from (C) Augment toward (B) Hybrid ONLY when ALL THREE of:

1. **One full week** of `deck_search_calls` telemetry showing the
   augment path firing at ≥ 5% of total turns and adding a net-positive
   answer quality (measurable via either `kb_audience_dropped_deck_hit`
   counts staying low OR a manual review of 20 random deck-augmented
   turns).
2. **Deck p95 drops to ≤ 2.0s** sustained (3 days running). This makes
   parallel local+deck retrieval an option without UX punishment.
3. **Either** the deck team adds server-side `audience` filtering **OR**
   we accept that the local belt-and-suspenders gate is our forever
   audience boundary and design the merge ranker around it.

We move to (A) Replace only if **all of the above** plus:

4. The deck ingests our `academy` and `document` subsources (the 1278+212
   missing chunks).
5. `documents_full` becomes joinable to local (or the deck team adds
   server-side enrichment so we can skip the join).

None of these are likely in the next sprint. **Plan for at least two
more probe passes before any architectural move.**

---

## Appendix A · Raw artefacts

* Probe summary JSON: `/app/deliverables/phase18c/summary.json`
* Per-call responses: `/app/deliverables/phase18c/raw/` (30+ files)
* 6 representative hit samples: `./deck_hits_shape/`
  * `01_AIF_Category_II.json` — bedrock-heavy schema query
  * `02_Purple_NCD.json` — vehicle + sales_pitch mix
  * `03_Retirement_v2.json` — top-scoring bedrock hits (0.74–0.81)
  * `04_en_documents_full_dominant.json` — illustrates `documents_full` taking all 5 slots for a generic English query
  * `05_hi_low_scores.json` — Hindi query, all scores < 0.32
  * `06_ta2_PMS_tamil.json` — Tamil "PMS திட்டம் என்ன", first hit `documents_full`
* Coverage parity rows: `./coverage_parity.md` + `./coverage_parity.json`
* Score histogram + threshold defence: `./score_histogram.md`
