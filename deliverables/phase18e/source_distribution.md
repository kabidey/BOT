# Phase 18e · Source distribution (20 broad queries × top_k=25)

| source | 18c hits | 18e hits | Δ | join into local `doc_chunks.smifs_id` |
|---|---:|---:|---:|---:|
| `documents_full` | 361 | **357** | −4 | 0% (un-joinable, deck-only) |
| `bedrock` | 99 | **103** | +4 | 100% |
| `vehicle` | 29 | **29** | 0 | 100% |
| `growth_insurance` | 6 | **6** | 0 | 100% |
| `growth_revenue` | 4 | **4** | 0 | 100% |
| `sales_pitch` | 1 | **1** | 0 | 100% |
| **`academy`** | **0** | **0** | unchanged | n/a — **still not indexed by deck** |
| **`document`** | **0** | **0** | unchanged | n/a — **still not indexed by deck** |
| _any new source_ | — | none observed | — | — |

## Diff vs 18c

* Total observed hits 500/500 (top_k=25 × 20 queries). Distribution is
  effectively unchanged — `documents_full` -4 / `bedrock` +4 is sampling
  noise.
* **No new source names** in the 18e sample. The 6-source space
  (`bedrock` / `vehicle` / `growth_insurance` / `growth_revenue` /
  `sales_pitch` / `documents_full`) is stable.
* **`academy` (1278 local chunks) and `document` (212 local chunks)
  remain local-only.** This is the dominant coverage gap and the reason
  the 18c parity regression hasn't recovered.

## Implication

* For the augment path: deck contributes incremental hits on brand-
  / vehicle-specific queries (where `bedrock`/`vehicle`/`sales_pitch`
  win) but not on educational queries (where `academy` would win
  locally and the deck has nothing).
* For the visitor source whitelist
  (`["bedrock","vehicle","academy","sales_pitch","document"]`):
  `academy` and `document` are no-ops at the deck side (always return
  0 results), but the whitelist costs us nothing — we keep them for
  forward-compatibility when/if the deck eventually ingests them.
* The Phase 18.2 `documents_full` block for visitor/client is still
  blocking a real 71% of total deck hits — but those hits never reach
  visitor/client anyway because the whitelist pre-filter (which omits
  `documents_full`) does the work server-side. The local block is
  defense-in-depth.
