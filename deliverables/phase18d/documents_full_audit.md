# Phase 18d · `documents_full` audience audit

## Executive summary (paste-ready, 5 lines)

> Sampled **35 distinct `documents_full` chunks** (≥ 25 ask, exceeded) across
> 15 AIF / PMS vehicles, all from public-branded sales-presentation PDFs.
> **4 chunks (11.4%)** — from 2 ICICI Prudential AIF presentations — carry
> the issuer's own disclaimer that the material is **"for registered
> distributors and should not be circulated to investors/prospective
> investors."** Zero chunks contain client PII, commission/fee mechanics,
> sales playbook language, or competitor put-downs.
> **Verdict: KEEP the Phase 18.2 guard indefinitely.** 11.4% is above the
> 5% threshold — the guard is doing real work, and at least one major AMC
> is explicitly directing their material away from client/visitor eyes.

---

## Audit method

* **Harvest**: `/app/deliverables/phase18d/harvest_documents_full.py` —
  read-only probe issuing 5 broad queries (`fund prospectus`, `scheme
  information document`, `investment strategy`, `portfolio construction
  methodology`, `risk factors disclosure`) against
  `POST https://deck.pesmifs.com/api/knowledge/search` with `top_k=10`.
  Harvest cap of 30 hit early at chunk 30; final harvest 35 (the 5th query
  pulled 7 extras in the same response). De-duped by `id`.
* **Persistence**: PII-redacted JSON dumped to
  `/app/deliverables/phase18d/documents_full_samples/*.json`. Redaction
  applied via 7 regex patterns covering PAN, UCC/folio/account, Indian
  phone, email, IFSC, Aadhaar, and generic 10+ digit numbers. Redaction
  triggered on 0 chunks (no PII patterns detected in any chunk — clean).
* **Classification**: pattern-sweep for distributor-only language,
  commission mechanics, sales tactics, competitor comparisons, employee
  PII, and client PII (see "Patterns checked" below).

## Coverage

| Metric | Value |
|---|---:|
| Distinct chunks harvested | 35 |
| Unique `vehicleId`s | 15 |
| Unique `fileName`s | 16 |
| `vehicleId` present | 35/35 = 100% |
| `vehicleName` present | 35/35 = 100% |
| `fileName` present | 35/35 = 100% |
| Vehicle types observed | `AIF`, `PMS` (no other categories) |

**Important**: every single `documents_full` chunk in the sample is
associated with a `vehicleId` that points to a real, vendor-branded AIF
or PMS product. None are floating, no-vehicle chunks. This raises the
relaxation bar in one direction — the corpus is at least bounded to known
products. It lowers it in the other — those products are SEBI-regulated
private placements where the issuer controls disclosure.

## Classification rollup

| Category | Count | % |
|---|---:|---:|
| `SAFE_PUBLIC` | 31 | 88.6% |
| `SAFE_INTERNAL_BUT_CLIENT_OK` | 0 | 0.0% |
| `EMPLOYEE_ONLY_COMMENTARY` | **4** | **11.4%** |
| `RESTRICTED_PII` | 0 | 0.0% |
| `AMBIGUOUS` | 0 | 0.0% |
| **EMPLOYEE_ONLY_COMMENTARY + RESTRICTED_PII** | **4** | **11.4%** |

## Patterns checked (zero matches in 35 chunks unless noted)

| Pattern | Regex | Matches |
|---|---|---:|
| Distributor-only / not for investors | `registered\s*distributors?` OR `should\s+not\s+be\s+circulated\s+to\s+investors` | **4** |
| Commission / brokerage / payout structure | `commission\|brokerage\|trail fee\|upfront fee\|distributor fee\|incentive\|payout structure` | 0 |
| Sales tactic / pitch playbook | `pitch\s+talking\s+points\|talking\s+points\|playbook\|objection\s+handling\|why\s+sell` | 0 |
| Competitor comparison | `vs\.?\s+(HDFC\|ICICI\|Kotak\|Marcellus\|ASK\|Carnelian)\|peer\s+comparison` | 0 |
| Employee PII (RM/EMP id) | `employee_id\|RM_code\|EMP-?\d` | 0 |
| Client PII (UCC/folio/account) | `folio\s*number\|account\s*holder\|UCC\s*[A-Z0-9]+` | 0 |
| PAN | `[A-Z]{5}[0-9]{4}[A-Z]` | 0 |
| Indian phone | `[6-9][0-9]{9}` | 0 |
| Email address | `[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}` | 0 |
| IFSC | `[A-Z]{4}0[A-Z0-9]{6}` | 0 |
| Confidential / private circulation (SEBI boilerplate) | `strictly confidential\|private placement\|private circulation` | 16 — but generic disclaimer, NOT employee-only |

