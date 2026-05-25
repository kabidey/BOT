# Phase 22 — Device-Fingerprint Fraud Detection

## Threat model

Bad actor (or compromised employee credential) sits at a single device and
sequentially submits PAN+UCC pairs to harvest customer profile data. Each
attempt looks like a routine verification turn; the abuse pattern only
emerges when you correlate **identity-binding count per device** over time.

## Signals we track

For every authenticated `/api/*` request the middleware appends the three
client-supplied headers to a per-fingerprint document in
`device_fingerprints`:

| Header                  | Purpose                                       |
| ----------------------- | --------------------------------------------- |
| `X-Client-Fingerprint`  | Stable visitorId from `@fingerprintjs/fingerprintjs` |
| `X-Client-Tz`           | Resolved IANA timezone (`Asia/Kolkata`)       |
| `X-Client-Screen`       | `WIDTHxHEIGHT@DPR`                            |

Every successful PAN→identity match calls `record_identity_binding`, which
pushes a `{ucc|employee_id, first_at, last_at, verification_count, rm_name?}`
entry into the `client_identities` / `employee_identities` array on the row.

## Scoring axes (exponentially time-decayed, half-life = 7 days)

* **Rapid burst** — distinct client UCCs first-seen within `FPRINT_RAPID_WINDOW_MIN`
  minutes (default 120). +25 per UCC after the first.
* **24h saturation** — distinct UCCs first-seen in the last 24 h. +15 per UCC
  after the first 2.
* **Lifetime-no-RM saturation** — decayed count of all client bindings beyond
  `FPRINT_LIFETIME_CLIENT_LIMIT_NO_RM` (default 10). +10 per excess decayed unit.
* **IP geographic jump** — ≥ 2 distinct /16 prefixes seen within 10 min. +50.
* **UA rotation** — ≥ 3 distinct user agents within 24 h. +10.

### Mitigators

* **RM linkage** — if ≥ 50 % of bound clients name an RM whose own employee
  record also signed in from this device, subtract 20. Reflects the
  "branch laptop, RM-assisted onboarding" archetype.
* **Single network** — if every IP seen so far is on the same /16, subtract 10.

`suspicious_score = clamp(0, 100, sum(axes) − sum(mitigators))`. Crossing
`FPRINT_BLOCK_SCORE` (default 75) trips an auto-block; crossing
`FPRINT_FLAG_SCORE` (default 40) emits a `fingerprint_fraud_flag` event but
does NOT block.

## Silent block behaviour

Blocked fingerprints continue receiving **HTTP 200** envelopes that mimic a
benign soft failure:

* `/api/chat`, `/api/agent/turn`, `/api/agent/turn/stream` →
  `{ "blocks": [{ "type": "text", "text": "We're currently unable to process your request…" }], "intent": "SOFT_ERROR" }`
* `/api/sessions/{sid}/*` → same chat envelope (FE renders `.blocks`)
* `/api/rag/search` → empty `hits` list
* `/api/leads` → fake "Thanks, we'll be in touch" lead-pending stub (NOT
  persisted)
* `/api/handoff` → empty pending envelope

There is **no** `403`, **no** `blocked: true`, and **no** UI banner. The
attacker sees a deterministic but plausible failure and burns out without
knowing they've been detected. Every silent-block hit emits a
`fingerprint_silent_block_served` security event so the admin console can
render the volume of attacks served per day.

## Admin Fraud Watch

`/api/admin/fingerprint/*` endpoints power the **Fraud Watch** tab:

| Endpoint                                            | Action                            |
| --------------------------------------------------- | --------------------------------- |
| `GET  /api/admin/fingerprint/summary`                | Counters + threshold snapshot     |
| `GET  /api/admin/fingerprint/list?status=…`          | Top suspicious fingerprints      |
| `GET  /api/admin/fingerprint/{hash}`                  | Full forensic row + audit trail   |
| `POST /api/admin/fingerprint/{hash}/block`            | Manual block                      |
| `POST /api/admin/fingerprint/{hash}/unblock`          | Lift block                        |
| `POST /api/admin/fingerprint/{hash}/trust`            | Mark trusted (immune to scoring)  |
| `POST /api/admin/fingerprint/{hash}/note`             | Append a free-form forensic note  |

Trusted fingerprints are **never** reported by `is_blocked` even if a stale
`blocked: true` row lingers — this is defence-in-depth against operator error
during incident response.

## Environment tuning

| Env var                              | Default | Knob                           |
| ------------------------------------ | ------- | ------------------------------ |
| `FPRINT_BLOCK_SCORE`                  | 75      | Auto-block threshold           |
| `FPRINT_FLAG_SCORE`                   | 40      | Flag-only threshold            |
| `FPRINT_RAPID_WINDOW_MIN`             | 120     | Rapid-burst window (minutes)   |
| `FPRINT_RAPID_CLIENT_LIMIT`           | 3       | Rapid-burst tolerance (legacy) |
| `FPRINT_DAILY_CLIENT_LIMIT`           | 5       | 24h saturation tolerance       |
| `FPRINT_LIFETIME_CLIENT_LIMIT_NO_RM`  | 10      | Lifetime-no-RM tolerance       |

All thresholds read live `os.environ` (no module-level cache) so an ops team
can tune without a restart.

## Privacy

* `fingerprint_hash` is the unhashed FingerprintJS visitorId. It is **never**
  associated with a plaintext PAN, email, or phone. The `client_identities`
  array stores the UCC verbatim (UCC is not PII on its own) and the audit
  trail logs only the *masked* identity key (`12***90`).
* The IP and UA arrays cap at 240 chars each and are subject to the same
  90-day TTL as the rest of the security telemetry.
* `device_fingerprint_audit` has a 180-day TTL.

## False-positive recovery

The most common false positive is a branch laptop where an RM legitimately
onboards multiple clients in a single afternoon. Two safety nets handle this:

1. **Automatic** — the RM-linkage mitigator subtracts 20 from the score
   if the device's employee record matches the RM name of the bound clients.
2. **Manual** — an admin clicks **Mark trusted** in the Fraud Watch tab;
   the fingerprint is excluded from future scoring AND any in-flight block
   is lifted in the same transaction.

## Acceptance tests

See `tests/test_fingerprint_guard.py` for the live regression suite that
covers:

* Single-client binding stays at score 0.
* Rapid 3-client burst within 5 min trips the block.
* `is_blocked` returns the chat-shape silent envelope (no `403`).
* Employee + 4 own clients with matching RM names stays under the threshold
  (RM-linkage mitigator).
* Admin unblock + trust resets the score path.
* Time decay: client bindings older than 14 days contribute < 25 % of their
  original weight.
