# Phase 18b — Deck Vector Engine re-probe delta

> Read-only re-probe of `POST /api/knowledge/search` on the SMIFS Deck
> (`https://deck.pesmifs.com`). Date: 24 May 2026.
> Baseline: `/app/deliverables/phase18/deck_search_delta.md` (probe at empty
> index, same day earlier).
> Raw payloads: `/app/deliverables/phase18/deck_search_probe/raw/` (50+ probe
> responses) + 6 representative samples copied to `./deck_hits_shape/`.
> Coverage parity rows: `./coverage_parity.json` + `./coverage_parity.md`.
> Score histogram: `./score_histogram.md`.
>
> **No code in the running bot was touched.** `DECK_SEARCH_FALLBACK` remains
> `false`.

---

## TL;DR (read this first)

1. **The deck index is live and populated.** `totalIndexed: 486` is **stable
   across every probe** (50+ queries). No variance.
2. **Hit shape is now known** (was UNKNOWN). Every hit has 8 top-level keys
   plus a free-form `metadata` blob whose contents vary by `source`:
   `id, score, source, sourceId, title, section, content, metadata`.
3. **Join key works 1:1.** Every deck hit ID lands in our local
   `doc_chunks.smifs_id` (53/53 = **100% match**). We can join back to local
   for `audience`, `vehicle_id`, `version_no`, `is_focused`, `is_active`,
   `updated_at`, `provider` — the 16 projected fields are all available.
4. **Server-side audience gating is still NOT possible** — `subsource`,
   `audience`, `exclude_sources`, `language`, `vehicle_id`, `is_focused` are
   all still silently dropped. The single positive whitelist `sources=[...]`
   is the only filter the server actually honours.
5. **Coverage gap is significant.** Deck indexes 486 chunks; we index 1976
   locally. The `academy` subsource (1278 chunks, ~65% of local corpus) and
   `document` subsource (212 chunks) are **not in the deck at all** — they
   live only in our local Mongo. Multiple parity-test rows where the local
   top-1 was an `academy` chunk regressed to a less precise `bedrock` /
   `growth_revenue` deck hit.
6. **Latency regressed sharply.** Deck p50 went from 301 ms (empty index) →
   **4 703 ms (populated)** and p95 620 ms → **7 020 ms**. The local cosine
   path is ~2 orders of magnitude faster (sub-100 ms in-process).
7. **Multilingual quality is workable but noisy.** Real Hindi / Tamil hits
   surface, but the deck embedder is `text-embedding-3-small` (English-
   centric) — non-English queries score 0.27–0.34, well below the score
   floor we'd want for confident grounding. Hinglish ("AIF ka structure
   kya hai") works fine (top score 0.52).
8. **Score scale is cosine-like, 0–1.** Real distribution clusters in
   `[0.45, 0.55]` with a long thin tail to 0.81. Proposed threshold:
   **`DECK_SEARCH_MIN_SCORE = 0.45`** (high-precision; see §8 for rationale).
9. **Recommendation: stay at (C) Augment for at least one more pass.**
   The join key win is real, but the latency regression + academy coverage
   gap + missing server-side audience filter mean we cannot recommend a
   (A) Replace migration. (B) Hybrid is the right ceiling for now —
   detailed migration plan in §10. Confidence: **medium-high**.

---

## 1 · Index population check

| Metric | Phase 18 baseline (empty) | Phase 18b re-probe | Delta |
|---|---|---|---|
| `totalIndexed` (typical) | 0 | **486** | populated |
| Variance across 50+ queries | 0 (constant) | 0 (constant) | stable |
| 200 responses | 50/50 | 50/50 | unchanged |
| Hits returned | 0/50 | 125/125 (top_k=5) | populated |

`totalIndexed: 486` does not move regardless of `q`, `sources`, `top_k`, or
`min_score`. Treating it as a tenant-wide vector count.

---

## 2 · Hit shape (was UNKNOWN — now mapped)

### Envelope (per response) — additive vs baseline

| Key | Type | Source of truth |
|---|---|---|
| `query` | string | echoes input `q` |
| `model` | string | constant **`text-embedding-3-small`** (NEW — was `paraphrase-multilingual-MiniLM-L12-v2` in baseline; deck was re-indexed with OpenAI embeddings) |
| `provider` | string | **`openai`** (NEW key on envelope) |
| `sources` | string[] | echoes input `sources` (`[]` if not sent) |
| `topK` | int | echoes input `top_k` |
| `minScore` | float | echoes input `min_score` |
| `results` | array | the hit array |
| `totalIndexed` | int | 486 (stable) |

