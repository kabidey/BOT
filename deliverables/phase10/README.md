# Phase 10 — Role Gateway + Client Q&A + Knowledge Gating + WM Fallback

**Status:** COMPLETE · 22 / 22 backend tests passing · fallback gap from iteration_12 closed.

## What changed in this iteration

1. **`backend/guardrails.py`** — widened `is_product_topic()` with a
   product-property keyword heuristic (NAV, returns, minimum, lock-in, exit
   load, tenure, expense, scheme, portfolio, etc.) so third-party fund names
   (e.g. *Alchemy Smart Alpha*) that don't match the Mackertich/SMIFS/AIF/PMS
   brand list still trigger the WM short-circuit for non-employee sessions.
2. **`backend/agents/rag_agent.py`** — added `_maybe_synthesize_wm_block()`,
   a post-generation safety net. If a verified client's reply contains the
   canonical phrase `"don't have that information in your record"` and no
   fallback blocks were produced upstream, we synthesise the
   `escalation_card` and tag `intent=ESCALATION`. Wired into both the
   streaming and non-streaming paths.

## Verification

- `cd /app/backend && python -m pytest tests/test_phase10_role_gateway.py -v` →
  **22 passed in 43.53s** (the Alchemy Smart Alpha case now passes).
- Live regression via curl against the preview URL:
  - Client self-query (risk profile) → `intent=KNOWLEDGE`, single text block.
  - Client + *Alchemy Smart Alpha* → `intent=ESCALATION`, `escalation_card` with JITEN SAHOO details, zero `smifs_knowledge` citations.
  - Client + *Mackertich ONE PMS* → same shape.
  - Visitor + *PMS minimum* → `intent=CALLBACK_REQUEST`, `form` block.
  - Verified employee + *Mackertich ONE PMS minimum* → `intent=KNOWLEDGE`, 3 `smifs_knowledge` citations + real ₹50 lakh answer.

## Files in this bundle

| File | Purpose |
|------|---------|
| `role_gate_root.jpg` | Screenshot of `/` showing the 3-button role gate |
| `role_gate_embed.jpg` | Screenshot of `/embed` showing the same gate |
| `verified_client_chat.jpg` | Verified-client chat — resume offer, client_card, masked PAN `XXXXX3602X` |
| `transcript_client.txt` | Full role-pick → UCC → PAN → 5 self-queries → 2 product fallbacks → session snapshot |
| `transcript_visitor.txt` | Visitor role-pick → PMS product Q → generic Q — both produce callback form |
| `transcript_employee.txt` | Employee role-pick → email → PAN → PMS Q with `smifs_knowledge` citations |
| `sample_client_profile_injection.txt` | Raw identity object + compact CLIENT_PROFILE block injected into the system prompt |
| `generate_transcripts.sh` | Reproducible script that generated the transcripts |

## Knowledge-gating confirmation

| Role | Product question | `smifs_knowledge` citations |
|------|------------------|-----------------------------|
| Visitor | PMS minimum | **0** (form block instead) |
| Verified Client | Mackertich ONE PMS / Alchemy Smart Alpha | **0** (escalation_card) |
| Verified Employee | Mackertich ONE PMS minimum | **3** (real answer) |

## Capability / field coverage

- `/app/backend/CLIENT_FIELD_MAP.md` — 60-field CLIENT_PROFILE inventory (active since Phase 10).
- `/app/backend/EMPLOYEE_FIELD_MAP.md` — full employee USER_PROFILE field map (Phase 8.1).
- `/app/backend/SMIFS_KNOWLEDGE_CAPABILITIES.md` — KB corpus probe.

## Credentials used

- Client: UCC `63876`, PAN `ARIPP3602Q` (A BALARAM PATRO, RM = JITEN SAHOO)
- Employee: `aaditya.jaiswal@smifs.com`, PAN `BQPPJ8323M`
- Admin: `X-Admin-Token: smifs-admin-2026`
