# SMIFS.com — Visual Identity Probe (Phase 14)

Source: `curl -sL https://smifs.com/` (HTML inspected 2026-02 — see raw probe in
`deliverables/phase14/smifs_html_excerpt.txt`).

## Color palette (extracted from inline SVG fill attributes, Tailwind utility
classes and inline style="background:..." declarations)

| Token | Hex | Where it appears on smifs.com |
|---|---|---|
| **`primary-500`** (brand green) | `#098C62` | All primary CTAs, link colour, FAQ bullets, contact icons, "lightgreen-500" utility |
| **`primary-600`** (hover) | `#077A55` (derived 1 step darker) | Button hover |
| **`primary-50`** (card tint) | `#E8F5EF` | Stat cards `bg-primary-50` |
| **`secondary-800`** (deep green) | `#065B40` | Result-update cards, dark feature panels |
| **`secondary-900`** (darkest CTA) | `#023726` | "Open Account" CTA in the footer banner |
| **`accent-darkgreen`** | `#0C3A2B` | Mobile result card overlay |
| **`ink-900`** (text) | `#191A15` | All long-form copy, hero headline |
| **`ink-600`** (muted) | `#808080` | Testimonial subhead |
| **`canvas`** (background) | `#FFFFFF` / `#F9F9F9` | Page bg / FAQ card fill |
| **`hairline`** | `#D9D9D9` | Borders on research cards |
| **`overlay`** | `rgba(0,0,0,0.6)` | Service card glass overlay |

## Typography

| Slot | Family | Notes |
|---|---|---|
| **Headlines / Brand wordmark** | `Libre Baskerville` | Used as `font-libre-baskerville` for H1, H2, the brand "trust" wordmark. Serif. |
| **Body copy / UI** | `Helvetica Neue` (`font-helvetica-neue`) | All paragraphs, form labels, buttons, captions |
| Italic accent | `Libre Baskerville` italic | Hero strap-line ("Guided by research.") |

Both available via Google Fonts (Libre Baskerville) + system fallback (Helvetica
Neue → Inter → sans-serif).

## Applied to Mackertich ONE Advisor

The Phase 5 widget defaults are now driven by these tokens. We keep the dark-
panel chat look (verified-card density needs contrast) but switch the brand
spine from **navy + gold** to **deep-green + emerald accent** so the bot reads
as an SMIFS asset.

| Bot token | Was (Phase 5) | Now (Phase 14) |
|---|---|---|
| `theme.primary` | `#0B1B2B` (navy) | `#065B40` (smifs deep green) |
| `theme.accent` | `#C9A86A` (gold) | `#098C62` (smifs primary green) |
| `theme.user_bubble` | `#0B1B2B` | `#065B40` |
| `theme.assistant_bubble` | `#F4F1EA` | `#F1F5F2` (very faint green tint) |
| `theme.header_bg` | `#0B1B2B` | `#023726` (smifs darkest CTA) |
| `theme.header_text` | `#FFFFFF` | `#FFFFFF` |
| `theme.text` | `#0B1B2B` | `#191A15` (smifs ink) |
| Headline font | `Cormorant Garamond` | `Libre Baskerville` |
| Body font | `Manrope` | `Helvetica Neue` → Inter fallback |

The CSS variables in `frontend/src/App.css` are renamed (`--navy-*` → `--smifs-*`,
`--gold` → `--accent-green`) and the radial-gradient shell tints adjusted to
match. The role-gate buttons, send-arrow, citations underline and the admin
top-nav active marker all flow from these tokens — no per-component overrides.