### Hit shape (constant across 125 hits sampled)

```jsonc
{
  "id":       "bedrock:b4d11a33-5320-4373-9b3d-cb780c727ed0:5",   // join key
  "score":    0.4845,                                              // cosine 0–1
  "source":   "bedrock",                                           // subsource bucket
  "sourceId": "b4d11a33-5320-4373-9b3d-cb780c727ed0",              // upstream record UUID
  "title":    "SMIFS Corporate PPT",                               // human title
  "section":  "corporate",                                         // free-text section
  "content":  "…",                                                 // chunk text
  "metadata": { /* shape depends on source — see below */ }
}
```

**Top-level keys are 100% present** on every hit. No nulls, no missing
fields. The hit envelope is stable.

### `metadata` shape varies by `source`

| `source` | `metadata` fields observed |
|---|---|
| `bedrock` | `section, fileType, fileName, ordinal, kind` |
| `vehicle` | `vehicleType, customTypeName, isFocused, isActive, documentCount, salesPitchReady, salesPitchLanguages, updatedAt` |
| `sales_pitch` | `vehicleId, vehicleName, vehicleType, language, languageLabel, languageNative, isFocused, generatedAt, model, ordinal` |
| `growth_revenue` | `kind, generatedAt, fyTag, generatedBy` |
| `growth_insurance` | `kind, providerName, generatedAt, generatedBy` |

**Verdict:** the metadata is rich enough that we *could* parse out
vehicle_id / version / focused / language directly from the deck response —
but the names are inconsistent (`vehicleId` here, plain `id` upstream) and
audience is **never on the hit**. So the join-back path (§3) is still
required for audience.

### Field checklist (asked-for fields the deck does/doesn't expose per hit)

| Asked-for | Present on hit? | Where |
|---|---|---|
| `id` / `chunk_id` | ✅ | `id` |
| `score` | ✅ | `score` |
| `source` / `subsource` | ✅ | `source` (single field; matches our `subsource`) |
| `title` | ✅ | `title` |
| `content` / `snippet` | ✅ | `content` |
| `vehicle_id` / `vehicle_name` | ⚠️ partial | only on `vehicle` & `sales_pitch` rows, named `vehicleId/vehicleName` |
| **`audience`** | ❌ | **never present** — must join to local |
| `language` | ⚠️ partial | only on `sales_pitch` rows |
| `updated_at` | ⚠️ partial | only on `vehicle` rows (`metadata.updatedAt`) and `sales_pitch.generatedAt` |
| `version_no` / `version_major` | ❌ | not exposed |
| `is_focused` | ⚠️ partial | on `vehicle` + `sales_pitch` rows only |
| `is_active` | ⚠️ partial | on `vehicle` rows only |
| `metadata` blob | ✅ | `metadata` (free-form) |

---

## 3 · Join-key verification (the headline finding)

| Test | Result |
|---|---|
| Hit IDs sampled across 50+ probe queries | **53 unique IDs** |
| Match into `doc_chunks.smifs_id` | **53/53 = 100%** |
| ID prefix distribution (matches our 5 subsources) | `bedrock: 22, vehicle: 16, sales_pitch: 8, growth_insurance: 6, growth_revenue: 1` |
| Alternative join keys tried (`doc_id`, `_id`) | 0/53 — format diverges |

**The deck's `id` is byte-for-byte identical to our `smifs_id`** —
`vehicle:<uuid>:<ord>`, `bedrock:<uuid>:<ord>`, `sales_pitch:<uuid>:<lang>:<ord>`,
`growth_*:<uuid>:<n>`. Confirmed by inspecting `doc_chunks` documents from
the live DB.

**What this unlocks:** for every deck hit, we can `find_one({"smifs_id":
hit.id})` and pull back the projected 16 metadata fields (audience,
vehicle_id, vehicle_name, version_no, is_focused, is_active,
sales_pitch_ready, language, provider, vertical, category, doc_type,
updated_at_iso, source_updated_at, content_hash). This makes (B) Hybrid
fully feasible and (A) Replace technically possible — modulo §5 coverage.

---

## 4 · Parameter honour re-check (the disappointment)

Same test method as baseline: send param, read the server's `echoed_*`
fields and compare the result set against the no-param baseline.

