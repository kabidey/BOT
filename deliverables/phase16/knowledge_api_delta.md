# Phase 16 — SMIFS Knowledge API · Field Delta + Integration Plan

> Status: **STEP 1 only** (probe + delta). Awaiting checkpoint before any
> code change per spec instruction *"Pause after Step 1 and send me the delta
> report. Don't barrel through to Step 5 without that checkpoint."*

---

## 0 · TL;DR (read this first)

* The API surface is **structurally the same** — only the original two
  endpoints respond. **No new endpoints.** `/search`, `/categories`, `/tags`,
  `/topics`, `/sources`, `/doc_types`, `/products`, `/related`, `/faq`,
  `/version`, `/health`, `/openapi.json` all return **404**. Server-side
  query / filter / search params (`q`, `search`, `query`, `source`,
  `vehicleType`, `section`) are **silently ignored** — the API returns the
  same paginated set regardless. Retrieval continues to be entirely
  client-side semantic search after we ingest the corpus.
* The corpus grew **+10 %**: `totalChunks` `1801 → 1977` (`+176`).
* The `/api/knowledge/stats` payload tripled in size — **18 dimensions**
  vs the previous handful.
* The chunk payload **gained 4 new subsources** the existing
  `knowledge_sync.py` knows nothing about: `sales_pitch`, `bedrock`,
  `growth_insurance`, `growth_revenue`. They DO get ingested today (the
  loader is permissive), but their `metadata` fields are dumped wholesale
  into `smifs_metadata` and **none of those signals flow into citations,
  retrieval, or LLM prompt**.
* Several pre-existing subsources got **new per-chunk metadata** the loader
  never reads: `vehicleId / vehicleName` on `document` and `sales_pitch`
  chunks (lets us tag any document chunk back to its parent vehicle),
  `vehicleType / isFocused / isActive / salesPitchReady / salesPitchLanguages`
  on `vehicle` chunks, `versionNo / collateralNo / kind / isVideo` on
  `bedrock` chunks, `provider / category / vertical / label / tableCount /
  rowCount / updatedBy` on `growth_*` chunks.
* There is **no `effective_date`, no `confidence`, no `authoritative_source`
  flag** that the spec hoped for. The closest proxies are `metadata.updatedAt`
  (per-chunk), `metadata.versionNo` (bedrock subset), and the curation flags
  `isFocused / isActive / salesPitchReady`. **Reframing Step 3 around these
  proxies is the judgement call we need your sign-off on.**

---

## 1 · Endpoint surface — what's live vs new

| Method · Path | Status | Notes |
|---|---|---|
| `GET /api/knowledge/stats` | **200** | Returns 18 counters (was ~5 before). |
| `GET /api/knowledge?limit=N&offset=K` | **200** | Single bulk pump. Returns `{stats, chunks[], totalAvailable}`. **Filter / search params are accepted with HTTP 200 but ignored.** Walking offset 0…1977 hits all chunks. |
| `GET /api/knowledge/search?q=…` | 404 | Not implemented. |
| `GET /api/knowledge/categories \| /tags \| /topics \| /sources \| /doc_types \| /products \| /related \| /faq` | 404 each | None implemented. |
| `GET /api/knowledge/version \| /health` | 404 | No version stamp. |
| `GET /api/openapi.json` | 404 (under `/api`); 200 on root `/openapi.json` but returns a **placeholder swagger UI HTML** (4.7 kB, non-JSON), not a machine-readable spec. |

**Conclusion**: the integration plan does NOT get any new endpoints to call.
All gains come from richer per-chunk metadata we currently discard.

---

## 2 · `/api/knowledge/stats` — old vs new dimensions

```json
{
  "vehicles": 168,           "vehiclesActive": 166,       "vehiclesFocused": 2,
  "documents": 231,          "documentsWithSummary": 212,
  "academyCourses": 11,      "academyChapters": 40,       "academyPages": 473,
  "bedrockItems": 81,        "bedrockExtractable": 3,
  "bedrockBrandEquity": 78,  "bedrockCached": 12,
  "growthInsuranceProviders": 12, "growthInsuranceRows": 122,
  "growthRevenueVerticals": 3,    "growthRevenueRows": 60,
  "salesPitchVehicles": 2,        "salesPitchLanguageTotal": 26
}
```

NEW dimensions worth surfacing on the admin KB tab + knowledge_sync run
records:

* `vehiclesFocused`, `vehiclesActive` — coverage / curation signal.
* `documentsWithSummary` — % of docs that have AI summaries.
* `bedrockExtractable / BrandEquity / Cached` — bedrock asset health.
* `growthInsuranceProviders`, `growthRevenueVerticals` — knowledge breadth.
* `salesPitchVehicles`, `salesPitchLanguageTotal` — pitch coverage.

---

## 3 · Subsources — what the chunk stream actually contains

Walked offsets 0…1977 to map subsource boundaries. The chunks come in **a
single ordered stream** — no per-source pagination — in this order:

| Order | Source | Count | Cum. offset | Currently handled by loader? |
|-------|--------|------:|------------:|------------------------------|
| 1 | `vehicle`           | 168 | 0–167 | partial (only metadata.vehicleType etc dumped to smifs_metadata) |
| 2 | `document`          | 212 | 168–379 | partial (no vehicleId linkage used) |
| 3 | `sales_pitch`       | 69 | 380–448 | **NOT explicitly handled** — falls through default |
| 4 | `academy`           | 1278 | 449–1726 | YES (only subsource with a proper `_section_for` branch) |
| 5 | `bedrock`           | 235 | 1727–1961 | partial (top-level fileName picked up, new fields ignored) |
| 6 | `growth_insurance`  | 12 | 1962–1973 | **NOT explicitly handled** |
| 7 | `growth_revenue`    | 3 | 1974–1976 | **NOT explicitly handled** |

(Counts derived from the stats payload — `vehicles 168 + documents 212 +
sales_pitch 69 + academy 1278 + bedrock 235 + growth_insurance 12 +
growth_revenue 3 = 1977`.)

---

## 4 · Per-subsource metadata — what we read today vs what's now offered

Legend:  ✅ already read  · 🆕 in payload, not read · ⚠ shape mismatch

### 4.1 · `source: "vehicle"`

```json
"metadata": {
  "vehicleType": "PMS",        🆕  (was unused — primary product family signal)
  "customTypeName": "",        🆕  (free-text override for vehicleType)
  "isFocused": false,          🆕  (curation: "house view" picks)
  "isActive": true,            🆕  (curation: currently-offered)
  "documentCount": 0,          🆕  (link count back to its document chunks)
  "salesPitchReady": false,    🆕  (whether a sales_pitch chunk exists)
  "salesPitchLanguages": [],   🆕  (e.g. ["en","hi"])
  "updatedAt": "2026-03-24…"   ✅
}
```

### 4.2 · `source: "document"`

```json
"metadata": {
  "vehicleId": "077fd2de-…",   🆕  ← parent-vehicle linkage (gold)
  "vehicleName": "Alchemy …",  🆕
  "fileType": "pdf",           🆕  (filtering / search ranking signal)
  "fileName": "…Factsheet.pdf",✅  (already used for section)
  "ordinal": 0                 ✅
}
```

### 4.3 · `source: "sales_pitch"` (id format `sales_pitch:<vehicle>:<lang>:<n>`)

```json
"metadata": {
  "vehicleId": "5940616d-…",   🆕
  "vehicleName": "Bharat …",   🆕
  "language": "en",            🆕  (derived from id, also in metadata — TBD)
  "ordinal": 0                 ✅
}
```

### 4.4 · `source: "academy"`

```json
"metadata": {
  "courseId": "AIF",           ✅ (already in _doc_title_for)
  "courseTitle": "Alternative…",✅
  "chapterId": "aif-3",        🆕  (new — wasn't in old payload)
  "chapterTitle": "…",         ✅
  "pageTitle": "…",            ✅
  "pageIndex": 7,              ✅
  "ordinal": 0                 ✅
}
```

### 4.5 · `source: "bedrock"`

```json
"metadata": {
  "section": "product",        🆕  (different "section" than top-level!)
  "fileType": "pdf",           🆕
  "fileName": "SMIFS_…pdf",    ✅
  "ordinal": 18,               ✅
  "kind": "body",              🆕  ("body" | "title" | "footer" | …)
  "isVideo": false,            🆕  (newer bedrock chunks only)
  "versionNo": 8,              🆕  (NEWER chunks — version stamp)
  "collateralNo": 122          🆕  (asset enumeration)
}
```

### 4.6 · `source: "growth_insurance"`

