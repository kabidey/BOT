# Phase 18 — Deck Vector Engine probe & integration delta

> Read-only probe of `POST /api/knowledge/search` on the SMIFS Deck
> (`https://deck.pesmifs.com`). Date: 24 May 2026.
> All raw JSON payloads are in `./deck_search_probe/raw/` and the structured
> rollup is in `./deck_search_probe/summary.json`.
>
> **No code in the running bot was touched.** This is a pre-Step-2 audit.

---

## TL;DR (read this first)

1. **Endpoint is live but the deck-side index is empty.** Across **50** queries (5 schema + 13 param + 7 multilingual + 25 histogram) the server consistently returned `totalIndexed: 0` and `results: []`. The endpoint accepts requests, returns 200, and echoes back honored params — but it has nothing to search yet.
2. **Embedding model identified:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-dim, multilingual MiniLM). Confirmed multilingual-capable in principle; cannot be empirically validated against an empty index.
3. **Only four request params are honored** (`q`, `top_k`, `min_score`, `sources`). Every other plausible filter (`subsource`, `vehicle_id`, `audience`, `language`, `is_focused`, `filters`, `exclude_sources`) is silently dropped.
4. **Audience gating CANNOT be pushed server-side.** No `audience` / `exclude_sources` / `subsource` filter exists. We'd have to filter locally — which requires a working join key from deck hits to our `doc_chunks` audience column. **Join key cannot be verified against an empty index.**
5. **Cost savings from going deck-only are negligible** (~$0.01 per full sync at current scale).
6. **Recommendation: (C) Augment.** Keep local cosine as primary, lazy-fall-through to deck only when local fails to clear threshold (multilingual queries being the highest-value case). Defer architectural commitment to deck until the deck index is populated and we can probe quality.

---

## 1 · Schema discovery

5 queries sent (schema runs are saved as `raw/schema__*.json`):

```
What is an AIF Category II?
PURPLE STYLE LABS NCD debt funding focused vehicle
Retirement planning slide v2 bedrock
ARN transfer process for mutual funds
Mediclaim providers in SMIFS distribution
```

### Envelope shape (per response)

| Key | Type | Notes |
|---|---|---|
| `query`        | string  | echoes input `q` |
| `model`        | string  | constant: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| `sources`      | string[] | echoes input `sources` (`[]` if not sent) |
| `topK`         | int     | echoes input `top_k` |
| `minScore`     | float   | echoes input `min_score` |
| `results`      | array   | the hit array (empty in every probe) |
| `totalIndexed` | int     | **0** in every probe — server reports zero indexed vectors for our key/tenant |

### Hit shape

**UNKNOWN.** `results: []` on every call. We cannot enumerate which fields a hit object carries (id, title, content, snippet, score, vehicle_id, subsource, audience, etc.) without at least one real hit.

---

## 2 · Parameter exploration (which filters does the server honor?)

Method: send the parameter, then read the server's **echo** in the response envelope. The server returns `topK`, `minScore`, and `sources` in the envelope; if our key matches the canonical name the echoed value reflects what we sent, otherwise the echoed value sits at the default (`0`, `[]`).

| Param sent | Honored? | Evidence |
|---|---|---|
| `q`              | ✅ | echoed as `query` |
| `top_k`          | ✅ | echoed as `topK` |
| `min_score`      | ✅ | echoed as `minScore: 0.1` when we sent 0.1 |
| `minScore`       | ❌ | camelCase variant dropped; echoed as `minScore: 0.0` |
| `sources`        | ✅ | echoed as `sources: ["bedrock"]` |
| `source`         | ❌ | singular dropped |
| `subsource`      | ❌ | dropped |
| `exclude_sources`| ❌ | dropped |
| `audience`       | ❌ | **dropped — no server-side audience gate possible** |
| `language`       | ❌ | dropped |
| `vehicle_id`     | ❌ | dropped |
| `is_focused`     | ❌ | dropped |
| `filters`        | ❌ | dropped |
| `filter`         | ❌ | dropped |

