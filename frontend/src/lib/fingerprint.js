/**
 * Phase 22 — Silent device fingerprinting for fraud detection.
 *
 * Generates a stable visitorId via FingerprintJS, caches it in localStorage
 * under `_smifs_dvc`, and installs a global axios default header so every
 * `/api/*` request automatically carries:
 *
 *   X-Client-Fingerprint  · the stable visitorId
 *   X-Client-Tz           · resolved IANA timezone (e.g. "Asia/Kolkata")
 *   X-Client-Screen       · "W×H@DPR" string for resolution variance signal
 *
 * The module is intentionally silent: no UI prompts, no banners, no toasts.
 * Failures fall through gracefully — the worst case is one request lands
 * without a fingerprint header (the backend middleware tolerates that
 * because pre-Phase-22 clients have none either).
 *
 * Calling `bootstrapFingerprint()` is idempotent: the first call loads the
 * FingerprintJS agent, every subsequent call short-circuits.
 */
import axios from "axios";
import FingerprintJS from "@fingerprintjs/fingerprintjs";

const STORAGE_KEY = "_smifs_dvc";
const STORAGE_VERSION = "v1";

let bootstrapPromise = null;

function readCachedVisitorId() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.v !== STORAGE_VERSION) return null;
    if (!parsed.id || typeof parsed.id !== "string") return null;
    return parsed.id;
  } catch (_) {
    return null;
  }
}

function writeCachedVisitorId(id) {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ v: STORAGE_VERSION, id, at: Date.now() }),
    );
  } catch (_) { /* private mode / quota — ignore */ }
}

function resolveTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch (_) {
    return "";
  }
}

function resolveScreenSignature() {
  try {
    const w = window.screen?.width || 0;
    const h = window.screen?.height || 0;
    const dpr = window.devicePixelRatio || 1;
    return `${w}x${h}@${Math.round(dpr * 100) / 100}`;
  } catch (_) {
    return "";
  }
}

/**
 * Install request headers on `axios.defaults`. Both direct `axios.get()`
 * calls and `axios.create()` instances inherit `defaults.headers.common`
 * unless they explicitly override — which we don't anywhere in the app.
 */
function applyHeaders(fingerprint) {
  if (!fingerprint) return;
  const tz = resolveTimezone();
  const screen = resolveScreenSignature();
  // `common` applies to every HTTP verb.
  axios.defaults.headers.common["X-Client-Fingerprint"] = fingerprint;
  if (tz) axios.defaults.headers.common["X-Client-Tz"] = tz;
  if (screen) axios.defaults.headers.common["X-Client-Screen"] = screen;
}

/**
 * Bootstrap (lazy + idempotent). Returns the fingerprint once available.
 * Safe to call from `index.js` at app start AND from any component.
 */
export function bootstrapFingerprint() {
  if (bootstrapPromise) return bootstrapPromise;

  const cached = readCachedVisitorId();
  if (cached) {
    applyHeaders(cached);
    bootstrapPromise = Promise.resolve(cached);
    // Refresh in the background in case the device signal has drifted.
    refreshInBackground();
    return bootstrapPromise;
  }

  bootstrapPromise = (async () => {
    try {
      const agent = await FingerprintJS.load({ monitoring: false });
      const result = await agent.get();
      const id = result?.visitorId || "";
      if (id) {
        writeCachedVisitorId(id);
        applyHeaders(id);
      }
      return id;
    } catch (_) {
      return "";
    }
  })();
  return bootstrapPromise;
}

async function refreshInBackground() {
  try {
    const agent = await FingerprintJS.load({ monitoring: false });
    const result = await agent.get();
    const id = result?.visitorId || "";
    if (id) {
      const cached = readCachedVisitorId();
      if (cached !== id) {
        writeCachedVisitorId(id);
        applyHeaders(id);
      }
    }
  } catch (_) { /* swallow */ }
}

/** Test helper — read the active fingerprint from cache without re-running. */
export function getCachedFingerprint() {
  return readCachedVisitorId();
}