> **Important nuance on the `confidential` matches**: 16/35 chunks contain
> "strictly confidential" or "private circulation" language. This is
> **standard SEBI-mandated boilerplate** that every AIF/PMS presentation
> carries because they're private placements rather than public mutual
> funds. It does NOT mean "employee-only" — it means "this fund is not
> publicly offered, please read the PPM." We exclude this from the
> employee-only classification.

## The 4 EMPLOYEE_ONLY_COMMENTARY chunks (per-chunk redacted snippets)

### 1. ICICI Pru Growth Leaders Fund — Series VI · ord 59
* `id`: `documents_full:1eda3b42-f4ea-4e16-8958-16fb19a8f5d3:59`
* `vehicleName`: ICICI Pru Growth Leaders Fund-Series VI
* `vehicleType`: AIF
* `fileName`: ICICI Pru Growth Leaders Fund Series VI - Presentation(Regular)_Feb 26 (1).pdf
* **Redacted snippet**:
  > `… The Scheme may invest in small & mid-cap companies at the time of
  > investment, where liquidity may be low… Information contained herein is
  > solely for private circulation for reading/understanding of registered
  > distributors and should not be circulated to investors/prospective
  > investors. …`
* **Why employee-only**: the issuer's own footer instructs that this
  material is **for registered distributors only and must not be
  circulated to investors or prospective investors**. Showing this to a
  visitor / client would violate the issuer's stated distribution policy.

### 2. ICICI Prudential Alpha Opportunities Fund · ord 2
* `id`: `documents_full:5de42226-9a9b-44f6-a311-4fc9c85dfe85:2`
* `vehicleName`: ICICI Prudential Alpha Opportunities Fund
* `vehicleType`: AIF
* `fileName`: ICICI Pru_Alpha Opportunites Fund_Deck.pdf
* **Redacted snippet**:
  > `… This document has been prepared for initial discussions only and it
  > does not amount to an offer or a solicitation to purchase units in the
  > Scheme. The document provided is for the private and confidential use
  > of the addressee … The information contained herein is solely for
  > private circulation for reading/understanding of registered
  > distributors and should not be circulated to investors/prospective
  > investors. …`
* **Why employee-only**: same disclaimer as above, repeated on a different
  slide. The issuer explicitly addresses this presentation to registered
  distributors.

### 3. ICICI Prudential Alpha Opportunities Fund · ord 46
* `id`: `documents_full:5de42226-9a9b-44f6-a311-4fc9c85dfe85:46`
* `vehicleName`: ICICI Prudential Alpha Opportunities Fund
* `vehicleType`: AIF
* `fileName`: ICICI Pru_Alpha Opportunites Fund_Deck.pdf
* **Redacted snippet**:
  > `… Portfolio Construct / Initial in-house screening process / Active
  > coverage of company / … Information contained herein is solely for
  > private circulation for reading/understanding of registered
  > distributors and should not be circulated to investors/prospective
  > investors. …`
* **Why employee-only**: same disclaimer on a portfolio-construction
  slide. Different ordinal of the same Alpha Opportunities deck.

### 4. ICICI Prudential Alpha Opportunities Fund · ord 48
* `id`: `documents_full:5de42226-9a9b-44f6-a311-4fc9c85dfe85:48`
* `vehicleName`: ICICI Prudential Alpha Opportunities Fund
* `vehicleType`: AIF
* `fileName`: ICICI Pru_Alpha Opportunites Fund_Deck.pdf
* **Redacted snippet**:
  > `… Overview of the Strategy / Strategy Features / Open-ended Scheme /
  > Seeking opportunities with long-term … Information contained herein is
  > solely for private circulation for reading/understanding of
  > registered distributors and should not be circulated to investors/
  > prospective investors. …`
* **Why employee-only**: same disclaimer on a strategy-overview slide.

> All 4 flagged chunks come from **2 ICICI Prudential AIF documents**.
> The other 13 vehicles in the sample (ASK, ABSL, Alchemy, Emkay, ICICI
> Pru Innovation, etc.) do NOT carry this distributor-only disclaimer.

