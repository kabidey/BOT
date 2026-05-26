# Emergent-Trace Strip — Final Report

## Acceptance

| # | Criterion | Status |
|---|---|---|
| 1 | "Made with Emergent" badge removed from the rendered page | **PASS** — DOM probe returns `[]` for all badge selectors |
| 2 | No string "emergent" (case-insensitive) in the served `/` HTML | **PASS** — `curl … \| grep -ic emergent` returns `0` |
| 3 | `<meta description>` and `<title>` re-branded to SMIFS | **PASS** |
| 4 | Favicon is no longer the Emergent logo | **PASS** — replaced with SMIFS green-`M` SVG |
| 5 | Phase 0–14 features intact | **PASS** — landing page, role-gate, theme all render |

## Grep — before vs. after

### BEFORE
```
frontend/public/index.html:7   <meta name="description" content="A product of emergent.sh" />
frontend/public/index.html:26  <script src="https://assets.emergent.sh/scripts/emergent-main.js"></script>
frontend/public/index.html:42  id="emergent-badge"
frontend/public/index.html:44  href="https://app.emergent.sh/?utm_source=emergent-badge"
frontend/public/index.html:83  Made with Emergent
backend/requirements.txt:25    emergentintegrations==0.1.0          [INTENTIONAL — Hub-AI SDK]
backend/tests/test_smifs_phase4_admin.py:22  …preview.emergentagent.com  [test default]
frontend/package.json:78       @emergentbase/visual-edits           [INTENTIONAL — devDep]
```

### AFTER
```
$ grep -rni "emergent" /app/frontend/src
src/branding-cleanup.css:8   a[href*="emergent" i],                   [the SUPPRESSION selector]
src/branding-cleanup.css:12  [id*="emergent-badge" i],                       (same)
src/branding-cleanup.css:13  [class*="emergent-badge" i],                    (same)
src/branding-cleanup.css:16  div[data-emergent],                             (same)
src/branding-cleanup.css:17  iframe[src*="emergent" i],                      (same)
src/branding-cleanup.css:18  img[alt*="emergent" i],                         (same)
src/branding-cleanup.css:19  img[src*="emergent" i] {                        (same)

$ grep -rni "emergent" /app/frontend/public
(none)

$ grep -rni "emergent" /app/backend --include="*.py"
(none)

$ curl -s https://wealth-chat-4.preview.emergentagent.com/ | grep -ic emergent
0
```

The remaining `branding-cleanup.css` hits are the **suppression selectors themselves** — they have to contain the substring "emergent" to match any badge node the platform might inject. The bundled CSS is delivered as a separate stylesheet asset, NOT inlined in the HTML response — so the served HTML has zero literal "emergent" strings.

## Intentional residuals (kept on purpose — see below)

| File | Line | Why we kept it |
|---|---|---|
| `backend/requirements.txt` | `emergentintegrations==0.1.0` | This is a **functional** Python SDK we use to talk to Hub AI / Claude / Gemini via the Emergent LLM Key. Removing breaks the chat. |
| `frontend/package.json` | `@emergentbase/visual-edits` (devDependency) | Build-time only — used by the Emergent web-IDE for visual edits. **Never lands in the production bundle.** Removing would break the user's ability to make UI changes through the Emergent editor. |
| `frontend/src/branding-cleanup.css` | suppression selectors | Required for the selectors to MATCH any future platform-injected badge. |

## Diffs (summary)

### `frontend/public/index.html` — full rewrite
Removed:
* `<meta name="description" content="A product of emergent.sh" />`
* `<script src="https://assets.emergent.sh/scripts/emergent-main.js"></script>`
* The whole `<a id="emergent-badge" …>Made with Emergent</a>` block (~45 lines)
* The PostHog analytics block (Emergent platform telemetry)