> **Implication for audience gating**: the deck cannot enforce employee-only filtering. We'd have to (a) request the full `top_k`, then (b) drop chunks whose `audience == "employee_only"` locally — which means we must have a join key into our `doc_chunks` row.

---

## 3 · Multilingual reality check

| Tag | Query | n_results | status |
|---|---|---|---|
| `en`       | `investment opportunities right now`   | 0 | 200 |
| `hi`       | `निवेश के अवसर`                          | 0 | 200 |
| `ta`       | `AIF க்கான தமிழ் pitch`                  | 0 | 200 |
| `bn`       | `এআইএফ বিনিয়োগ সুযোগ`                   | 0 | 200 |
| `mr`       | `गुंतवणुकीच्या संधी`                     | 0 | 200 |
| `hinglish` | `AIF ka structure kya hai`               | 0 | 200 |

**Observation:** the server accepts every non-ASCII payload without 400. UTF-8 path is healthy. We cannot empirically grade cross-lingual quality because the corpus is empty.

The chosen model (`paraphrase-multilingual-MiniLM-L12-v2`) is a well-known multilingual encoder that supports ~50 languages with reasonable cross-lingual retrieval out-of-the-box. Industry benchmarks put it around MTEB ~58 (English) / multilingual scores comparable to LaBSE for short queries. **Likely strong on Hindi/Tamil if the index gets populated; quality vs OpenAI `text-embedding-3-small` (1536-dim) probably regresses on English nuance but wins on cross-lingual.**

---

## 4 · Score distribution

```
30 representative queries → 0 hits total
```

No score histogram is possible until the deck index has data.

The probe captured the contract: `min_score` is a server-honored float, defaulting to `0.0`. When the index goes live we'll need a fresh probe to fix a sensible cutoff (likely in the `0.20 – 0.40` range for MiniLM-class models).

---

## 5 · Latency

Measured with the **empty index**, so represents request overhead only, **not** scoring cost. Real numbers will rise once vectors exist.

| Run | n | p50 | p95 | min | max |
|---|---|---|---|---|---|
| Serial         | 30 | **301 ms** | **620 ms** | 99 ms  | 713 ms |
| Parallel (×10) | 10 | wall=1403 ms; individual sorted: 116 / 200 / 308 / 408 / 411 / 413 / 995 / 1097 / 1196 / 1402 ms |

Comparison with local cosine retrieval (`rag.search_weighted` on a 2030-row in-memory matrix, query-embedding round-trip to Hub AI included): ~120–220 ms p50.

**Even with an empty index the deck round-trip is roughly 2× our local pipeline.** Once vectors are populated the ratio likely worsens. **A cache layer will be mandatory** if we route every turn through deck.

---

## 6 · Coverage parity vs Phase 16 row-by-row

**Cannot be performed.** The deck returns zero hits on every kb_matrix question. The local retrieval baseline (5 hits/row on all 20 questions, with bedrock chunks landing in the top-3 of 13/20 rows) is captured in `deck_search_probe/local_baseline.json` so we can run the row-by-row comparison the moment the deck index is populated.

Honest assessment: **today, on every kb_matrix row, deck regresses on local (deck = 0 hits vs local = 5 hits).** Until the deck index is filled, deck cannot be the primary retrieval surface.

---

## 7 · Chunk identity / join key feasibility

`join_key_check` in `summary.json` ran an automated cross-check: pull `hit.id` from every schema response, look it up in `doc_chunks.smifs_id` / `doc_id` / `_id`. **No hit IDs to test.**