## The other 31 SAFE_PUBLIC chunks — characterization

| Pattern | Count |
|---|---:|
| SEBI disclaimer / risk-factors boilerplate | 19 |
| Investment-strategy methodology (generic fund-house language) | 12 |
| Stock-screening / portfolio-construction framework slides | 5 |
| Sample / unidentified | 0 |

Representative SAFE_PUBLIC chunks (no PII / no commission language /
nothing sales-internal):
* `documents_full:7a…:50` — "Aditya Birla Global Bluechip Equity Fund (IFSC) · Risk factors disclosure" → standard regulator-mandated risk language.
* `documents_full:db…:7` — "Aditya Birla Core Equity Portfolio · Portfolio Construction Process" → public methodology slide, identical to the PMS factsheet on the AMC's website.
* `documents_full:48…:18` — "ASK Absolute Return Fund · Multi-strategy approach" → high-level strategy slide; generic enough to appear on the AMC's marketing site.

## `vehicleId` / `vehicleName` enrichment availability

* 35/35 (100%) chunks carry `metadata.vehicleId` AND `metadata.vehicleName`
  AND `metadata.vehicleType` AND `metadata.fileName`.
* The `vehicleId` is a different UUID scheme from our local
  `doc_chunks.smifs_id` — i.e. it does NOT join into `doc_chunks`, but it
  COULD theoretically be used to look up an in-deck vehicle policy.
* No `audience` field anywhere on the hit envelope or `metadata` blob.

## Verdict + recommendation

**Decision band (from `DEPLOY_NOTES.md`)**

| Category total | Recommendation |
|---|---|
| 0% | relax the guard |
| 1–5% | keep + mid confidence, ask deck team for per-chunk audience field |
| **> 5%** | **strong evidence — keep the guard indefinitely, ask deck team for server-side audience filtering** |

**This audit: 11.4%** → falls into the **"> 5% — strong evidence"** band.

### Recommendation: **KEEP the Phase 18.2 `documents_full` guard for visitor/client sessions.**

Rationale:
1. **11.4% of the audited corpus is explicitly distributor-only** by the
   issuer's own statement. Showing those chunks to a visitor/client
   would violate ICICI Prudential's stated distribution policy. The risk
   is not theoretical — it's actively documented inside the chunks.
2. **No way to gate per-chunk client-side** without parsing the chunk
   body for the disclaimer string, which is fragile (each AMC phrases it
   differently). The clean fix is server-side: deck adds an `audience`
   field per chunk based on issuer policy.
3. **Verified-employee unblock continues to work as designed.**
   Employees are regulated as registered distributors (by their ARN/MFD
   onboarding), so showing them ICICI Pru's distributor-only material is
   on-policy. The guard already permits this.
4. **No PII risk observed** — but absence of PII in 35 samples is not
   proof of absence across 2 486 chunks. Belt-and-suspenders is cheap.

### Follow-up asks for the deck team (this is item (a) the user is chasing)

If the deck team can ship any of the following, the guard can be
relaxed or made more granular:

1. **Per-chunk `audience` field on the hit envelope** — values
   `"all" | "distributors_only" | "employee_only"`. We'd respect it on
   our side without changing anything else. **(highest leverage)**
2. **Server-side honour of the existing `audience` query param** —
   either with the same value set as (1) or as a `client_facing_only`
   boolean filter. We currently send `audience` and the deck silently
   drops it (confirmed in 18c probe §8).
3. **Confirm whether ICICI Pru chunks are an outlier or a pattern** —
   audit the other AMCs in `documents_full` for similar
   "distributors-only" disclaimers and either flag them with a
   distinguishing field or remove them from the public-API tier.

Once any of (1)/(2) lands, we can simplify our client-side gate to
"drop hits whose enriched `audience != all` for non-employees", remove
the source-name special case, and the corpus opens up for visitors
beyond just the 88.6% safe-public majority.

## Appendix · raw files

* `/app/deliverables/phase18d/documents_full_samples/` — 35 PII-redacted
  chunk JSON files.
* `/app/deliverables/phase18d/harvest_index.json` — compact index with
  per-chunk title / vehicle / file / ordinal / content_head.
* `/app/deliverables/phase18d/classification.json` — machine-readable
  classification result.
* `/app/deliverables/phase18d/harvest_documents_full.py` — read-only
  harvest script (does not call any internal SMIFS APIs, only the deck).
