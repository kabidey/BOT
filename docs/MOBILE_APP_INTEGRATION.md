# Mackertich ONE — Mobile App Integration Spec

> **Audience**: iOS + Android engineers building a native Mackertich ONE
> app that consumes the existing FastAPI backend at `https://bot.pesmifs.com`.
>
> **Source of truth**: this document tracks the live backend contract as of
> **Phase 29 (May 2026)**. Any backend change MUST update this file in the
> same PR. The web frontend at `/app/frontend/` is the visual reference
> implementation; mobile is a peer client, not a port.
>
> **Read order**: §1 product overview → §2 base URLs → §3 auth/session →
> §4 chat API (the core) → §5 block reference (the bulk) → everything else.
>
> **Not in scope**: native code. This is a SPEC. Native repos live separately.

---

## Table of Contents

1.  [Product Overview](#1-product-overview)
2.  [Base URLs + Environments](#2-base-urls--environments)
3.  [Authentication & Session](#3-authentication--session)
4.  [Chat API — the core endpoint](#4-chat-api--the-core-endpoint)
5.  [Block Type Reference](#5-block-type-reference)
6.  [Dynamic Forms — the 5 form types](#6-dynamic-forms--the-5-form-types)
7.  [Persona-Aware Behavior](#7-persona-aware-behavior)
8.  [Localization](#8-localization)
9.  [Theme & Visual Language](#9-theme--visual-language)
10. [UX Patterns the Mobile App Should Mirror](#10-ux-patterns-the-mobile-app-should-mirror)
11. [Admin / Diagnostic Endpoints](#11-admin--diagnostic-endpoints)
12. [Error Handling](#12-error-handling)
13. [External Integrations](#13-external-integrations)
14. [Push Notifications (TBD)](#14-push-notifications-tbd)
15. [Build Targets](#15-build-targets)
16. [Test Plan Skeleton](#16-test-plan-skeleton)
17. [Repository / Pipeline](#17-repository--pipeline)
18. [Open Questions for the Backend Team](#18-open-questions-for-the-backend-team)

---

## 1. Product Overview

**Mackertich ONE** is the wealth-management vertical of **SMIFS Ltd** (Stewart
& Mackertich Wealth Management). The app is a "concierge with a team":
the user talks to a single chat surface; under the hood a multi-agent
orchestrator routes between LLM synthesis (Hub AI), retrieval-augmented
generation (SMIFS knowledge-base), structured tool calls (OrgLens for
identity & holdings, BMIA for compliance & fundamentals), and human
hand-off to a relationship manager.

### Three personas

| Persona      | Authenticated? | What they can do |
| ------------ | -------------- | ---------------- |
| **Visitor**  | No             | Generic wealth-management Q&A, eligibility queries, pricing comparisons, lead-capture forms (callback, demand). |
| **Client**   | Yes (PAN + UCC) | Personal portfolio queries, NCD/SIP holdings, RM hand-off, complaints, feedback, request a callback. |
| **Employee** | Yes (PAN + work email `@smifs.com`) | OrgLens directory lookups, client portfolio research, reporting-chain navigation, internal SEBI/compliance briefings, sales talking-point generation. |

### Design tenets

* **Concierge tone, not bot tone** — premium private-banker register. No
  emojis in copy, no bot-chatty filler. See §9 for visual language.
* **Anti-bluff rail** — when the orchestrator's confidence is below a
  threshold OR retrieval returns no relevant chunks, the bot says *"I don't
  have a confident answer; let me route you to a senior advisor"* and emits
  a `low_confidence_escalation` block. **Never fabricate**.
* **Citation-grounded** — every retrieval-backed claim has a citation chip
  in the payload. Citations are CURRENTLY HIDDEN from the user via env flag
  (`CHAT_SHOW_CITATIONS_TO_USER=false`); they still arrive in the payload
  for admin audit. Mobile must therefore RECEIVE citations but NOT RENDER
  them unless that env flag is flipped to `true`.
* **Suggestive flow** — after every substantive answer, 3 contextual
  follow-up chips render (see §5 → `suggested_actions`). Skip rules apply.

---

## 2. Base URLs + Environments

| Env        | Base URL                                                  | Use for                                |
| ---------- | --------------------------------------------------------- | -------------------------------------- |
| **Production** | `https://bot.pesmifs.com`                              | Real users, App Store builds.          |
| **Staging / Preview** | `https://wealth-chat-4.preview.emergentagent.com` | Pre-release smoke tests, dev builds. Same backend, fresher code. |
| OpenAPI docs | `<base>/api/docs`                                       | Swagger UI of every endpoint listed below. |

### Test credentials (preview + production — same DB)

* **Employee login**: email `aaditya.jaiswal@smifs.com` · PAN `BQPPJ8323M`
* **Client login**: UCC `63876` · PAN `ARIPP3602Q`  *(or)*  UCC `M700778`
* **Admin token** (dev/test only — do NOT embed in mobile binaries):
  `Authorization: Bearer smifs-admin-2026`

### Hostname posture

* TLS: Let's Encrypt ECDSA P-256, valid through `notAfter` of the cert
  (renews automatically via certbot.timer on the VPS).
* HTTP/2 enabled on production. TLS 1.2 + 1.3 only.
* DNS: `bot.pesmifs.com` A → `187.127.174.187` (no Cloudflare proxy).

---

## 3. Authentication & Session

The backend uses **session-based** auth (not bearer tokens). Mobile must
generate a session id, persist it, and send it with every chat turn.

### Session lifecycle

```
   [cold start]
         │
         ▼
   anonymous  (no role chosen yet)
         │
         ▼   POST /api/sessions/{id}/select_role  body: {"role": "client" | "employee" | "visitor"}
         │
   ┌─────┴──────────────┬────────────────────────┐
   ▼                    ▼                        ▼
 visitor             client                  employee
 (no further auth)   PAN + UCC verify        PAN + work email verify
                     ▼                        ▼
                 identity-verified         identity-verified
                  (auth_state = "verified")
```

* **Session id**: a UUID v4 string the mobile app generates ONCE on first
  launch and **persists** in secure storage (iOS Keychain, Android
  EncryptedSharedPreferences). Pass it as `session_id` in every chat-turn
  request. **DO NOT** rotate the session_id across user sessions — that
  loses conversation history.
* **No JWTs, no refresh tokens**. The backend identifies the session purely
  by `session_id` + the fingerprint headers (§3.4).
* **Sessions are persistent**: `/api/sessions/{id}` returns the full
  conversation history. Mobile can replay it on app resume.

### 3.1 Role gate (FIRST screen after splash)

When the user opens the app for the first time (or after sign-out), show a
3-option picker:

| ID         | Label shown to user      | Auth required? |
| ---------- | ------------------------ | -------------- |
| `client`   | I am an SMIFS client     | PAN + UCC      |
| `employee` | I work at SMIFS          | PAN + work email |
| `visitor`  | I am new to the site     | None           |

**Submit** with `POST /api/sessions/{session_id}/select_role`:

```json
// Request
{ "role": "client" }
// or "employee" / "visitor"

// Response
{
  "session_id": "f8e9d2a1-…",
  "session_type": "client",
  "auth_state": "anon",            // → "verified" only after PAN+UCC|email confirmed
  "blocks": [ { "type": "text", "text": "…opener text…" }, … ],
  "intent": "AUTH_PAN_REQUEST"     // bot's next-step hint
}
```

The `blocks[]` returned here are the bot's role-specific opener (NOT
chat-turn output). Render them inline as if the assistant just messaged
the user.

### 3.2 Identity verification (client + employee)

After role selection, the bot drives the credential exchange as normal
chat turns. The user just types their PAN, then the UCC (or work email).
On success the assistant emits a verified-identity opener and `intent`
becomes `AUTH_VERIFIED`.

Mobile does **not** need a separate "login form" — the role-picker plus
the next ~2 chat turns handle it. Server-side:

| Intent                | Meaning to mobile UX |
| --------------------- | -------------------- |
| `AUTH_PAN_REQUEST`    | Bot asked for PAN. Show keyboard, monospace input hint. |
| `AUTH_PAN_RETRY`      | Bot rejected the PAN. Reassure + re-prompt. |
| `AUTH_CHALLENGE`      | Bot asked for the second factor (UCC or work email). |
| `AUTH_LOCKED`         | Too many failures. Show "Try later or pick a different role". |
| `AUTH_NOT_FOUND`      | PAN didn't match any record. Suggest visitor path. |
| `AUTH_VERIFIED`       | Done. Persist `auth_state = "verified"` locally. |

### 3.3 Session metadata pull

`GET /api/sessions/{session_id}` returns:

```json
{
  "session_id": "…",
  "session_type": "client" | "employee" | "visitor",
  "auth_state":   "anon"   | "verified",
  "identity": { … OrgLens-shaped identity if verified … },
  "locale":   "en" | "hi" | "ta",
  "created_at": "2026-05-28T16:54:29.033+00:00",
  "messages": [
    { "role": "user"|"assistant", "content": "…", "blocks": [...], "citations": [...] },
    …
  ]
}
```

Use on app resume to rehydrate the chat scroll.

### 3.4 Device fingerprint headers

The web frontend silently fingerprints every device via FingerprintJS and
sends three headers on **every `/api/*` request** for fraud detection and
session continuity. Mobile should mirror this:

```http
X-Client-Fingerprint: <stable visitorId>
X-Client-Tz:          Asia/Kolkata
X-Client-Screen:      414x896@3
```

* **`X-Client-Fingerprint`** — a stable, app-install-scoped UUID. On iOS use
  `identifierForVendor`. On Android use a UUID generated once, stored in
  EncryptedSharedPreferences. **Do not** use IDFA / AAID (privacy + Play
  Store policy). Backend tolerates missing fingerprint headers but
  prefers them for risk scoring.
* **`X-Client-Tz`** — IANA timezone name from system locale.
* **`X-Client-Screen`** — `WIDTHxHEIGHT@DPR` as a hint for response sizing.

### 3.5 Sign-out

`POST /api/sessions/{session_id}/signout`

Clears `auth_state` back to `anon` on the server and tells the bot to drop
identity context. The session_id itself remains valid — the user simply
goes back to the role gate.

```json
// Response
{ "session_id": "…", "ok": true }
```

---

## 4. Chat API — the core endpoint

Two endpoints, same semantic contract:

| Endpoint                          | When to use                                 |
| --------------------------------- | ------------------------------------------- |
| `POST /api/agent/turn/stream`     | **Always prefer this**. Server-Sent Events for progressive streaming. |
| `POST /api/agent/turn`            | Fallback when SSE not available (rare).     |

Both accept the same request payload, return the same final envelope.

### 4.1 Request payload

```http
POST /api/agent/turn/stream HTTP/1.1
Host: bot.pesmifs.com
Content-Type: application/json
Accept: text/event-stream
X-Client-Fingerprint: <stable visitorId>
X-Client-Tz: Asia/Kolkata
X-Client-Screen: 414x896@3
```

```json
{
  "session_id": "f8e9d2a1-…",   // REQUIRED. Generate on first launch, persist.
  "message":    "What are NCDs?" // REQUIRED. The user's typed input.
}
```

* `session_id` may be `null` on the very first turn — backend will generate
  one and return it in the result envelope. Persist that value.
* Role and identity are **derived server-side** from the session record. Do
  NOT send `role` in the body — it's already known once `select_role` ran.

### 4.2 SSE event taxonomy

The server emits these `event:` types on `/api/agent/turn/stream`. All
`data:` payloads are JSON.

```
event: status       data: {"step":"router","label":"Routing your question"}
event: status       data: {"step":"fanout_ticker","label":"Pulling RELIANCE fundamentals"}
event: token        data: "NCDs, "
event: token        data: "or Non-Convertible Debentures, "
event: token        data: "are a type of fixed-income…"
event: citations    data: [{"doc_id":"…","title":"…","section":"…","score":0.78}]
event: warning      data: {"error_id":"…","message":"I hit a hiccup — sending a graceful reply."}
event: result       data: {  ← full envelope, see §4.3
                       "session_id":"…","intent":"TOOLS_PIPELINE_VISITOR",
                       "blocks":[…],"citations":[…],"model":"gpt-4o",
                       "trace":[…]
                     }
: ping              ← SSE comment-line heartbeat every ~10s (keep connection open)
```

| Event       | Frequency                   | Mobile behaviour                                             |
| ----------- | --------------------------- | ------------------------------------------------------------ |
| `status`    | 0..N during a turn          | Show a pill ("Routing your question…", "Pulling fundamentals…"). The `step` field is for telemetry; `label` is user-visible. |
| `token`     | 0..N. Word-group chunks (~3 words, ~25 ms apart, ~57 ms observed wire-time gap). | Append each chunk to the in-progress assistant bubble's text. **Use a per-chunk state update**, not a single batched setState. |
| `citations` | 0..1 (only when retrieval ran) | Persist in message metadata. **Do NOT render** unless `CHAT_SHOW_CITATIONS_TO_USER=true` (currently false in prod). |
| `warning`   | 0..1                        | Show a small banner ("hit a hiccup, sending graceful reply"). The next `result` event is still authoritative. |
| `result`    | exactly 1                   | The terminal envelope — render `blocks[]` in order. Close the bubble. |
| `: ping`    | every 10 s of silence       | SSE comment line; ignore. The parser must not crash on it.   |

**Hard timeout**: server caps stream lifetime at **60 s**. If the orchestrator
doesn't reach `result` by then, the server emits a graceful timeout
`warning` + `result` and closes.

### 4.3 The `result` envelope

```json
{
  "session_id": "f8e9d2a1-…",
  "intent":     "TOOLS_PIPELINE_VISITOR",
  "model":      "gpt-4o",
  "blocks": [
    { "type": "text", "text": "NCDs, or Non-Convertible Debentures, are …" },
    { "type": "suggested_actions",
      "options": [
        {"id":"1","label":"Compare with FDs"},
        {"id":"2","label":"Eligibility criteria"},
        {"id":"3","label":"Investment minimums"}
      ]
    }
  ],
  "citations": [
    { "doc_id":"…", "title":"NCD product brief", "section":"Eligibility",
      "score":0.78, "source":"smifs_kb" }
  ],
  "trace": [
    { "step":"router","intent":"KNOWLEDGE","confidence":0.91, … }
  ],
  "prior_session_id": null,
  "resume_offer":     null
}
```

* `blocks[]` — **render in order**. Each entry is a structured block (§5).
  The leading `text` block is also what got streamed via `token` events;
  the rest of the array (form / chips / cards) appears after the stream
  completes.
* `intent` — router classification, useful for telemetry & block-skip
  decisions but not for UX rendering.
* `citations` — keep, do not render.
* `trace` — debug info; useful in dev menu only.
* `prior_session_id` + `resume_offer` — used when an anonymous→identified
  user has a previous session worth offering to continue. See `resume_offer`
  block (§5).

### 4.4 Non-streaming fallback

If SSE fails (proxy, OS-level connection issue, network plane outage),
mobile should retry once on the **buffered** endpoint:

```http
POST /api/agent/turn
Content-Type: application/json
{ "session_id": "…", "message": "…" }
```

Response is exactly the `result` envelope (§4.3) without the streaming
prelude. The user experiences a single delayed render instead of
progressive build. **Acceptable** as a graceful fallback.

### 4.5 SSE consumer — concrete snippets

**Android (Kotlin, OkHttp + okhttp-sse)**

```kotlin
val client = OkHttpClient.Builder()
    .readTimeout(0, TimeUnit.MILLISECONDS) // SSE: no read-timeout
    .build()

val body = """{"session_id":"$sid","message":"${msg.replace("\"","\\\"")}"}""".toRequestBody(JSON)
val req = Request.Builder()
    .url("https://bot.pesmifs.com/api/agent/turn/stream")
    .header("Accept", "text/event-stream")
    .header("X-Client-Fingerprint", fingerprint)
    .header("X-Client-Tz", TimeZone.getDefault().id)
    .header("X-Client-Screen", screen)
    .post(body)
    .build()

val factory = EventSources.createFactory(client)
factory.newEventSource(req, object : EventSourceListener() {
    override fun onEvent(es: EventSource, id: String?, type: String?, data: String) {
        when (type) {
            "status"    -> updateStatusPill(JSONObject(data).getString("label"))
            "token"     -> appendChunk(JSONObject(data).toString()) // value is a quoted string
            "citations" -> stashCitations(JSONArray(data))
            "warning"   -> showHiccupBanner(JSONObject(data).getString("message"))
            "result"    -> renderFinalEnvelope(JSONObject(data))
        }
    }
    override fun onFailure(es: EventSource, t: Throwable?, response: Response?) {
        fallbackToBufferedTurn(sid, msg)
    }
})
```

**iOS (Swift, URLSession + line-based event parser)**

Apple ships no built-in EventSource. Roll a 25-line parser or pull in
`LDSwiftEventSource` (Launch Darkly's MIT-licensed implementation).

```swift
import LDSwiftEventSource

let cfg = EventSource.Config(
    handler: SSEHandler(),
    url: URL(string: "https://bot.pesmifs.com/api/agent/turn/stream")!
)
.connectionErrorHandler { err in .proceed }
.headers([
    "Accept": "text/event-stream",
    "X-Client-Fingerprint": fingerprint,
    "X-Client-Tz": TimeZone.current.identifier,
    "X-Client-Screen": "\(screen.width)x\(screen.height)@\(scale)"
])
.method("POST")
.body(jsonBody)
.idleTimeout(70.0) // > 60s server cap + 10s slack

let es = EventSource(config: cfg)
es.start()

class SSEHandler: EventHandler {
    func onMessage(eventType: String, messageEvent: MessageEvent) {
        switch eventType {
        case "status":    updateStatusPill(parse(messageEvent.data))
        case "token":     appendChunk(messageEvent.data)
        case "citations": stashCitations(messageEvent.data)
        case "warning":   showHiccupBanner(parse(messageEvent.data))
        case "result":    renderFinalEnvelope(parse(messageEvent.data))
        default: break
        }
    }
    func onComment(comment: String) { /* keepalive : ping — ignore */ }
    func onClosed() { /* stream ended */ }
    func onError(error: Error) { fallbackToBufferedTurn() }
}
```

**Critical**: append each `token` event to the in-progress bubble's text
**immediately**, with a per-chunk state mutation. Do NOT buffer to a
single render on stream close — that defeats the progressive UX (and
reproduces the Phase 29 "flashing" bug we just fixed).

---

## 5. Block Type Reference

Every assistant message contains a `blocks: []` array. Render the blocks
**in order, top-to-bottom**. A block is renderable iff your dispatcher
has a case for its `type` — unknown types should be silently skipped
(forward-compat: backend may add new block types without mobile updates).

The web reference dispatcher lives at `frontend/src/pages/Chat.jsx:renderBlock`.
Each block's JSX component is in `frontend/src/components/blocks/`.

### 5.1 `text`

The leading narrative block. Plain markdown-light prose (no tables, no
fenced code). The text streamed via `token` events is exactly this
block's content.

```json
{ "type": "text",
  "text": "NCDs, or Non-Convertible Debentures, are a type of fixed-income…",
  "role": "assistant"
}
```

* Render in a bubble styled per §9 (light text on dark surface, serif
  optional for the leading paragraph).
* Tap-to-copy is nice-to-have.
* The `role` field is reserved for future use (e.g., system notices); for
  now treat anything not `"assistant"` as system info.

Web component: `TextBlock.jsx`.

### 5.2 `suggested_actions` (Phase 29b — required UX)

After every substantive answer, the backend appends exactly 3 chips.
Tapping a chip submits its `label` as the user's next message.

```json
{ "type": "suggested_actions",
  "options": [
    {"id":"1","label":"Compare with FDs"},
    {"id":"2","label":"Eligibility criteria"},
    {"id":"3","label":"Investment minimums"}
  ]
}
```

* **Always exactly 3 chips**. If you receive fewer, the backend bug —
  render what you got.
* `label` is ≤ 60 characters, persona-aware (visitor: discovery; client:
  portfolio-tied; employee: ops/research). See §7.
* On tap: submit the label as the next chat-turn message, disable all 3
  chips (prevent double-submit), DO NOT render this `suggested_actions`
  block on prior history scrollback — chips are valid only for the
  LATEST assistant message.
* Skip rules (backend-enforced — your dispatcher does NOT need to
  re-apply them, but be aware): no chips after `dynamic_form`, after
  `low_confidence_escalation`, after farewell user messages ("thanks
  bye"), after AUTH_VERIFIED opener (the opener already proposes next
  actions in text).

Web component: `SuggestedActionsBlock.jsx`.

### 5.3 `dynamic_form` (the 5-form family)

Inline form with user-chosen fields. Submitted via `POST /api/forms/submit`
(§6).

```json
{ "type": "dynamic_form",
  "form_id": "complaint_capture",
  "title": "Register a complaint",
  "subtitle": "We take this seriously. Senior advisor will reach out within 4 business hours.",
  "fields": [
    { "name":"subject", "label":"Subject", "type":"text", "required": true },
    { "name":"description", "label":"What happened?", "type":"textarea",
      "required": true, "min_chars": 30 },
    { "name":"affected_rm", "label":"Affected relationship manager (optional)",
      "type":"text", "required": false }
  ],
  "submit_label": "Register complaint",
  "submit_endpoint": "/api/forms/submit",
  "priority": "high",
  "success_message": "We take this seriously. A senior advisor will personally reach out within 4 business hours."
}
```

* `fields[].type` ∈ `text` | `textarea` | `email` | `tel` | `select` | `number`.
* `select` fields carry an `options: ["…", "…"]` array.
* On submit success: emit a `form_submitted` (server-side block) — see §5.4.
* Validation: enforce `required` + `min_chars` on textarea client-side;
  backend re-validates. Show inline error on missing required.

Web component: `DynamicFormBlock.jsx`. Full schemas in §6.

### 5.4 `form_submitted`

Confirmation card the backend emits after `/api/forms/submit` succeeds.
The mobile app renders the success message; the bot continues normally
on the next turn.

```json
{ "type": "form_submitted",
  "form_id": "complaint_capture",
  "submission_id": "uuid-…",
  "message": "Complaint registered. Reference: BA0ED6F5. Senior advisor will reach out within 4 business hours.",
  "email_status": "sent"   // or "queued" | "failed"
}
```

### 5.5 `bmia_fundamentals_card`

Stock fundamentals card emitted after the BMIA `fundamentals` tool call
(e.g. user asks "Tell me about RELIANCE").

```json
{ "type": "bmia_fundamentals_card",
  "data": {
    "symbol": "RELIANCE",
    "about":  "Reliance was founded by Dhirubhai Ambani…",
    "last_fetched": "2026-05-28T16:00:00Z",
    "pros": [],
    "cons": [
      "Company has a low return on equity of 8.91% over last 3 years.",
      "Dividend payout has been low at 10.2% of profits over last 3 years"
    ],
    "profit_loss_3y": {
      "periods": ["Mar 2024","Mar 2025","Mar 2026"],
      "rows": { "Sales +": [899041, 980000, 1057219],
                "EPS in Rs": [49.20, 53.10, 59.69] }
    }
  }
}
```

* Render brand-strap (NSE · FUNDAMENTALS) + symbol + as-of pill.
* Pros / Cons two-column.
* Sparklines: pure SVG, NO charting library. Last 3-5 years EPS + last 4
  quarters Sales. Up-arrow on positive trend, down-arrow on negative.
* "View full statements" expands the long-form `profit_loss`,
  `balance_sheet`, `cash_flow`, `ratios` slices (collapsed by default —
  reduces scroll fatigue on mobile).

Web component: `BmiaFundamentalsCard.jsx`.

### 5.6 `low_confidence_escalation`

The anti-bluff rail. Emitted when retrieval confidence is below threshold
OR the LLM explicitly refuses to answer.

```json
{ "type": "low_confidence_escalation",
  "user_facing_text": "I don't have a confident answer on that specific topic. Let me route you to a senior advisor who can help.",
  "intent": "LOW_CONFIDENCE_ESCALATION",
  "confidence": { "router": 0.34, "rag_top1": 0.41 }
}
```

* Always accompanied by a sibling `handoff_request` block (next item).
* Visually: warning-toned rail card with a "Routing to advisor" eyebrow.
  See `LowConfidenceEscalation.jsx` for the visual reference.
* **No `suggested_actions` chips** follow this block — the rail owns the CTA.

### 5.7 `handoff_request`

Hand-off CTA — call/message a relationship manager.

```json
{ "type": "handoff_request",
  "target_kind": "rm" | "advisor" | "hrbp",
  "target_display_name": "Aaditya Jaiswal",
  "target_contact_masked": "+91 98••• 43210",
  "handoff_type": "whatsapp" | "email",
  "deep_link": "https://wa.me/919876543210?text=Hi%2C%20I%27m%20…",
  "user_question": "What's a Mackertich PMS for ₹10 lakh?"
}
```

* Render as a primary CTA button. On tap, open the `deep_link` in the
  system browser / mail client.
* If `target_has_contact` is false (no RM assigned), the backend instead
  emits a `callback_request` form (see §6).
* On tap, also fire `POST /api/handoff` with the same context so backend
  logs the hand-off ID:

```json
POST /api/handoff
{
  "session_id":     "…",
  "handoff_type":   "whatsapp",
  "channel_target": "rm",
  "user_question":  "What's a Mackertich PMS for ₹10 lakh?",
  "context_snippet": "<last 2 assistant messages>"
}
```

### 5.8 `callback_request`

When no RM is assigned OR the user explicitly asks for a callback. Same
schema family as `dynamic_form` but with the `callback_request` form_id.
See §6.5 for full schema.

### 5.9 `locale_choice`

The bot offers a locale picker, usually on first turn of a new session.

```json
{ "type": "locale_choice",
  "current": "en",
  "options": [
    {"id":"en","label":"English","native":"English","hint":"Default"},
    {"id":"hi","label":"Hindi","native":"हिंदी","hint":"Devanagari"},
    {"id":"ta","label":"Tamil","native":"தமிழ்","hint":"Tamil script"}
  ]
}
```

* Render as 3 inline chips with native script label.
* On tap, `POST /api/agent/locale` (§8) with the chosen id; the next turn
  responds in that locale.

Web component: `LocaleChoiceBlock.jsx`.

### 5.10 `vehicle_cta`

Inline CTA to open a vehicle factsheet (NCD / AIF / PMS / MF).
Emitted alongside a `text` block when retrieval cites a specific vehicle.

```json
{ "type": "vehicle_cta",
  "vehicle_id":   "uuid",
  "vehicle_name": "PURPLE STYLE LABS | DEBT FUNDING",
  "vehicle_type": "NCD",       // or "AIF" | "PMS" | "MF" | null
  "label":        "Open the vehicle factsheet · PURPLE STYLE LABS | DEBT FUNDING",
  "action":       "handoff_or_factsheet"
}
```

* Pill-shaped chip below the text block.
* Backend dedupes by `vehicle_id` and caps at 2 per turn.
* On tap, for v1 mobile: open the factsheet URL (`/vehicle/{vehicle_id}.pdf` if
  one exists) OR trigger a `handoff_request` programmatically. Coordinate
  with backend team — `action` value is forward-looking.

Web component: `VehicleCtaBlock.jsx`.

### 5.11 `citation_chips`

Citation chips. **HIDDEN BY DEFAULT** via env flag `CHAT_SHOW_CITATIONS_TO_USER`
(currently `false` on prod). Mobile must receive but not render unless that
flag is `true`.

```json
{ "type": "citation_chips",
  "chips": [
    { "doc_id":"…", "title":"NCD product brief", "section":"Eligibility",
      "score":0.82, "source":"smifs_kb" }
  ]
}
```

When enabled: render as small grey chips below the text block, tap-to-expand
into a popover showing the cited passage.

### 5.12 `resume_offer`

When an anonymous user logs in and the backend finds a prior anonymous
session worth offering to continue.

```json
{ "type": "resume_offer",
  "prior_session_id": "…",
  "summary": "You were asking about NCDs and complaint resolution.",
  "options": [
    { "id":"resume",  "label":"Continue where I left off" },
    { "id":"decline", "label":"Start fresh" }
  ]
}
```

* On "resume": `POST /api/sessions/{session_id}/resume` with body `{"prior_session_id": "..."}`
* On "decline": `POST /api/sessions/{session_id}/decline_resume`

### 5.13 Other blocks (lower-frequency)

| Block type             | Purpose                                             | Web component                  |
| ---------------------- | --------------------------------------------------- | ------------------------------ |
| `market_card`          | Snapshot of a market index / ticker.                | `MarketCardBlock.jsx`          |
| `client_card`          | Identity-verified client profile card.              | `ClientCardBlock.jsx`          |
| `employee_card`        | Identity-verified employee profile card (OrgLens).  | `EmployeeCardBlock.jsx`        |
| `directory_card`       | Single org-directory entry (one employee lookup).   | `DirectoryCardBlock.jsx`       |
| `directory_list`       | Multiple org-directory entries.                     | `DirectoryListBlock.jsx`       |
| `org_stats_card`       | Headcount / branch counts overview.                 | `OrgStatsCardBlock.jsx`        |
| `reporting_chain_card` | Manager chain (employee → manager → …).             | `ReportingChainCardBlock.jsx`  |
| `table`                | Simple tabular data.                                | `TableBlock.jsx`               |
| `chart`                | URL-anchored static PNG chart (`/api/charts/{id}.png`). | `ChartBlock.jsx`            |
| `image`                | A generated PNG (e.g. portfolio donut).             | `ImageBlock.jsx`               |
| `download`             | Downloadable PDF / CSV.                             | `DownloadBlock.jsx`            |
| `role_choice`          | Yes/No or N-way choice ("Log a sale?").             | `RoleChoiceBlock.jsx`          |
| `product_choice`       | Multi-product CTA grid.                             | `ProductChoiceBlock.jsx`       |
| `sale_form` / `sale_confirmation` | Employee-only quick-sale-entry workflow. | `SaleFormBlock.jsx`, `SaleConfirmationBlock.jsx` |
| `escalation_card`      | Lighter-weight escalation card (vs the rail).       | `EscalationBlock.jsx`          |

For each, the JSX component in `frontend/src/components/blocks/` is the
authoritative schema reference — read the file's leading comment for the
exact shape.

---

## 6. Dynamic Forms — the 5 form types

All 5 forms share the `dynamic_form` block schema (§5.3). They differ only
in `form_id`, fields, and `success_message`. Submit via:

```http
POST /api/forms/submit
Content-Type: application/json

{
  "form_id":   "complaint_capture",
  "form_data": { "subject": "…", "description": "…", "affected_rm": "" },
  "session_id":"…",
  "context":   { /* optional - the bot may have populated context */ }
}

// Response
{
  "submission_id": "uuid",
  "message":       "Complaint registered. Reference: BA0ED6F5. …",
  "email_status":  "sent"   // or "queued" | "failed"
}
```

Rate-limit: same as `/api/leads` — by session + by IP.

### 6.1 `demand_capture` — soft lead capture

* **Trigger**: after a substantive product / pricing answer, max once per
  3 conversation turns.
* **Fields**: name (text, required), email (email, required),
  phone (tel, optional), interest_topic (text, optional — auto-populated
  from chat context).
* **Success**: "Thanks! Our team will reach out about {topic} shortly."

### 6.2 `referral_capture` — refer a friend

* **Trigger**: when the user says "I want to refer someone" or similar.
  7-day cooldown per session.
* **Fields**: your_name, your_phone, friend_name, friend_phone,
  friend_relationship (select: Friend / Family / Colleague / Other).
* **Success**: "We'll reach out to {friend_name} on your recommendation."

### 6.3 `feedback_capture` — collect Net Promoter

* **Trigger**: after `n` turns or on explicit "I want to give feedback"
  signal. Max once per session.
* **Fields**: rating (number 1-5, required), what_worked (textarea),
  what_to_improve (textarea), would_recommend (select: Definitely /
  Probably / Not sure / No).
* **Success**: "Thanks — your feedback helps us improve."

### 6.4 `complaint_capture` — formal complaint

* **Trigger**: any message containing complaint / issue / grievance /
  escalate signals.
* **Fields**:
  * `subject` — text, required.
  * `description` — textarea, required, **min 30 chars** (client-side
    enforce; backend re-validates).
  * `affected_rm` — text, optional. Backend pre-populates if the verified
    client has an assigned RM.
* **Priority**: `"high"` (drives nightly mongodump retention).
* **Success**: "We take this seriously. A senior advisor will personally
  reach out within 4 business hours."

### 6.5 `callback_request` — schedule a callback

* **Trigger**: when user asks for a callback OR `low_confidence_escalation`
  + no RM assigned.
* **Fields**:
  * `name` — text, required.
  * `phone` — tel, required, placeholder `+91 98765 43210`.
  * `email` — email, optional.
  * `preferred_time` — select, required. Options:
    * "Today, 10am–1pm"
    * "Today, 2pm–6pm"
    * "Tomorrow morning"
    * "Tomorrow afternoon"
    * "Next available slot"
  * `topic` — text, optional (auto-populated from chat context).
* **Success**: "Got it — a senior advisor will reach out at your preferred time."

### 6.6 Submission flow (end-to-end)

```
1. User sees `dynamic_form` block, fills it in.
2. Mobile validates client-side: required + min_chars + email/tel regex.
3. Mobile POSTs to `submit_endpoint` (always `/api/forms/submit`).
4. Server persists to mongo `forms_submissions`, fires SMTP email to
   brand@smifs.com via the `submitted_at` worker.
5. Server returns `submission_id` + success_message + email_status.
6. Mobile renders a success card (use form_submitted block schema OR
   inline render).
7. Conversation continues normally.
```

* `email_status="failed"` is non-fatal — submission is still persisted
  and admin can retry via `POST /api/admin/forms/{submission_id}/retry`.

---

## 7. Persona-Aware Behavior

Persona is **derived server-side** from the session record. Mobile never
sends a `persona` field — `session_type` is already set by `select_role`
(§3.1), and `auth_state` upgrades to `"verified"` after PAN+UCC|email
confirms. The orchestrator reads both from the session row on every turn.

### Behavioral differences

| Aspect                 | Visitor                              | Client                                  | Employee                                   |
| ---------------------- | ------------------------------------ | --------------------------------------- | ------------------------------------------ |
| **System prompt tone** | Educational, qualifying.            | Personal, portfolio-aware.              | Concise, operational, peer-to-peer.        |
| **Fan-out events**     | Ticker (limited), product.          | Ticker, product, identity, portfolio.   | Ticker, product, identity, OrgLens lookups, sales pitch generation. |
| **Form triggers**      | demand, feedback, callback.         | All 5 (incl. complaint, referral).      | None visible (employees use Admin tab).    |
| **Suggestion chips**   | Discovery + qualification.          | Portfolio-tied + service.               | Ops + research + sales.                    |
| **Hand-off target**    | Generic "advisor".                   | Assigned RM (if any).                   | HRBP / internal escalation paths.          |
| **OrgLens scope**      | None.                                | Self-only.                              | Full (subject to OrgLens permissions).     |

### Same question, three chip families (live data)

User asks "What are NCDs?":

* **Visitor chips**: `Compare with FDs`, `Eligibility criteria`, `Investment minimums`
* **Client chips**:  `Show my NCD holdings`, `Upcoming NCD interest dates`, `Talk to my relationship manager`
* **Employee chips**: `Pull PSLR KYI Sheet`, `Generate NCD sales pitch`, `Check SEBI NCD guidelines`

The system prompt addendum is driven by `agents/suggestion_agent.py::_PERSONA_DIRECTIVE`
(read that file's source for the exact text).

---

## 8. Localization

### Supported locales

| Code | Label   | Native     | Status              |
| ---- | ------- | ---------- | ------------------- |
| `en` | English | English    | Default; complete.  |
| `hi` | Hindi   | हिंदी       | Chat prose only.    |
| `ta` | Tamil   | தமிழ்       | Chat prose only.    |

### Set the session locale

```http
POST /api/agent/locale
Content-Type: application/json

{ "session_id": "…", "locale": "hi" }

// Response
{ "session_id": "…", "locale": "hi", "supported": ["en","hi","ta"] }
```

The next chat turn responds in the chosen locale; the orchestrator
injects the locale into every LLM system prompt.

### What is and isn't localized

| Localized                              | Stays English (by design)                  |
| -------------------------------------- | ------------------------------------------ |
| `text` block prose.                    | `dynamic_form.fields[].label`              |
| `low_confidence_escalation.user_facing_text` | `dynamic_form` validation messages   |
| Status pill labels.                    | `bmia_fundamentals_card` field names       |
| `suggested_actions.options[].label`    | Regulatory terms (PAN, UCC, NAV, NCD, …)   |
|                                        | Citation chip metadata                     |
|                                        | Admin endpoints                            |

This split is intentional — forms and structured data stay machine-parseable
and audit-clean.

### The `locale_choice` block

The bot offers a locale picker either inline (block) or in the header
(popover) on first turn or when the user types "Switch to Hindi" /
"भाषा बदलो" etc. See §5.9. On tap, fire `POST /api/agent/locale` then
re-issue the last user turn so the assistant responds in the new locale.

---

## 9. Theme & Visual Language

The web app's `App.css` is the source of truth. Mobile should mirror these
tokens.

### Brand strap

```
Mackertich ONE
WEALTH MANAGEMENT · SMIFS LTD
```

Logo: monogrammed "R1" mark on the left, brand strap right of it. See the
production screenshot for layout.

### Palette (CSS custom properties from `App.css`)

| Token                       | Hex        | Use                                          |
| --------------------------- | ---------- | -------------------------------------------- |
| `--smifs-green-darkest`     | `#023726`  | App background (full dark surface).          |
| `--smifs-green-deep`        | `#065b40`  | Card surfaces, deeper layer.                 |
| `--smifs-green`             | `#098c62`  | Primary accent (CTAs, brand mark).           |
| `--gold-soft`               | `#14a47a`  | Hover state for primary CTAs.                |
| `--gold-deep`               | `#066b4a`  | Pressed state for primary CTAs.              |
| `--smifs-green-tint`        | `#e8f5ef`  | Light surface (rare — used in BMIA card).    |
| `--ink`                     | `#ecf3ef`  | Primary text on dark surfaces.               |
| `--ink-muted`               | `#a4c4b4`  | Secondary text, eyebrows, captions.          |
| `--ink-dim`                 | `#6e8b7c`  | Disabled, placeholders, hairlines.           |
| `--smifs-ink`               | `#191a15`  | Primary text on the BMIA light card.         |
| `--smifs-canvas`            | `#ffffff`  | BMIA card surface (the ONE light surface).   |
| `--smifs-canvas-soft`       | `#f9f9f9`  | BMIA card section background.                |
| `--smifs-hairline`          | `#d9d9d9`  | BMIA card borders.                           |

**Avoid**:
* Purple / violet (against brand).
* Pure black backgrounds (`#000` — too harsh; use `#023726`).
* Cyan accents (clashes with green).

### Typography

```css
--font-serif: "Libre Baskerville", Georgia, "Times New Roman", serif;
--font-sans:  "Inter", "Helvetica Neue", -apple-system, BlinkMacSystemFont,
              "Segoe UI", Roboto, sans-serif;
```

On mobile:

* **Serif** (Libre Baskerville → fall back to system serif): brand strap,
  block headings (`Mackertich ONE Advisor`), card titles ("RELIANCE",
  "Register complaint").
* **Sans** (Inter → fall back to SF Pro / Roboto): body text, chat bubbles,
  chips, buttons, form fields.
* **Mono** (JetBrains Mono / SF Mono): session id pill in the footer,
  PAN/UCC inputs.

Bundle the fonts in the app if you want pixel-perfect parity. Otherwise
the fallback chain renders cleanly on both platforms.

### Text scale (Tailwind / DP equivalents)

| Role            | Size (mobile)               |
| --------------- | --------------------------- |
| H1 brand title  | 28 pt serif, light weight   |
| H2 section      | 18 pt serif                 |
| Body            | 15 pt sans                  |
| Captions / eyebrows | 11 pt sans, uppercase, tracked +0.06em |

### Surface treatment

* Subtle blob backgrounds (gold + teal radial gradients, 18% opacity, blur
  72 px) on the main canvas.
* Grain overlay at 2-3% opacity on top of the canvas (see `.smifs-grain`
  in App.css).
* All cards have a 1 px hairline border at `rgba(255,255,255,0.12)` and
  4-8 px radius.

### Component conventions

* **Buttons**: pill-shaped (`border-radius: 999px`), 32-36 dp height,
  emerald fill or transparent + 1px border. Light text. Hover/pressed
  states drift toward `--gold-soft`.
* **Inputs**: dark surface with hairline border, no inner shadow. Caret
  is emerald.
* **Chips**: pill, `--smifs-green` accent on focus, white/85 text.

### Motion

* Status pill: fade-in 120 ms.
* Token chunks appended without re-layout (use a contained text node, not
  a re-render of the whole bubble).
* Chip press: 100 ms scale 0.98 + emerald glow.

---

## 10. UX Patterns the Mobile App Should Mirror

These behaviours are NOT optional — they're table-stakes for parity with web.

### 10.1 Anti-bluff rail

When `intent === "LOW_CONFIDENCE_ESCALATION"`:

* Show the rail card (§5.6).
* **No suggestion chips below**.
* The accompanying `handoff_request` block is the only CTA.
* DO NOT autoplay a `text` block as if the bot answered — the rail IS the
  answer.

### 10.2 Suggestion chip pattern (Phase 29b)

Already detailed in §5.2. Recap:

* Always 3 chips, ≤ 60 chars each.
* Tap submits as next user message.
* Disable all 3 on first tap (no double-submit).
* Only on the LATEST assistant message — history scrollback should NOT
  re-render chips.
* Skip rules backend-enforced (form / rail / farewell).

### 10.3 Citation chips (HIDDEN)

Backend env flag: `CHAT_SHOW_CITATIONS_TO_USER=false` (prod default).
**Mobile must not render** `citations[]` or `citation_chips` blocks until
the backend flips the flag. Persist them in message metadata so a future
"reveal citations" toggle in settings could surface them without a backend
change.

### 10.4 Streaming UX timeline (single chat turn)

```
T=0      User taps Send.
T+~100ms first `status` event arrives. Render "Routing your question…" pill.
T+~Xs    More `status` events (`fanout_ticker`, `synthesis`, …) as the
         orchestrator progresses. Update the pill in place.
T+Xs     First `token` event. Drop the pill. Start appending chunks to
         the bubble's text node.
T+Xs+0.5s last `token` event (typical ~150-word answer streams over 500ms
         at ~57 ms inter-token gap).
T+Xs+0.6s `citations` event (silent — stash only).
T+Xs+0.7s `result` envelope. Render any non-text blocks (form / chips /
         card) below the bubble. Re-enable composer.
```

### 10.5 Latency expectations (Phase 29 baseline)

| Path                                       | First token | Total wall-clock |
| ------------------------------------------ | ----------- | ---------------- |
| Simple greeting ("hi")                     | < 1 s       | 1-2 s            |
| Identity opener (post-AUTH_VERIFIED)       | 2-3 s       | 3-4 s            |
| Knowledge Q&A ("What are NCDs?")           | 6-12 s      | 7-13 s           |
| Ticker fan-out ("Tell me about RELIANCE")  | 6-9 s       | 8-11 s           |
| Complaint form trigger                     | 1-2 s       | 2-3 s            |

The 6-12s "first-token" for knowledge Q&A is the orchestrator's tool-calling
loop running before pseudo-stream kicks off — Phase 30 candidate to reduce.
For now show status pills aggressively so the user knows work is happening.

### 10.6 Optimistic UI

* User taps Send → push their message into the chat immediately with
  `pending` state. Show the streaming assistant bubble alongside.
* If the request fails entirely, show a retry button on the user bubble
  (don't lose the message).

---

## 11. Admin / Diagnostic Endpoints

These are dev / admin tools — **do NOT embed the admin token in mobile
binaries**. Useful only if you're building an in-app dev menu.

| Endpoint                                          | Returns                                       |
| ------------------------------------------------- | --------------------------------------------- |
| `GET  /api/admin/forms/submissions?limit=20`      | Recent form submissions for admin review.     |
| `POST /api/admin/forms/{submission_id}/retry`     | Force-retry SMTP email for one submission.    |
| `GET  /api/admin/cost_ledger?days=7`              | Hub AI token + USD cost ledger.               |
| `GET  /api/admin/insight/top_asks?days=30`        | Top tickers/products/identities asked about.  |
| `GET  /api/admin/errors/recent?limit=20`          | Recent uncaught exceptions (with traceback).  |
| `GET  /api/admin/errors/summary`                  | Last-24h grouped error counts.                |
| `GET  /api/admin/reembed/estimate`                | Doc-chunk re-embed migration cost preview.    |
| `POST /api/admin/reembed/run`                     | Trigger re-embed job (idempotent).            |
| `GET  /api/admin/reembed/status/{job_id}`         | Poll a re-embed job.                          |

Auth: `Authorization: Bearer smifs-admin-2026` OR `X-Admin-Token: smifs-admin-2026`.

For mobile observability, prefer `/api/client_errors` (§12).

---

## 12. Error Handling

### 12.1 HTTP status codes

| Code | Meaning                                      | Mobile action                                                              |
| ---- | -------------------------------------------- | -------------------------------------------------------------------------- |
| 200  | OK                                           | Normal path.                                                               |
| 400  | Validation error (e.g. unknown `form_id`)    | Show field-level error from `detail` string.                               |
| 401  | Admin token missing/invalid                  | Should never happen on user endpoints.                                     |
| 404  | Session / submission / job not found         | Re-init session or show "not found".                                       |
| 422  | FastAPI validation                           | Show "invalid request" + log to client_errors.                             |
| 429  | Rate limit                                   | Server response includes a `blocks: [{"type":"text","text":"You're going a bit fast…"}]`. Render those blocks; back off 5 s before retry. |
| 5xx  | Backend hiccup                               | Single retry (jitter 250 ms-1 s). If still failing, log to client_errors and show graceful banner. |

### 12.2 Graceful degradation chain

```
1. Try `/api/agent/turn/stream` (SSE).
2. On network/SSE failure → retry once.
3. On second failure → fall back to `/api/agent/turn` (buffered).
4. On THAT failure → show "Trouble reaching Mackertich ONE — retry?" CTA.
   Log to `/api/client_errors`. Optionally surface the prior session
   transcript from `/api/sessions/{id}` so the conversation isn't lost.
```

### 12.3 Telemetry for client-side failures

```http
POST /api/client_errors
Content-Type: application/json

{
  "session_id": "…",
  "kind":       "sse_stream_error" | "render_block_failed" | "network_lost" | …,
  "message":    "Stream closed at +12.3s without `result` event",
  "trace":      "<stack>",
  "context":    { "endpoint":"/api/agent/turn/stream","platform":"ios" }
}
```

Fire-and-forget; do not block UX on its success.

### 12.4 Heartbeat handling

SSE comment-line `: ping` every ~10 s of silence. The OkHttp / EventSource
parsers handle this transparently. Verify your parser doesn't crash on
comment-only lines.

---

## 13. External Integrations

The mobile app talks ONLY to `bot.pesmifs.com`. All third-party calls fan
out from the backend:

| External           | Role                                                                |
| ------------------ | ------------------------------------------------------------------- |
| **Hub AI** (`ai.superclue.io`) | LLM completions (gpt-4o, gpt-4o-mini, llama-3.3-70b, gemma-4, claude-haiku) + embeddings (text-embedding-3-large @ 3072d). |
| **OrgLens** (`orglens.pesmifs.com`) | Identity verification, employee directory, client portfolio, holdings, reporting chain. |
| **BMIA** (compliance + fundamentals) | NSE fundamentals (`bmia_fundamentals_card`), daily briefing, anti-bluff signals. |
| **SMTP relay**     | Outbound emails for form submissions to `brand@smifs.com`.          |

Mobile **does not** authenticate to any of these. The backend's API keys
live in `/opt/mackertich/.env` (chmod 600 on the VPS).

---

## 14. Push Notifications (TBD)

**Not built yet.** Forward-looking schema for the mobile team to comment on:

```http
POST /api/notifications/register
{
  "session_id":   "…",
  "platform":     "ios" | "android",
  "device_token": "<APNs token | FCM token>",
  "topics":       ["rm_message","callback_scheduled","sip_debit"]
}
```

```http
GET /api/notifications/preferences
// returns the per-topic on/off matrix the user controls in app Settings
```

**Proposed notification kinds** (none implemented yet — coordinate with
backend team before building UI):

| Topic               | Trigger                                                | Payload    |
| ------------------- | ------------------------------------------------------ | ---------- |
| `rm_message`        | RM sent a message to the user via the admin console.   | `{session_id, message_excerpt}` |
| `callback_scheduled`| `callback_request` form submitted; RM confirms a slot. | `{at_iso, rm_name}`             |
| `sip_debit`         | An SIP debit went through.                             | `{amount, fund_name, date}`     |
| `complaint_update`  | Status change on a registered complaint.               | `{submission_id, status}`       |

**Bring back to backend**: how does the backend know which device to push
to? Probably needs a `devices` collection keyed by `session_id` +
`device_token`. Open issue — see §18.

---

## 15. Build Targets

### Platform matrix

| Platform | Min version           | Notes                                            |
| -------- | --------------------- | ------------------------------------------------ |
| iOS      | 15.0+                 | SwiftUI preferred; URLSession SSE OK.            |
| Android  | API 26 (Oreo, 8.0+)   | Jetpack Compose preferred; OkHttp 4.12+.         |
| iPad     | Same iOS 15+          | Optional — bias to phone-first.                  |
| Android tablet | Same API 26+   | Optional.                                        |

### Mandatory capabilities

* **Dark mode** — app is dark-by-default to match brand. No light theme
  required for v1.
* **System font fallback** — bundle Libre Baskerville + Inter, fall back
  to platform defaults.
* **Accessibility**:
  * VoiceOver / TalkBack must read `suggested_actions` chips as buttons
    with their label as the accessibility label.
  * `dynamic_form` fields must announce `required`, `label`, and
    `min_chars` constraints.
  * Streaming text bubble: announce only on stream end (not on each
    chunk — that's a screen-reader DOS).
* **Offline graceful degradation**: when network drops, queue the user's
  message locally and retry on reconnect. Show a "queued" badge.
* **Privacy**: device fingerprint must NOT use IDFA / AAID. Use
  `identifierForVendor` on iOS, app-scoped UUID on Android.
* **No analytics SDKs** that exfiltrate chat content. The backend already
  logs everything in mongo.
* **App Tracking Transparency (iOS 14.5+)**: not applicable for v1 since
  we use no cross-app tracking.

---

## 16. Test Plan Skeleton

Run these before every release; both Production and Staging if relevant.

| # | Test                                                | Expected                                                                 |
| - | --------------------------------------------------- | ------------------------------------------------------------------------ |
| 1 | Cold-start → role gate appears                      | 3 options (Client, Employee, Visitor) within 1 s of splash.              |
| 2 | Pick "Visitor", send `"hi"`                         | Status pill → progressive token stream → final text + 3 chips below.    |
| 3 | Pick "Visitor", send `"What are NCDs?"`             | Text block + 3 NCD-specific chips (e.g. "Compare with FDs").             |
| 4 | Tap chip #1                                         | Submits as next user message; new assistant turn produces new chips.    |
| 5 | Send `"I have a complaint"`                         | `text` + `dynamic_form` (form_id `complaint_capture`). **No chips.**     |
| 6 | Fill complaint form (subject + 30+ char description) → submit | `submission_id` returned; success card; chat continues.        |
| 7 | Send `"Tell me about RELIANCE"`                     | Status pill ("Pulling RELIANCE fundamentals"), then text + `bmia_fundamentals_card` + 3 chips. |
| 8 | Send `"What is the dress code at SMIFS Mumbai?"` (anti-bluff) | `low_confidence_escalation` + `handoff_request` rail. **No chips.** |
| 9 | Send `"thanks bye"`                                  | Polite goodbye text. **No chips.**                                       |
| 10 | Pick "Employee", verify with `aaditya.jaiswal@smifs.com` / `BQPPJ8323M` | `auth_state` becomes `verified`; opener emits employee-tone chips. |
| 11 | Same NCD question as employee                       | Chips read: "Pull PSLR KYI Sheet", "Generate NCD sales pitch", "Check SEBI NCD guidelines" — visibly DIFFERENT from visitor chips in test #3. |
| 12 | Network kill (airplane mode) mid-stream             | Graceful error + retry CTA. Buffered fallback works on retry.            |
| 13 | Sign out → role gate reappears                      | Identity context cleared.                                                 |
| 14 | App resume after backgrounding                      | Chat scroll rehydrated from `/api/sessions/{id}`.                       |
| 15 | Locale switch via `locale_choice` block             | Next assistant reply in Hindi; form labels stay in English.              |

Acceptance threshold: 14/15 PASS for production release.

---

## 17. Repository / Pipeline

* **Mobile repos**: TBD — the customer to decide if iOS and Android live
  in one monorepo or separate.
* **Versioning**: `vMAJOR.MINOR.PATCH-PHASE` — e.g. `v1.0.0-phase29`. The
  PHASE field references the backend phase the binary was built against.
* **Source of truth for API contract**: this MD file. Any backend change
  that alters request / response / block schema MUST update this file in
  the same PR. Mobile dev should `git pull` this file before each
  release planning meeting.
* **API change cadence**: backend ships ~1-2 phases per month. Mobile
  should pin to a phase and test against staging before promoting.

---

## 18. Open Questions for the Backend Team

Please surface these to the backend team before starting mobile build:

1. **Fingerprint hashing scope** — does the backend want a `mobile/`
   subkey in `X-Client-Fingerprint` to distinguish app installs from web
   visits? Or is the existing single-namespace fingerprint enough?

2. **`client_platform` telemetry field** — should `/api/agent/turn/stream`
   accept a `client_platform` body field (`"ios"|"android"|"web"`) so
   trace / cost_ledger can segment? Currently web is the only client and
   nothing flags it.

3. **Push notification stack** — confirm § 14 schema. Specifically:
   - Does backend persist `device_token` per `session_id`?
   - What is the canonical sender (Firebase Cloud Messaging?)
   - Who provisions APNs certificates — backend team or app team?

4. **Deep links** — should `vehicle_cta.action="handoff_or_factsheet"`
   resolve to a backend-served PDF URL, a deep link into a future
   `/vehicle/{id}` route, or always trigger `handoff_request` for v1?

5. **Sign-out semantics** — does signing out wipe the session_id (mobile
   should rotate to a new UUID) or just clear `auth_state` (keep the
   UUID, re-pick role)? Current `/api/sessions/{id}/signout` clears
   auth state but preserves the session id; confirm that's the intended
   contract for mobile.

6. **Resume offer flow on mobile** — when an authenticated user starts a
   new session, should mobile auto-show the `resume_offer` block as a
   modal sheet, or render it inline like web? Inline matches web parity;
   modal is more mobile-native.

7. **Camera / file upload** — does any current form (e.g.
   `complaint_capture`, KYC update) need photo upload from the camera
   roll? None do today. If added later, define the multipart endpoint
   contract before mobile starts on it.

8. **Biometric unlock** — should returning verified users be allowed to
   biometric-unlock (FaceID / fingerprint) instead of re-typing PAN?
   Requires backend to issue a refresh token on first successful auth,
   which we don't have today. Roadmap decision.

9. **Locale auto-detect** — should mobile auto-call `POST /api/agent/locale`
   on first launch based on system locale (e.g. `hi-IN` → `locale=hi`),
   or always start in English and let the user pick via `locale_choice`?

10. **Rate-limit messaging** — backend returns 429 with a `blocks` field
    on rate limit. Should mobile render those blocks inline (current web
    behaviour) or show a system-banner? Web does inline.

---

## Appendix A. Full route catalogue (backend `server.py`, Phase 29)

For reference. Mobile uses a subset (chat, sessions, forms). Admin
endpoints listed for completeness.

```
GET    /api/                                          — service banner
GET    /api/health                                    — health probe
POST   /api/rag/search                                — internal RAG search (admin)
POST   /api/client_errors                             — mobile error telemetry
POST   /api/agent/turn                                — chat (buffered)
POST   /api/agent/turn/stream                         — chat (SSE)
POST   /api/leads                                     — generic lead capture (legacy)
POST   /api/forms/submit                              — dynamic-form submission
GET    /api/admin/forms/submissions                   — admin: list forms
POST   /api/admin/forms/{id}/retry                    — admin: retry email
GET    /api/admin/cost_ledger                         — admin: cost tracking
GET    /api/admin/insight/top_asks                    — admin: top tickers/products
GET    /api/admin/errors/recent                       — admin: error list
GET    /api/admin/errors/summary                      — admin: error rollup
POST   /api/admin/reembed/run                         — admin: re-embed job
GET    /api/admin/reembed/status/{job_id}             — admin: poll re-embed
GET    /api/admin/reembed/jobs                        — admin: list re-embed jobs
GET    /api/admin/reembed/estimate                    — admin: cost preview
POST   /api/handoff                                   — log RM hand-off
POST   /api/chat                                      — legacy chat (deprecated)
GET    /api/conversations/{session_id}                — legacy convo fetch
GET    /api/sessions/{session_id}                     — session state + history
POST   /api/sessions/{session_id}/signout             — sign out
POST   /api/sessions/{session_id}/select_role         — role gate
POST   /api/agent/locale                              — set locale
GET    /api/sessions/{session_id}/rehydration_candidates  — resume candidates
POST   /api/sessions/{session_id}/resume              — resume prior session
POST   /api/sessions/{session_id}/decline_resume      — decline resume
GET    /api/widget/config                             — embed widget config (web)
GET    /api/charts/{chart_id}.png                     — static chart PNGs
```

OpenAPI spec at `https://bot.pesmifs.com/api/docs`.

---

## Appendix B. Reference web frontend files

When in doubt about a block's schema or render rules, read the source:

```
frontend/src/pages/Chat.jsx                      — block dispatcher + SSE consumer
frontend/src/components/blocks/TextBlock.jsx
frontend/src/components/blocks/SuggestedActionsBlock.jsx
frontend/src/components/blocks/DynamicFormBlock.jsx
frontend/src/components/blocks/BmiaFundamentalsCard.jsx
frontend/src/components/blocks/LowConfidenceEscalation.jsx
frontend/src/components/blocks/LocaleChoiceBlock.jsx
frontend/src/components/blocks/VehicleCtaBlock.jsx
… (full list in section 5.13)
frontend/src/lib/fingerprint.js                  — X-Client-* headers contract
backend/agents/dynamic_forms.py                  — form schema builders
backend/agents/suggestion_agent.py               — chip generation + skip rules
backend/server.py                                — every endpoint
```

---

*End of spec. Last updated Phase 29 (2026-05-29). Maintained alongside the
Mackertich ONE backend in `/app/docs/MOBILE_APP_INTEGRATION.md`.*