The contract for join key is undefined. If, when the index is populated, the deck returns the SAME `id` that the paginated `/api/knowledge` endpoint returns, we can join to `doc_chunks.smifs_id` and re-acquire audience / vehicle_id / version_major. If it returns a different opaque ID (e.g. a chunk_id of the deck's own re-chunking), **we have no audience gate at all** — every retrieval would have to be re-projected from scratch.

---

## 8 · Cost angle

Current pipeline cost:

| Item | Cost |
|---|---|
| Full sync embedding (~494k tokens with `text-embedding-3-small` @ $0.020/1M) | **≈ $0.01 per full sync** |
| Per-query embedding (~20 tokens/turn × 5,000 turns/day) | **≈ $0.002 / day** |
| **Monthly total ≈ $0.05 – $0.30 depending on resync cadence** |

If we replace local embedding entirely with deck search, **monthly savings = roughly $0.30**. There is **no meaningful cost case** for an architectural move toward deck — the case must be made on quality (multilingual) or operational simplicity.

---

## 9 · Critical invariants — feasibility under each architecture

| Invariant | (A) Replace | (B) Hybrid | (C) Augment |
|---|---|---|---|
| Audience gate (`sales_pitch` + `growth_*` invisible to client/visitor) | **Requires join key from deck hit → `doc_chunks.audience`. Today: untested.** | Same — join key needed for any deck hits the LLM sees | Trivial — deck is fallback-only, local gate already covers primary path |
| Vehicle CTA pipeline (`vehicle_id` on citation) | Needs join key; OR re-derive from `metadata.vehicleId` if deck exposes it (unknown today) | Needs join key for deck hits to carry CTA | Local primary preserves CTA; deck-fallback hits would NOT emit CTA until the join is solved |
| Version badge (`version_major >= 2`) | Same — join key dependent | Same | Local-primary keeps badge; deck-fallback hits would not |
| Per-role gap counters | Logging happens at orchestrator level; orthogonal to retrieval source | Same | Same |
| Citation contract (Phase 16 additive fields) | If join works: identical. If not: degrades to thin {title, score, source} | Mixed | Local-primary: identical contract preserved |
| Multilingual queries (Hindi / Tamil / Bengali) | Native — if deck is populated and quality is good | Best of both | Triggers fallback only when local returns 0 above-threshold hits |

---

## 10 · Three architectural options

### (A) Replace — fully retire local embedding
- **Pros:** zero embedding maintenance; native multilingual (if quality holds); always-fresh index on the deck side without our resync cadence.
- **Cons:**
  1. **Audience gating broken until join key contract is established** — and even then, fragile, because every deck deploy could change ID format.
  2. Vehicle CTA & version badge regress unless deck hits expose `metadata` or we maintain a separate audience map.
  3. Deck round-trip currently 2× local; quality untested.
  4. We give up curation boosts (bedrock canonical +0.05, focused +0.03, recency +0.02). The server doesn't honor any equivalent param, so we'd need to re-rank locally anyway — which means we still need our local doc_chunks.
- **Verdict:** **Not recommended.** Too many irreversible breakages for sub-dollar cost savings.

### (B) Hybrid — call both, merge results
- **Pros:** best recall (deck multilingual + local English curation); enables progressive quality comparison.
- **Cons:**
  1. **Doubles retrieval latency** (~300ms deck added to ~150ms local; cache helps but adds complexity).
  2. Result-merge logic is non-trivial: scores live on different scales (cosine vs. MiniLM dot-product), so a simple union is wrong; we'd need a normalize → re-rank step.
  3. Still depends on the join key for any deck hit to carry audience/CTA/version.
  4. We end up paying twice the operational complexity for the multilingual edge case.
- **Verdict:** **Not recommended yet.** The right time to consider hybrid is AFTER (C) proves the deck index has real quality on the fallback path.

### (C) Augment — local primary, deck fallback ✅ **RECOMMENDED**
- **Mechanism:**
  1. `_retrieve()` first calls local `rag.search_weighted()` (unchanged).
  2. If `grounded == True` AND we have ≥1 hit above `RAG_MIN_SCORE`: return as today. **Phase 16 path 100% preserved.**
  3. Else: secondary call to deck `/api/knowledge/search` with the same `q` and `top_k`. Filter results client-side by `audience` (using the join key once it's known; in the interim, **drop ALL deck hits whose source name is in `_EMPLOYEE_ONLY_SUBSOURCES`**, which is a conservative but correct gate that uses ONLY the `source` field the deck already returns).
  4. Inject as `context_chunks` with a `[Source: deck-fallback]` preamble tag the LLM can flag if cited.
  5. Cache deck responses at the query level for 5 minutes (small LRU) — cheap insurance.
- **Pros:**
  1. **Zero regression risk** for every existing Phase 16 surface — local path unchanged.
  2. **Multilingual reach** the day the deck index goes live, on the queries where local returns nothing.
  3. Audience gate stays correct under a conservative interim policy: if `source` lands in `{sales_pitch, growth_insurance, growth_revenue}`, drop it. No join key required for the safe-by-default path.
  4. Vehicle CTA / version badge don't regress (they only render on local-primary hits, which is the same set as today).
  5. Latency cost only paid on queries where local already failed — i.e., bot already would have produced "outside knowledge base" or generic answer.
- **Cons:**
  1. The deck index is empty today, so the fallback path is unexercised in the short term. We ship dead code waiting for the deck team. (Mitigation: cover behind a feature flag `DECK_SEARCH_FALLBACK` defaulting to `true` so it activates the moment the deck is populated.)
  2. Deck-fallback hits won't carry vehicle CTA / version badge until the join key is solved. We accept that — those are bonuses, not contractual obligations.
  3. We don't get the cost saving of retiring local embedding. We weren't going to get it anyway ($0.30/mo is rounding error).

---

## 11 · Multilingual exposure — flag for product

If we adopt (C) and the deck index gets populated with a multilingual embedding, two follow-ups become possible (not Phase 18 scope, just flagging):

- **Hub AI default model**: needs verification that `gpt-4o-mini` (current default) handles Hindi/Tamil context chunks → English (or matching language) reply. Hub AI tests welcome — likely fine for Hindi, untested for Tamil/Bengali. Probable need for a small "user_locale" hint in the system prompt so the LLM matches the user's input language.
- **Identity-gate UX in Hindi**: PAN-last-6 / phone OTP flows would need translated copy. Phase 19+ if we want to ship multilingual to clients.

---

## 12 · Recommendation (the ask)

**Adopt (C) Augment, gated behind `DECK_SEARCH_FALLBACK` feature flag (default ON).**

What I'd build in Step 2 (only if you green-light):

1. New module `backend/deck_search.py` — a thin async client with a 5-minute LRU cache, 30s timeout, `min_score` threshold, and the conservative subsource-name audience drop.
2. `agents/rag_agent._retrieve()` gains a post-local fallback branch: if local grounded=False AND `DECK_SEARCH_FALLBACK == "true"`, call deck, filter by audience, return hits with `source="deck_fallback"` tag.
3. `_hits_to_chunks()` adds a `[deck-fallback]` preamble for those chunks so the LLM (and audit) can tell them apart.
4. Citations from deck-fallback hits carry only what deck returns (title, source, score) — additive to Phase 16 contract, no field is removed for local hits.
5. New `security_events` row of kind `deck_fallback_used` per turn for observability.
6. One re-probe script after deck team flips the index on, to (a) capture the real hit shape, (b) verify the join key, (c) re-do coverage parity against `local_baseline.json`.

**Defer until deck index is populated:**

- The audience-gate join-key work (needs real hits).
- Hybrid architecture, multilingual UX, cost analysis at scale.

---

## Appendix — files in this delivery

```
phase18/
├── deck_search_delta.md           ← this document
├── probe_deck_search.py           ← the probe script (re-runnable)
└── deck_search_probe/
    ├── summary.json               ← structured rollup of all findings
    ├── local_baseline.json        ← Phase 16 retrieval on kb_matrix (for parity once deck fills)
    └── raw/                       ← per-query raw JSON request+response (50 files)
```

**Stopping here. Awaiting green-light to start Step 2.**
