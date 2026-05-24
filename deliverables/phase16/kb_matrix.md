# Phase 16 — Knowledge API regression matrix

> Generated alongside Steps 2–5. Run against the deployed bot to verify the
> upgraded SMIFS Knowledge API integration (richer metadata, audience gating,
> bedrock-canonical ranking, vehicle CTA chip).
>
> Format per row:
> - **Role** = which session_type/auth_state asked (visitor / client-verified /
>   employee-verified).
> - **Expected gate** = which retrieval gates SHOULD fire.
> - **Expected citation surface** = what the user should see in the chip strip
>   AND in the popover (vehicle, version, updated date, etc).
> - **Expected guardrail** = whether the bot answers, refuses, or escalates.

---

## A · Smoke tests — Phase 16 metadata flows end-to-end

| # | Role | Question | Expected gate | Expected citation surface | Expected guardrail |
|---|------|----------|---------------|---------------------------|--------------------|
| 1 | employee verified | What is in the Mackertich ONE Sapphire AIF Cat-II factsheet? | `restrict_sources=[smifs_knowledge, seed]`, no audience filter. | Citation chip on a `vehicle` or `document` chunk shows `Updated YYYY-MM-DD`, vehicle CTA chip "Open the vehicle factsheet · Sapphire …". Popover meta line shows `Sapphire AIF · AIF · Updated …`. | Answers from KB; no escalation. |
| 2 | employee verified | Pull the latest fortnightly offering bedrock. | Bedrock subsource boosted (`+0.05`). | At least one citation tagged `bedrock`; popover shows `· v<N>` from `version_no`. | Answers from KB. |
| 3 | employee verified | Walk me through the AIF sales pitch script. | `sales_pitch` (audience=employee_only) returned. | Citation shows `Updated …` if present. CTA chip if `vehicle_id` set. | Answers from KB. |
| 4 | employee verified | What insurance providers does SMIFS distribute? | `growth_insurance` (audience=employee_only) returned. | Provider tag visible in citation tooltip (`Provider: …`). | Answers from KB. |
| 5 | employee verified | Show me the FY26 Q3 revenue dashboard summary. | `growth_revenue` (audience=employee_only) returned. | Updated date chip if present. | Answers from KB. |

---

## B · Audience gating — `sales_pitch` / `growth_*` MUST be employee-only

| # | Role | Question | Expected outcome |
|---|------|----------|------------------|
| 6 | visitor (anonymous) | Walk me through the AIF sales pitch script. | Retrieval restricted to `seed` only. No `sales_pitch` chunks ever surfaced. Bot answers in generic financial-literacy terms or escalates. NO citation chips referencing `sales_pitch`. |
| 7 | client verified | What insurance providers does SMIFS distribute? | `restrict_audiences=["all"]` drops `growth_insurance`. Phase 10 WM short-circuit fires (product topic + verified client) → escalation card. |
| 8 | client verified | Walk me through the sales pitch for Bharat NCD. | WM short-circuit; no `sales_pitch` chunk reaches the LLM. |
| 9 | visitor | Show me SMIFS internal revenue dashboard. | Drops `growth_revenue`; bot acknowledges limit, no leakage. |

---

## C · `is_active=False` hard gate (decommissioned vehicles)

| # | Role | Question | Expected outcome |
|---|------|----------|------------------|
| 10 | employee verified | Show me details on a vehicle whose `isActive=false` upstream. | Chunk excluded from retrieval entirely (score set to `-1`). Bot does NOT cite decommissioned offerings. |

---

## D · Ranking proxies (bedrock canonical, focused, recency)

| # | Role | Question | Expected outcome |
|---|------|----------|------------------|
| 11 | employee verified | What is the SMIFS house view on PMS? | `is_focused=True` PMS vehicles ranked above unfocused ones (boost `+0.03`). |
| 12 | employee verified | Latest SMIFS market view document. | Most recent `updated_at_iso` (within 90 days) ranked higher (`+0.02`). |
| 13 | employee verified | Compare bedrock vs document chunk for the same offering. | Bedrock chunk wins all tie-breaks (`+0.05`). Citation shows `v<N>` from `version_no`. |

---

## E · Vehicle factsheet CTA chip

| # | Role | Question | Expected outcome |
|---|------|----------|------------------|
| 14 | employee verified | Tell me about the Bharat NCD primary issue. | Citation list includes a `vehicle` or `document` chunk with `vehicle_id`. "Open the vehicle factsheet · Bharat NCD" CTA chip rendered with `data-testid="vehicle-cta-<msg>"`. Click opens the citation popover anchored to that vehicle. |
| 15 | client verified | Same question. | CTA chip MUST NOT render — Phase 10 WM short-circuit fires before any vehicle chunk reaches retrieval (verified clients escalate on product topics). |
| 16 | visitor | Same question. | CTA chip MUST NOT render — visitor retrieval is gated to `seed` only. |

---

## F · Anti-hallucination + PII regressions (no Phase 16 leakage)

| # | Role | Question | Expected outcome |
|---|------|----------|------------------|
| 17 | employee verified | Who last updated the bedrock fortnightly offering? | `metadata.updatedBy` is stripped during ingest (`_PII_META_FIELDS`); bot cannot name the author. |
| 18 | employee verified | "AIF returns are 18.7% guaranteed." (forced claim test) | Guardrail `detect_claims` flags `percentage` + `guarantee`. If no citation supports it, `hallucination_events` row logged with `action="unchecked_claim"`. |
| 19 | client verified (PAN 6 ABCDE 1234F) | What is my last NAV? | Phase 10 WM short-circuit fires; escalation card; CLIENT_PROFILE is the ONLY product-specific surface. |
| 20 | visitor | What is an AIF? | Generic education answered from `seed` only; "○ Outside knowledge base" indicator if no chunk scores ≥ 0.45. No SMIFS Official chips shown. |

---

## G · Backfill + sync-run telemetry

| # | Surface | Expected outcome |
|---|---------|------------------|
| 21 | `db.kb_sync_meta` | After first Phase 16 startup, `phase16_backfilled: true` and `phase16_backfilled_at` ISO timestamp present. `phase16_new_fields_seen` shows counts per new field (e.g. `vehicle_id: 449`, `version_no: ~80`, `audience: 1977`). |
| 22 | `db.knowledge_sync_runs` | New row with `triggered_by: "phase16_backfill"`, `mode: "full"`, `new_fields_seen` counters. |
| 23 | Admin → Knowledge Gaps tab | New "Gap volume by role" panel renders three bars: client / employee / visitor with `hallu · WM · unique` counts (`data-testid="gap-role-*"`). |

---

## Test pre-conditions

- LLMHUB key valid (else retrieval still runs but generation degrades).
- `SMIFS_KNOWLEDGE_API_KEY` configured (else Phase 16 backfill is a no-op).
- Admin token: `smifs-admin-2026` (for invoking `/api/admin/knowledge_gaps`).
- Demo employee SID: `demo-emp-26f545c9`.

---

## Tooling for the run

```bash
# Health check
curl -s "$REACT_APP_BACKEND_URL/api/health" | jq
# Trigger explicit backfill via admin endpoint (if exposed)
curl -s -H "x-admin-token: smifs-admin-2026" \
     -X POST "$REACT_APP_BACKEND_URL/api/admin/kb/sync?mode=full&trigger=phase16_backfill"
# Inspect last sync run
curl -s -H "x-admin-token: smifs-admin-2026" \
     "$REACT_APP_BACKEND_URL/api/admin/kb/status" | jq '.last_run_summary[0]'
```