```json
"metadata": {
  "vertical": "Insurance",     🆕
  "provider": "HDFC Life",     🆕  ← key for "what insurance providers does
                                     SMIFS support?" queries
  "category": "Term",          🆕
  "tableCount": 3,             🆕  (structured data — N tables in the chunk)
  "rowCount": 28,              🆕
  "updatedAt": "…",            ✅
  "updatedBy": "…"             🆕
}
```

### 4.7 · `source: "growth_revenue"`

```json
"metadata": {
  "vertical": "Wealth Mgmt",   🆕
  "label": "FY26 Q3 dashboard",🆕
  "tableCount": 2,             🆕
  "rowCount": 12,              🆕
  "updatedAt": "…",            ✅
  "updatedBy": "…"             🆕
}
```

---

## 5 · How this maps to Step 2 (integration) — proposed plan

I'll only execute this once you OK the delta.

### 5.1 · `knowledge_sync.py` — keep, extend per-subsource projector

Today there's one switch on `subsource` (`academy` vs `document`) producing
the curated `section` + `doc_title`. Extend to a small projector function
that returns:

```python
{
  "doc_id":          str,    # cluster citations
  "doc_title":       str,    # human-readable
  "section":         str,    # "AIF › Sales Pitch › Adapting your pitch …"
  "doc_type":        str,    # 'vehicle','document','sales_pitch','academy',
                             # 'bedrock','growth_insurance','growth_revenue'
  "vehicle_id":      str?,   # NEW — backlink for vehicle/document/sales_pitch
  "vehicle_name":    str?,   # NEW
  "vehicle_type":    str?,   # 'AIF' / 'PMS' / 'MF' / 'NCD' / …
  "language":        str?,   # 'en' / 'hi' for sales_pitch
  "kind":            str?,   # bedrock body/title/footer
  "version_no":      int?,   # bedrock versioned content
  "is_focused":      bool?,  # vehicle curation
  "is_active":       bool?,  # vehicle curation
  "provider":        str?,   # growth_insurance
  "category":        str?,
  "updated_at":      str?,   # ISO — closest thing we have to effective_date
  "tags":            list[str], # synthesised from doc_type, vehicle_type,
                                # provider, category, focused/active flags
}
```

Persist all of this on the `doc_chunks` row alongside `smifs_metadata`. Two
new helper indexes on `doc_chunks`: `(source, doc_type)` and `(vehicle_id)`
for cheap filtered retrieval.

### 5.2 · Chunking strategy

The API delivers chunks pre-chunked at the source (each row IS a chunk). We
**do not re-chunk** today and I don't propose changing that — re-chunking
would break the API's own ordinal sequence + we'd lose the metadata→chunk
1:1 mapping. The right move is to (a) preserve the API's chunk boundaries
and (b) enrich each chunk's metadata before embedding (already what we do —
just need broader projection).

### 5.3 · LLM prompt assembly (`agents/rag_agent.py`)

Today each context chunk is shipped as `{title, section, text}`. Extend the
per-chunk envelope sent into the LLM to:

```
[Doc: <doc_title>]   [Section: <section>]
[Type: <doc_type>]   [Vehicle: <vehicle_name> · <vehicle_type>]
[Updated: <updated_at>]   [Version: v<version_no>]
[Focused: yes/no · Active: yes/no]
---
<text>
```

Pure tag-style preamble — no extra LLM call, no extra tokens for chunks
that don't have a value (omit blank lines). The system prompt is updated to
instruct: *"If the context lists 'Updated' or 'Version', cite it explicitly
in your answer (e.g., 'per the 24 Mar 2026 vehicle update' or
'per Fortnightly Offering v8'). Prefer Focused=yes Active=yes vehicles when
suggesting products."*

### 5.4 · Anti-hallucination tightening (re-framing of Step 3)

The spec mentioned `confidence` / `authoritative-source` flags. **The API
doesn't expose these.** The honest proxies we have:

| Proxy | Use |
|---|---|
| `isFocused` + `isActive` (vehicle) | Boost in retrieval ranking — house-view picks beat dormant offerings. |
| `salesPitchReady` (vehicle) | Tells us a curated pitch exists — boost when answering "how do I pitch X?" questions. |
| `versionNo` (bedrock) | Higher version → more recent → small ranking boost. |
| `updatedAt` (every subsource) | Newer chunks get a small recency boost (decay function). |
| `documentsWithSummary` (stats) | Surfaces in admin tab so content team sees coverage gap. |
| `doc_type == 'growth_insurance'` ∨ `'growth_revenue'` | Treated as authoritative for "what providers / what verticals" questions. |