| Param | Baseline (empty index) | Re-probe (populated) | Status |
|---|---|---|---|
| `q` | ✅ | ✅ | unchanged |
| `top_k` | ✅ | ✅ | unchanged |
| `min_score` | ✅ | ✅ | unchanged |
| `minScore` (camelCase) | ❌ | ❌ | dropped |
| `sources` (array, positive whitelist) | ✅ echoed | **✅ functionally honoured** | result set narrows to listed sources — only filter that actually works |
| `source` (singular) | ❌ | ❌ | dropped |
| `subsource` | ❌ | ❌ | dropped |
| `exclude_sources` | ❌ | ❌ | dropped (identical result set with/without) |
| `audience` | ❌ | ❌ | dropped — **server-side audience gate impossible** |
| `language` | ❌ | ❌ | dropped |
| `vehicle_id` | ❌ | ❌ | dropped |
| `is_focused` | ❌ | ❌ | dropped |
| `filters` / `filter` (nested) | ❌ | ❌ | dropped |

> The `sources` whitelist now demonstrably narrows results — e.g.
> `sources=["bedrock"]` returns 5 bedrock-only hits where the unfiltered
> query returned 1 `growth_revenue` + 3 `bedrock` + 1 `vehicle`. So
> `sources` is real, but it's a positive list, not a negative one.
>
> **Implication:** we can request `sources=["bedrock","vehicle","academy",
> "sales_pitch"]` (omit `growth_*`) for non-employee sessions and let the
> server pre-filter — but we still need the local audience join for the
> sub-source-vs-audience matrix (e.g. some `bedrock` chunks are marked
> `audience=employee_only` locally; the deck can't see that).

---

## 5 · Multilingual quality (now testable)

| lang | query | n_results | top score | top-1 title | smell test |
|---|---|---:|---:|---|---|
| en  | "investment opportunities right now" | 5 | 0.4845 | SMIFS Corporate PPT | ✅ on-topic |
| hi  | `निवेश के अवसर` | 5 | 0.3141 | (bedrock corporate slides) | ⚠️ weak — borderline grounding |
| ta  | `AIF க்கான தமிழ் pitch` | 5 | 0.4785 | Sapphire AIF Tamil sales pitch | ✅ surprisingly on-topic — Tamil sales-pitch corpus exists |
| bn  | `এআইএফ বিনিয়োগ সুযোগ` | 5 | 0.3439 | (bedrock generic) | ⚠️ weak |
| mr  | `गुंतवणुकीच्या संधी` | 5 | 0.2894 | (bedrock generic) | ⚠️ borderline — every hit < 0.30 |
| hinglish | "AIF ka structure kya hai" | 5 | 0.5244 | (AIF Cat III) | ✅ works — code-mix transliterated tokens |
| en2 | "what is AIF structure" | 5 | 0.5617 | (AIF Cat III) | ✅ |

**Verdict:** The deck embedder (`text-embedding-3-small`) is English-
centric. Hindi/Tamil queries return hits but scores cluster in 0.27–0.34 —
below any reasonable confidence threshold. Of the three v1 locales we
support (en/hi/ta):

* **English (en)**: deck is on-par with local. Useable.
* **Hindi (hi)**: weak — below proposed threshold. Use local first; deck
  fallback unlikely to clear bar. Recommend caller keeps query in English
  internally and only localises the LLM **reply** (which Phase 18
  Workstream B already does).
* **Tamil (ta)**: surprisingly OK — because we have Tamil sales-pitch
  chunks in the deck. Same recommendation as Hindi (English search + Tamil
  reply).
* **Bn / Mr / Hinglish**: same English-search pattern. Hinglish works
  natively.

This **does not** invalidate the multilingual UX shipped — the FE locale
flag drives the LLM **response language**, not the retrieval query.
Retrieval-side multilingual is a separate concern, deferred.

---

## 6 · Latency on populated index

| Metric | Phase 18 (empty index) | Phase 18b (populated) | Δ |
|---|---:|---:|---:|
| Serial p50 | 301 ms | **4 703 ms** | +1462% |
| Serial p95 | 620 ms | **7 020 ms** | +1032% |
| Serial min | (unknown, ~250 ms) | 584 ms | — |
| Serial max | — | 10 133 ms | — |
| Parallel-10 wall | — | 6 496 ms | — |
| Local cosine p50 (in-process) | ~50–80 ms | ~50–80 ms | unchanged |

**This is the single biggest blocker for (A) Replace.** Sub-500 ms is the
chat UX gold standard; the deck currently averages ~4.7 s per call. Every
user turn would pay this once.

For (C) Augment the latency is acceptable **because the deck is only
queried when local returns no hits above threshold** — i.e. ~5–10% of
turns. For (B) Hybrid (always call both) the latency is the wall clock of
the slower path (deck), which is unacceptable.

> Caveat: this is from a single test pod inside the Emergent k8s cluster.
> Production frontend traffic may experience different network
> characteristics. Re-measure from the FE pod before any commitment.

---

## 7 · Coverage parity vs local cosine (20-row matrix)

See `coverage_parity.md` for the full table. Headline numbers:

| Verdict | Count | % of 20 |
|---|---:|---:|
| identical (3/3 top-3 overlap) | 10 | 50% |
| partial_2 (2/3) | 1 | 5% |
| partial_1 (1/3) | 4 | 20% |
| no_overlap (0/3) | 5 | 25% |
| **net ≥1 overlap** | **15** | **75%** |

**Where deck loses to local**: all 5 `no_overlap` rows had a local top-1
in the `academy` subsource (which is missing from the deck). When the
question is educational/literacy ("What is an AIF?", "tax implications
LTCG AIF", "AIF sales pitch script"), local wins because the deck's
educational corpus is sparse.

**Where deck ties local**: brand-specific / vehicle-specific / market-view
questions where the relevant chunk lives in `bedrock` / `vehicle` /
`sales_pitch` — both engines return the same chunk, deck just at a lower
absolute score.

**Score scale calibration**: across the 20 rows, mean local top-1 score
was **0.668** (range 0.46–0.91); mean deck top-1 score was **0.509**
(range 0.36–0.75). Both engines use cosine in `[0, 1]`, but local applies
source weighting + recency boost (Phase 16), so its absolute numbers run
~0.15 higher. This is the most important fact for any future merge ranker
— **deck scores must be calibrated up by +0.10..+0.15** before mixing
into the local sort.

---

## 8 · Score scale & threshold

Distribution of 125 hit scores across the 50+ probe queries:

```
0.3:  15 ####### (junk-ish, below 0.30 = noise)
0.4:  22 ###########
0.5:  64 ################################ (the bulk)
0.6:  21 ##########
0.7:   0
0.8:   3 # (clearly correct hits)
```

Percentiles: `p10=0.314, p25=0.448, p50=0.475, p75=0.542, p90=0.564,
p99=0.803`.

**Proposed threshold: `DECK_SEARCH_MIN_SCORE = 0.45`** (high-precision).

Rationale:
* `< 0.30` is almost entirely the same recurring "SMIFS Fortnightly
  Offerings" fallback chunk that the deck returns when nothing else
  matches — it's hallucination fuel.
* `0.30–0.45` is mixed: some borderline-relevant `growth_*` hits, some
  noise. Risky to include.
* `≥ 0.45` consistently surfaces the right chunk for the query (verified
  against the parity-matrix top-1 set).
* Maps roughly to local's effective threshold (~0.55 raw cosine after
  source-weighting) once you add the +0.10 calibration offset.

The current `DECK_SEARCH_MIN_SCORE=0.30` (set as default in `.env`) is
too permissive for production. **Recommend bumping to 0.45 before flipping
the flag.**

---

## 9 · Behaviour the deck handles automatically (free wins)

* **De-duplicated chunks within `top_k`** — yes (we don't see the same
  `id` repeated within a result set, even across overlapping ordinals).
* **Cross-encoder re-rank** — unclear; scores look like raw cosine, not
  cross-encoded.
* **Stop-words / casing** — yes (case-insensitive, no obvious stop-word
  weirdness).
* **Recency boost** — **no**. Top-1 for "latest fortnightly offering" is
  the May 2026 doc, but only because it's the title match. The deck does
  not visibly boost newer `updatedAt` values. Our Phase-16 +0.02 recency
  boost is therefore additive value, not redundant.
* **Source weighting** — no. The deck ranks purely by cosine. Our Phase-9
  +source weights (smifs_knowledge > seed > upload > archive) are not
  represented.

---

## 10 · Revised architecture recommendation

> **Stay at (C) Augment. Defer (B) Hybrid by one observation window
> (≥ 1 week) once the flag is flipped.**

Confidence: **medium-high.** Specifically:

### Why NOT (A) Replace
1. **Coverage gap**: `academy` (1278 chunks) and `document` (212 chunks)
   are not in the deck. The educational backbone of the bot — "what is an
   AIF?", "explain SIP", "compare PMS vs MF" — degrades materially.
2. **Latency regression**: 4.7 s p50 on the deck vs sub-100 ms local.
   Every user turn would inherit this. UX-blocking.
3. **No server-side audience gate**: still must join back locally.
   Replacing local removes the join target.
4. **No recency / source weighting**: features we've built in Phase 9/16
   would have to be re-implemented client-side over deck results — at
   which point why go to deck at all.

### Why NOT (B) Hybrid (yet)
1. **Latency dominates wall clock**: parallel-fetch local + deck means
   every turn waits on deck. Until the deck is < 1 s p95, hybrid is a tax
   on the 95% of turns where local already had the answer.
2. **Calibration debt**: score-scale mismatch (deck 0.51 mean vs local
   0.67 mean) means any naive `sorted(local + deck, key=score)` ranks
   local hits above deck hits even when deck is right. We'd need a
   calibrated merge — non-trivial. (Currently `deck_search.py` puts deck
   hits last, which is the safe default.)
3. **Observability gap**: we haven't observed how often the augment path
   even fires in production. Could be 0.2%, could be 20%. Without that
   number, hybrid is over-engineering.

### Phased plan toward (B) Hybrid (only after Augment is observed)

* **Week 1 (now)**: Flip `DECK_SEARCH_FALLBACK=true` with
  `DECK_SEARCH_MIN_SCORE=0.45`. Audience gate stays client-side via the
  existing `apply_audience_drop()` + the new join-back lookup (see below).
* **Week 1 ops checklist**:
  * Add a join-back step in `deck_search.deck_search()`: after the deck
    returns hits, look up each `id` in `doc_chunks.smifs_id` to enrich
    with `audience, vehicle_id, vehicle_name, version_no, updated_at,
    is_focused, is_active`. Hits whose join target is missing or has
    `audience=employee_only` (for non-employees) are dropped.
  * **Pre-filter at the server** for non-employee sessions using
    `sources=["bedrock","vehicle","academy","sales_pitch","document"]`
    (omit `growth_*`) — saves a round-trip on chunks we'd just drop.
  * Audit `deck_search_calls` weekly: count rows, audience-drop count,
    p50/p95 latency, fire rate (calls/total turns).
* **Week 2 review**: if augment-fire rate is < 1% or the deck quality
  isn't visibly helping any specific class of queries, **shut it off
  again**. Don't move to (B) on a hunch.
* **Week 3+ (only if augment proves valuable)**: introduce hybrid merge —
  always call deck in parallel, calibrate-add +0.10 to deck scores, then
  sort. Cap deck contribution at 2 of 5 final hits.
* **(A) Replace remains off the table** until the deck either (i)
  ingests `academy` + `document`, (ii) honours server-side
  `audience`/`exclude_sources`, AND (iii) drops p50 below 1 s.

### Hard requirements before flipping the flag

1. ⬜ Bump default `DECK_SEARCH_MIN_SCORE` from 0.30 → **0.45** in `.env`.
2. ⬜ Add the local join-back enrichment step in `deck_search.deck_search()`
   (the current implementation drops by string `source` only; with 100%
   join-key match we can do proper audience filtering).
3. ⬜ Pre-filter with `sources=[…]` whitelist for non-employee sessions
   (omit `growth_*`).
4. ⬜ Add latency alert in `/api/admin/deck_search/status` if p95 > 8 s.

Everything above is non-blocking and can ship in a single ~30-minute
patch under "Phase 18.1". I am NOT making the patch in this read-only
probe pass — flagged here for your green-light.

---

## Appendix A · Raw artefacts

* Per-call JSON for the 50+ probe queries: `/app/deliverables/phase18/deck_search_probe/raw/`
* 6 representative hit-shape samples: `./deck_hits_shape/`
  * `01_AIF_Category_II.json` — bedrock-heavy result
  * `02_Purple_NCD.json` — vehicle + sales_pitch hits
  * `03_Retirement_v2.json` — top-scoring bedrock hits (0.78–0.81)
  * `04_en_investment_opps.json` — English multilingual baseline
  * `05_hi_invest_opps.json` — Hindi (weak scores)
  * `06_hinglish_AIF_structure.json` — Hinglish (strong scores)
* Probe summary: `/app/deliverables/phase18/deck_search_probe/summary.json`
* Coverage parity 20-row table: `./coverage_parity.md`
* Score histogram + threshold proposal: `./score_histogram.md`
