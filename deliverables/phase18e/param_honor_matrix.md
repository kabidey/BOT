# Phase 18e · Param-honour matrix

Method: send each param + control (same query without it). If the result
ID set differs from control → honoured. If identical → silently dropped.

**Control query**: `{"q": "AIF", "top_k": 5}` →
returns 5 hits, sources `[growth_revenue, documents_full × 3, bedrock]`.

| Param | Sample value | n_results | Result-ID set vs control | Honoured? |
|---|---|---:|:---:|:---:|
| `q` | `"AIF"` | 5 | (control) | ✅ |
| `top_k` | 5 | 5 | (control) | ✅ |
| `min_score` | 0.1 | 5 | identical | ✅ honoured (no junk above 0.1 to remove) |
| `minScore` (camelCase) | 0.1 | 5 | identical | ❌ dropped |
| `sources` | `["bedrock"]` | 5 | **different** | ✅ honoured — narrows to bedrock-only |
| `sources` | `["academy"]` | 0 | **different** | ✅ honoured (empty because deck has no academy) |
| `sources` | `["document"]` | 0 | **different** | ✅ honoured (empty because deck has no document) |
| `source` (singular) | `"bedrock"` | 5 | identical | ❌ dropped |
| `subsource` | `"bedrock"` | 5 | identical | ❌ dropped |
| `exclude_sources` | `["sales_pitch"]` | 5 | identical | ❌ dropped |
| **`audience`** | `"all"` | 5 | identical | ❌ **still dropped** |
| **`audience`** | `"employee_only"` | 5 | identical | ❌ **still dropped** (same set as `"all"` → no audience-aware filter) |
| `language` | `"en"` | 5 | identical | ❌ dropped |
| `vehicle_id` | `"cc602b11-…"` | 5 | identical | ❌ dropped |
| `is_focused` | `true` | 5 | identical | ❌ dropped |
| `is_active` | `true` | 5 | identical | ❌ dropped |
| `filters` (nested) | `{"source":"bedrock"}` | 5 | identical | ❌ dropped |

## Honoured params (4 total — unchanged from 18c)

```
q, top_k, min_score, sources (positive whitelist)
```

## Pending deck-team asks (still not shipped)

The Phase 18d follow-up asked for:

1. **Per-chunk `audience` field on the hit envelope** — top priority.
2. **Server-side honour of the existing `audience` query param.**
3. **`exclude_sources` as a real filter** so we can negative-list
   `documents_full` for non-employees without re-sending the full
   positive whitelist.

**None of (1)/(2)/(3) have landed.** Confirmed by direct probe.

## Implication

* The Phase 18.1 belt-and-suspenders source-name audience gate AND the
  Phase 18.2 `documents_full` guard remain the only line of defence on
  audience — both run client-side.
* No regression in honoured params — `sources` whitelist still works,
  which is the single mechanism that makes the visitor pre-filter
  actually save bandwidth.
* When the `audience` field eventually lands, the swap-out is small
  (~10 LOC): replace `_belt_and_suspenders_audience_drop()`'s
  source-name check with a direct `enriched.audience != "all"` check.
  Both belt-and-suspenders layers can be removed in favour of a single
  audience-field check.
