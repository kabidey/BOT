# SMIFS Knowledge API — Capabilities Probe

**Base URL**: `https://deck.pesmifs.com`
**Auth**: `X-API-Key: <key>` (401 without; 401 with bad key)
**Probe date**: 2026-04-29

---

## 1. Endpoint surface

| Path | Verified? | Notes |
|---|---|---|
| `GET /api/knowledge` | ✅ | Primary list endpoint. Returns all chunks pre-chunked by source. |
| `GET /api/knowledge/stats` | ✅ | Richer counts than the inline `stats` in the list response. |
| `GET /api/knowledge/{id}` | ❌ 404 | **No single-item endpoint.** Chunks available only via list. |
| `GET /api/knowledge/search?q=…` | ❌ 404 | No server-side full-text search. |
| `GET /api/knowledge/categories` | ❌ 404 | N/A. |
| `GET /api/knowledge/tags` | ❌ 404 | N/A. |
| `GET /api/openapi.json` | ❌ 404 | No OpenAPI descriptor. |
| `GET /openapi.json` | ⚠️ 200 (HTML) | Returns the SPA landing page, not JSON schema. |
| `GET /docs` | ⚠️ 200 (HTML) | Same SPA. Not Swagger. |

## 2. Pagination — the TLDR

`GET /api/knowledge?limit=N&offset=K` is the only working form. The API accepts a `limit` up to at least 2000 (returns all 1801 chunks in one call, ~1 MB). Other params are **silently ignored**:

| Param | Behaviour |
|---|---|
| `limit` | ✅ respected |
| `offset` | ✅ respected |
| `skip` | ❌ ignored (returns offset=0) |
| `page` | ❌ ignored |
| `cursor` | ❌ ignored |

## 3. Response shape

```json
GET /api/knowledge?limit=5

{
  "stats": {
    "vehicles": 162,
    "documents": 213,
    "academy": 1278,
    "bedrock": 148,
    "totalChunks": 1801
  },
  "chunks": [
    {
      "id": "vehicle:<uuid>:<ordinal>",
      "source": "vehicle" | "document" | "academy" | "bedrock",
      "sourceId": "<uuid>",
      "title": "Alchemy Smart Alpha 250",
      "section": "PMS",
      "content": "Investment Product: Alchemy Smart Alpha 250\nCategory: PMS",
      "metadata": { ...source-specific keys... }
    }
  ]
}
```

### 3a. `/api/knowledge/stats`
```json
{
  "vehicles": 162,
  "vehiclesActive": 161,
  "documents": 220,
  "documentsWithSummary": 213,
  "academyCourses": 11,
  "academyChapters": 40,
  "academyPages": 473,
  "bedrockItems": 2,
  "bedrockExtractable": 2,
  "bedrockCached": 2
}
```

## 4. Four distinct source types (1801 chunks total)

| Source | Count | What it is | Key metadata keys |
|---|---|---|---|
| `vehicle` | 162 | SMIFS investment vehicles (PMS / AIF / MF master records) | `vehicleType`, `isFocused`, `isActive`, `documentCount`, `updatedAt` |
| `document` | 213 | PDF factsheets / presentations attached to vehicles (summarised) | `vehicleId`, `vehicleName`, `fileType`, `fileName`, `ordinal` |
| `academy` | 1278 | Training content — product masterclasses, broken by course→chapter→page | `courseId`, `courseTitle`, `chapterId`, `chapterTitle`, `pageTitle`, `pageIndex`, `ordinal` |
| `bedrock` | 148 | Corporate presentations / foundational SMIFS assets | `section`, `fileType`, `fileName`, `isVideo`, `kind` |

### 4a. `updatedAt` — ONLY on `vehicle` source

This is the ONLY chunk type carrying a per-chunk `updatedAt` ISO timestamp. `document`, `academy`, `bedrock` chunks do NOT carry any change timestamp. **Implication**: delta-sync by timestamp is impossible for 91% of the corpus. We fall back to:

- Content hash comparison (if `id` exists + content hash matches → skip re-embed)
- Full sync on admin demand; startup runs a "reconcile" that re-embeds only chunks whose SHA-1(content) changed.

### 4b. Content length

Pre-chunked by the API. Observed content sizes: `vehicle` ~50-200 chars, `document` ~500-1500 chars, `academy` ~300-2000 chars, `bedrock` ~50-100 chars (mostly metadata headers, not deep content). We do **not** re-chunk — we embed each chunk as-is. This preserves the source's own semantic boundaries (course→chapter→page).

## 5. Filtering

| Filter param | Honoured? |
|---|---|
| `source=vehicle` / `source=document` / `source=academy` / `source=bedrock` | ✅ server-side filter works |
| `type`, `vehicleType`, `category` | ❌ ignored |
| `q`, `search` | ❌ ignored |
| `since`, `updatedAfter` | ❌ ignored |

For all non-source filtering we do it client-side post-fetch.

## 6. Error shapes

| Status | Body | Trigger |
|---|---|---|
| 401 | `{"detail":"Not authenticated"}` | Missing `X-API-Key` |
| 401 | `{"detail":"Invalid or revoked API key"}` | Wrong key value |
| 404 | `{"detail":"Not Found"}` | Unknown subpath |

### Rate limits
No documented rate limit; probed at ~10 req/sec without 429. Our sync rate-limits client-side at **10 req/sec** with exponential backoff (250ms → 1s → 3s with jitter) on 429/5xx, max 3 retries.

## 7. Normalised internal shape (after ingest)

Every API chunk is stored into our `doc_chunks` collection as:

```python
{
  "_id": "smifs_kb_<api_chunk_id>",
  "source": "smifs_knowledge",
  "subsource": "vehicle" | "document" | "academy" | "bedrock",
  "doc_id": "smifs_kb_<sourceId>",           # the SMIFS logical doc
  "doc_title": <title>,
  "section": <section or chapterTitle/pageTitle>,
  "text": <content>,
  "embedding": [...],                         # 1536-dim from Hub AI text-embedding-3-small
  "smifs_metadata": <full metadata object>,   # preserved verbatim
  "smifs_id": <api id>,
  "content_hash": "<sha1(content)[:16]>",     # idempotence key
  "updated_at": <ISO>,                        # our ingest timestamp
  "source_updated_at": <metadata.updatedAt if vehicle, else null>
}
```

## 8. Sync idempotency algorithm

```text
for every chunk in GET /api/knowledge?limit=2000:
  key   = chunk.id
  hash  = sha1(chunk.content).hexdigest()[:16]
  prior = doc_chunks.find_one({smifs_id: key})
  if prior and prior.content_hash == hash:
    skipped += 1
    continue
  embed chunk.content → vec
  upsert {smifs_id: key} with new content + vec + hash
  upserted += 1

# Drop stale: any doc_chunks.source=smifs_knowledge whose smifs_id is NOT in
# the fetched id set anymore → delete (supports upstream removals).
```

Startup behaviour:
- If `doc_chunks.source=smifs_knowledge` is empty → run a **full** sync in background.
- If non-empty → skip (admin can trigger `POST /api/admin/knowledge/sync`).

## 9. Quirks & surprises

1. **`stats.totalChunks` (1801) ≠ `documents + vehicles + academy + bedrock` from `/stats`** — the root `/stats` endpoint counts documents before chunking summaries (220 vs 213). The inline `stats` in the list response is authoritative for chunk counts.
2. `skip` and `page` query params silently return the same first page — do NOT assume they paginate.
3. Only `vehicle` chunks carry `updatedAt` — no universal change-tracking.
4. **No `/api/knowledge/{id}` endpoint** — we can't fetch a single chunk after ingest. Not a blocker since we store the full content ourselves.
5. Corpus is **already chunked** by the API: academy is one chunk per page, vehicles are one tiny metadata chunk, documents are one summary chunk. We do NOT re-chunk.