The Phase 9 score threshold (`RAG_MIN_SCORE = 0.45`) stays. NEW: when the
top score is `0.45 ≤ s < 0.55` we count the answer as **"low-confidence
answered"** and increment a new counter on `knowledge_sync_runs` /
`knowledge_gaps` so admins can audit borderline turns. We do not refuse
the answer — borderline cases still get answered, just flagged.

### 5.5 · Frontend citation chips

Today the chip JSON contract is:
```ts
{ doc_id, doc_title, section, score, raw_score }
```
Extend additively (backward compatible — old chips still render):
```ts
{ doc_id, doc_title, section, score, raw_score,
  doc_type?:     string,   // "academy" | "vehicle" | "sales_pitch" | …
  vehicle_name?: string,
  vehicle_type?: string,   // "AIF" | "PMS" | "MF" | "NCD" | …
  updated_at?:   string,   // ISO — render as "Updated 24 Mar 2026"
  version_no?:   number,   // render as "v8"
  language?:     string }
```

The chip tooltip becomes:
```
Alternative Investment Fund · AIF
Section: Sales Pitch Scripts › Adapting Your Pitch
Updated 7 Mar 2026 · v3
Source: academy
```

### 5.6 · Admin Knowledge Gaps tab

NEW filters at the top of the tab:
* **Doc type** (multi-select: vehicle, document, sales_pitch, academy, bedrock, growth_insurance, growth_revenue)
* **Vehicle type** (multi-select: AIF, PMS, MF, NCD, FD, Insurance) — derived from `metadata.vehicleType`
* **Effective date (updatedAt)** range — proxy for "what's missing about X since …"

---

## 6 · Things in the payload we deliberately do NOT plan to surface (yet)

| Field | Why we'd leave it |
|---|---|
| `metadata.salesPitchLanguages: ["en","hi"]` | Multi-language pitches exist but the bot is English-only end-user. We'd surface this only when we localise the chat shell. |
| `growth_insurance.tableCount / rowCount` | Counts of tables / rows inside a chunk's text — useful for the structured-data team, not for the bot's narrative answer. |
| `metadata.updatedBy` | Internal author email — likely PII-bearing. We will explicitly **strip this at projection time** (same guardrails as PAN). |
| `bedrock.isVideo` | We're not surfacing video previews in chat. Logged but not promoted. |
| Server-side filtering | The API silently ignores filter params. We continue to do client-side filtering after embedding. |

---

## 7 · Risk / open question

The biggest judgement call: **do we want a fresh full-sync ASAP** after
extending the projector, to backfill the new metadata for the 1801 chunks
already in `doc_chunks`? Today's delta-sync would treat unchanged chunk
text as a skip and miss the metadata refresh. Recommendation: ship the
projector change → flip the next scheduled sync to `mode="full"` once →
return to `delta` after. Should be a ~3-minute one-off (embedder re-uses
the same Hub AI endpoint).

---

## 8 · Files I will touch in Step 2 (preview only — no code yet)

```
backend/
  knowledge_sync.py                 — projector function, sync_runs counters,
                                       new index, optional one-off full-resync flag
  agents/rag_agent.py               — chunk preamble in LLM context,
                                       low-confidence counter,
                                       boost rules (focused/active/version/recency)
  rag.py                            — hit row carries new metadata fields
  admin.py                          — knowledge_gaps / KB tab new filters + agg
frontend/src/
  components/CitationStrip.jsx      — show updated_at, version, type
                                       (additive — falls back gracefully)
  components/admin/KnowledgeGapsTab.jsx — doc_type / vehicle_type / date filters
deliverables/phase16/
  kb_matrix.md                      — 20-row regression matrix
```

---

## 9 · Stopping here as instructed

Probe artefacts: `/app/deliverables/phase16/knowledge_api_probe/` (24 files —
one per endpoint sampled, plus per-subsource samples in
`by_subsource/`).

**Awaiting your sign-off on:**

1. **Re-framing Step 3** around the four proxies (focused/active flags,
   updatedAt recency, versionNo for bedrock, score-band low-confidence
   bucket) given the API does NOT expose `confidence` /
   `authoritative_source` / `effective_date`. OK to proceed with that?
2. **One-off full-resync** after the projector ships, so the existing
   1801 chunks pick up the new metadata. OK?
3. Anything in §6 ("deliberately not surfaced") you want me to surface
   anyway?