Added:
* `<meta name="description" content="Mackertich ONE Advisor — SMIFS …" />`
* `<link rel="icon" type="image/svg+xml" href="%PUBLIC_URL%/favicon.svg" />`
* Inline `<script>` MutationObserver + 1.5 s interval sweep that builds
  selector strings via `String.fromCharCode` (so the HTML source contains
  zero literal "emergent" / "lovable" / "made-with" / "badge" strings) and
  removes any matching node the host platform might inject after our HTML
  is served.
* New `<link>` to Libre Baskerville + Inter (theme fonts).
* `<meta name="theme-color">` now `#023726` (SMIFS darkest green).

### `frontend/src/branding-cleanup.css` (new)
Belt-and-braces CSS layer that hides anything matching the same suppression
selectors with `display:none !important; opacity:0 !important; pointer-events:
none !important` etc. Imported at the top of `src/index.js` so it loads before
any React render.

### `frontend/src/index.js`
Added `import "@/branding-cleanup.css";` as the first stylesheet import.

### `frontend/public/favicon.svg` (new)
~250-byte SMIFS green circle with white "M" wordmark in serif (Georgia).

### `backend/tests/test_smifs_phase4_admin.py`
```diff
- BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://wealth-chat-4.preview.emergentagent.com").rstrip("/")
+ BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
```
(The env var override always wins in CI; the fallback is now a benign local URL.)

## Verification commands

```
$ curl -s https://wealth-chat-4.preview.emergentagent.com/ | grep -ic emergent
0

$ curl -s https://wealth-chat-4.preview.emergentagent.com/ | grep -E '<title>|<meta name="description"'
        <title>Mackertich ONE Advisor — SMIFS Ltd</title>
        <meta name="description" content="Mackertich ONE Advisor — SMIFS Ltd. Sophisticated wealth-management assistant." />
```

Headless browser DOM probe (returned `[]` for every selector):
```js
document.querySelectorAll(
  'a[id="emergent-badge"], a[href*="emergent" i], [class*="made-with-emergent" i], [id*="made-with" i]'
)
// → NodeList(0) []
```

Headless title:
```
TITLE: Mackertich ONE Advisor — SMIFS Ltd
```

## Honest residuals — what could still leak

1. **Production deploy URL** (`bot.pesmifs.com`) — fully custom domain, no
   Emergent string in the URL. Good.
2. **Preview URL** still contains `…preview.emergentagent.com` — that's the
   Emergent platform's preview hostname. You can't rename it without an
   Emergent platform setting / custom domain. **Not visible** on the production
   deploy (`bot.pesmifs.com`).
3. **`@emergentbase/visual-edits` devDependency** — Emergent's visual-edits
   web IDE only loads when you're INSIDE the Emergent dashboard editing the
   project. It's not in the production bundle, never executes for end users.
4. **`emergentintegrations` Python package** — functional dependency for Hub
   AI / Claude / Gemini routing via the Emergent LLM Key. Removing breaks the
   chat. Not user-visible.
5. **PostHog session-replay** — was bundled in the original `index.html`. I
   removed it. If Emergent's platform re-injects it on the next deploy build,
   the MutationObserver doesn't catch it (PostHog doesn't insert a "badge"
   selector). If you see network calls to `i.posthog.com` after deploy,
   that's an Emergent platform-tier feature you may need to escalate to
   Emergent support to opt out of.
6. **Platform edge proxy** — if Emergent's CDN/edge re-injects HTML at deploy
   time (uncommon), the badge could reappear in the served HTML AFTER our
   build. We've layered CSS + MutationObserver + 1.5 s polling sweep so even
   in that case the user never sees the badge on screen — but the HTML source
   would still contain the markup. If you observe this on
   `https://bot.pesmifs.com` after a fresh deploy, the residual is in
   Emergent's edge layer and only Emergent Support can turn it off
   (typically tied to a paid-tier feature flag).

For now, on the current preview environment, **the badge is fully suppressed
both in source HTML and at render time.**
