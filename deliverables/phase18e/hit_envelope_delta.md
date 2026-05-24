# Phase 18e · Hit envelope delta vs 18c

Compared the 8-key hit shape from 18c against fresh 18e samples across all
6 observed sources. **Verdict: zero changes.**

## Top-level keys (still 8)

| Field | 18c | 18e | Notes |
|---|---|---|---|
| `id` | string | string | same scheme (`<source>:<uuid>:<ord>`) |
| `score` | float (cosine 0–1) | float | same |
| `source` | string | string | same 6 values |
| `sourceId` | string (UUID) | string | same |
| `title` | string | string | same |
| `section` | string | string | same |
| `content` | string | string | same |
| `metadata` | object | object | per-source shape unchanged |

## Asked-for new fields (the brief's "BIG ONE")

| Field | 18c | 18e | Status |
|---|---|---|---|
| **`audience`** (top-level) | ❌ missing | **❌ still missing** | not shipped by deck team yet |
| **`language`** (top-level) | ❌ missing | **❌ still missing** | not shipped |
| **`effective_date`** | ❌ missing | **❌ still missing** | not shipped |
| **`updated_at`** (top-level) | ❌ (only inside metadata for some sources) | unchanged | not shipped |
| **`versionNo`** | ❌ missing | **❌ still missing** | not shipped |
| **`metadata.audience`** | ❌ missing | **❌ still missing** | not shipped |
| **`metadata.is_focused`** | partial (only `vehicle`, `sales_pitch`) | unchanged | not new |
| **`metadata.vehicle_id`** | partial — camelCase `vehicleId` on `vehicle`, `sales_pitch`, `documents_full` | unchanged | not new |

## Per-source `metadata` shape (unchanged from 18c)

```jsonc
// bedrock
{ "section", "fileType", "fileName", "ordinal", "kind" }

// vehicle
{ "vehicleType", "customTypeName", "isFocused", "isActive",
  "documentCount", "salesPitchReady", "salesPitchLanguages", "updatedAt" }

// sales_pitch
{ "vehicleId", "vehicleName", "vehicleType",
  "language", "languageLabel", "languageNative",
  "isFocused", "generatedAt", "model", "ordinal" }

// growth_revenue
{ "kind", "generatedAt", "fyTag", "generatedBy" }

// growth_insurance
{ "kind", "providerName", "generatedAt", "generatedBy" }

// documents_full
{ "vehicleId", "vehicleName", "vehicleType",
  "fileType", "fileName", "fileSize", "ordinal", "kind" }
```

## Conclusion

**The hit envelope is byte-identical between 18c and 18e.** None of the
fields requested in the Phase 18d follow-up ask to the deck team have
landed yet. Schedule the next probe based on the trigger conditions in
`deck_reprobe_delta.md` §11, not on a fixed cadence.
