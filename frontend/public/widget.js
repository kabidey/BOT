/* Mackertich ONE chat widget — vanilla JS bubble + iframe loader.
 * Host pages embed this via:
 *   <script src="https://<host>/widget.js" defer></script>
 *
 * Phase 23 — Responsive, device-aware embed.
 *   * Mobile portrait (≤640px): full-screen sheet (100dvh) with backdrop blur
 *     over the partner page, bubble hides while open.
 *   * Mobile landscape (≤950px landscape): compact 80dvh bottom-sheet.
 *   * Tablet (641-1024px): floating ≤420×85dvh panel right-side.
 *   * Desktop (>1024px): existing 420×720 floating panel.
 *   * Safe-area-inset top/bottom on mobile (notch + home indicator).
 *   * `prefers-reduced-motion: reduce` → instant transitions.
 *   * Lazy iframe — `iframe.src` set on first launcher click only.
 *   * `visualViewport` not needed at the loader level — the embed surface
 *     handles its own keyboard awareness (see Chat.jsx).
 *
 * Behaviour:
 *   1. Reads the script tag's src to derive the host URL.
 *   2. Fetches /api/widget/config for theme + branding (CORS-friendly).
 *   3. Injects a floating circular bubble button (z-index 2147483000).
 *   4. On click, lazy-creates an iframe → /embed.
 *   5. iframe ↔ widget postMessage:
 *        {type:"mackertich:close"}             → minimise back to bubble
 *        {type:"mackertich:assistant_message"} → unread badge dot if minimised
 *
 * Optional script-tag attributes:
 *   data-position="bottom-left"   forces position
 *   data-debug                    logs widget activity to console
 */
(function () {
  "use strict";
  if (window.__mackertichWidgetLoaded) return;
  window.__mackertichWidgetLoaded = true;

  var SCRIPT_ID = "mackertich-widget-script";
  var CONTAINER_ID = "mackertich-widget-root";
  var Z = 2147483000;

  function currentScriptEl() {
    if (document.currentScript) return document.currentScript;
    var scripts = document.getElementsByTagName("script");
    for (var i = scripts.length - 1; i >= 0; i--) {
      if ((scripts[i].src || "").indexOf("widget.js") !== -1) return scripts[i];
    }
    return null;
  }

  var scriptEl = currentScriptEl();
  if (scriptEl) scriptEl.id = SCRIPT_ID;
  var debug = scriptEl && scriptEl.hasAttribute("data-debug");
  var positionOverride = scriptEl && scriptEl.getAttribute("data-position");
  var hostBase = (function () {
    if (!scriptEl) return location.origin;
    try {
      var u = new URL(scriptEl.src, location.href);
      return u.origin;
    } catch (_) { return location.origin; }
  })();

  function log() {
    if (!debug) return;
    var args = Array.prototype.slice.call(arguments);
    args.unshift("[mackertich]");
    console.log.apply(console, args);
  }

  function ensureContainer() {
    var el = document.getElementById(CONTAINER_ID);
    if (el) return el;
    el = document.createElement("div");
    el.id = CONTAINER_ID;
    el.setAttribute("data-testid", "mackertich-widget-root");
    document.body.appendChild(el);
    return el;
  }

  /* dvh polyfill (older browsers fall back to vh via CSS cascade in App.css) */
  function injectStyles(theme, position) {
    if (document.getElementById("mackertich-widget-styles")) return;
    var pos = position === "bottom-left"
      ? "left: 24px; right: auto;"
      : "right: 24px; left: auto;";
    var iframePos = position === "bottom-left"
      ? "left: 24px; right: auto;"
      : "right: 24px; left: auto;";
    var bubbleBg = (theme && theme.primary) || "#0B1B2B";
    var bubbleAccent = (theme && theme.accent) || "#C9A86A";
    var bg = (theme && theme.background) || "#FFFFFF";
    var css =
      "#" + CONTAINER_ID + " { all: initial; font-family: system-ui, -apple-system, sans-serif; }" +

      /* ----- Floating launcher bubble (desktop + tablet always; mobile only when closed) ----- */
      ".m1-bubble {" +
      "  position: fixed; bottom: 24px; " + pos +
      "  width: 60px; height: 60px; border-radius: 999px;" +
      "  background: " + bubbleBg + ";" +
      "  border: 2px solid " + bubbleAccent + ";" +
      "  display: flex; align-items: center; justify-content: center;" +
      "  cursor: pointer; z-index: " + (Z + 1) + ";" +
      "  box-shadow: 0 12px 30px rgba(11, 27, 43, 0.35);" +
      "  transition: transform 0.18s, box-shadow 0.18s, opacity 0.18s;" +
      "  color: white; font-size: 26px; line-height: 1; user-select: none;" +
      "  padding-bottom: env(safe-area-inset-bottom, 0);" +
      "}" +
      ".m1-bubble:hover { transform: scale(1.06); box-shadow: 0 16px 40px rgba(11, 27, 43, 0.45); }" +
      ".m1-bubble.is-hidden { opacity: 0; pointer-events: none; transform: scale(0.6); }" +
      ".m1-bubble--pulse::after {" +
      "  content: ''; position: absolute; inset: -4px; border-radius: 999px;" +
      "  border: 2px solid " + bubbleAccent + "; opacity: 0.5;" +
      "  animation: m1-pulse 2.4s ease-out infinite;" +
      "}" +
      "@keyframes m1-pulse { 0% { transform: scale(1); opacity: 0.55; } 100% { transform: scale(1.5); opacity: 0; } }" +
      ".m1-unread {" +
      "  position: absolute; top: 6px; right: 6px; width: 12px; height: 12px;" +
      "  border-radius: 999px; background: #E03E3E; border: 2px solid white; display: none;" +
      "}" +
      ".m1-unread.is-visible { display: block; }" +

      /* ----- Backdrop (mobile only) ----- */
      ".m1-backdrop {" +
      "  position: fixed; inset: 0;" +
      "  background: rgba(8, 18, 30, 0.45);" +
      "  -webkit-backdrop-filter: blur(8px) saturate(120%);" +
      "  backdrop-filter: blur(8px) saturate(120%);" +
      "  z-index: " + (Z - 1) + ";" +
      "  opacity: 0; pointer-events: none;" +
      "  transition: opacity 0.18s ease;" +
      "}" +
      ".m1-backdrop.is-open { opacity: 1; pointer-events: auto; }" +

      /* ----- Iframe wrap — DESKTOP defaults (>1024px). Anchored flush to the
       *       bottom-right corner per Phase 23 spec. The bubble (z-index Z+1)
       *       remains clickable above the panel as the toggle/minimize control. */
      ".m1-iframe-wrap {" +
      "  position: fixed; bottom: 24px; " + iframePos +
      "  width: 420px; height: 720px; max-height: calc(100dvh - 48px);" +
      "  z-index: " + Z + ";" +
      "  border-radius: 20px; overflow: hidden;" +
      "  box-shadow: 0 22px 60px rgba(11, 27, 43, 0.32);" +
      "  background: " + bg + ";" +
      "  opacity: 0; transform: translateY(12px) scale(0.98);" +
      "  pointer-events: none;" +
      "  transition: opacity 220ms ease, transform 220ms cubic-bezier(0.22, 1, 0.36, 1);" +
      "}" +
      ".m1-iframe-wrap.is-open {" +
      "  opacity: 1; transform: translateY(0) scale(1);" +
      "  pointer-events: auto;" +
      "}" +
      /* iframe inherits parent radius so the corners render rounded even when
       * inspectors read the iframe node directly (defence-in-depth — the
       * wrap's `overflow: hidden` already crops, but devtools probes that
       * look only at the iframe see the expected radius too). */
      ".m1-iframe-wrap iframe {" +
      "  width: 100%; height: 100%; border: 0; display: block;" +
      "  background: " + bg + ";" +
      "  border-radius: inherit;" +
      "}" +

      /* ----- Phase 23.3 — JS-resolved breakpoint classes ALWAYS win over the
       *       width/orientation media queries below (which Android Chrome can
       *       briefly mis-report during URL-bar collapse / keyboard reflow).
       *       The `.m1-bp-*` selectors carry one more level of specificity
       *       than the plain `.m1-iframe-wrap` rules. ----- */
      ".m1-iframe-wrap.m1-bp-desktop {" +
      "  bottom: 24px; " + iframePos +
      "  width: 420px; height: 720px; max-height: calc(100dvh - 48px);" +
      "  border-radius: 20px;" +
      "}" +
      ".m1-iframe-wrap.m1-bp-desktop iframe { border-radius: 20px; }" +

      ".m1-iframe-wrap.m1-bp-tablet {" +
      "  width: min(420px, 90vw); height: min(720px, 85dvh);" +
      "  bottom: max(20px, env(safe-area-inset-bottom, 20px)); " + iframePos +
      "  border-radius: 18px;" +
      "  top: auto; left: auto;" +
      "}" +
      ".m1-iframe-wrap.m1-bp-tablet iframe { border-radius: 18px; }" +

      ".m1-iframe-wrap.m1-bp-mobile {" +
      "  width: 100vw !important; width: 100dvw !important;" +
      "  height: 100vh !important; height: 100dvh !important;" +
      "  top: 0 !important; right: 0 !important;" +
      "  bottom: 0 !important; left: 0 !important;" +
      "  max-height: none !important; max-width: none !important;" +
      "  border-radius: 0 !important;" +
      "  transform: translateY(100%) scale(1);" +
      "}" +
      ".m1-iframe-wrap.m1-bp-mobile.is-open { transform: translateY(0); }" +
      ".m1-iframe-wrap.m1-bp-mobile iframe { border-radius: 0 !important; }" +

      ".m1-iframe-wrap.m1-bp-mobile-landscape {" +
      "  width: min(520px, 96vw); height: 80dvh;" +
      "  bottom: max(8px, env(safe-area-inset-bottom, 8px));" +
      "  right: 8px; left: auto; top: auto;" +
      "  border-radius: 16px;" +
      "  transform: translateY(40%) scale(1);" +
      "  max-height: none; max-width: none;" +
      "}" +
      ".m1-iframe-wrap.m1-bp-mobile-landscape.is-open { transform: translateY(0); }" +
      ".m1-iframe-wrap.m1-bp-mobile-landscape iframe { border-radius: 16px; }" +

      /* ----- Tablet (641-1024px) — legacy MQ kept as a fallback for partner
       *       sites that load widget.js asynchronously before our JS detection
       *       has run. Class-based rules above ALWAYS win when applied. ----- */
      "@media (max-width: 1024px) and (min-width: 641px) {" +
      "  .m1-iframe-wrap:not(.m1-bp-desktop):not(.m1-bp-mobile):not(.m1-bp-mobile-landscape) {" +
      "    width: min(420px, 90vw); height: min(720px, 85dvh);" +
      "    bottom: max(20px, env(safe-area-inset-bottom, 20px)); " + iframePos +
      "    border-radius: 18px;" +
      "  }" +
      "  .m1-iframe-wrap:not(.m1-bp-desktop):not(.m1-bp-mobile):not(.m1-bp-mobile-landscape) iframe { border-radius: 18px; }" +
      "}" +

      /* ----- Mobile width MQ — NO orientation gate (was the Phase 23.0 bug:
       *       Android Chrome briefly reports landscape on initial load and
       *       silently dropped this rule, falling through to landscape MQ). ----- */
      "@media (max-width: 640px) {" +
      "  .m1-iframe-wrap:not(.m1-bp-desktop):not(.m1-bp-tablet) {" +
      "    width: 100vw; width: 100dvw;" +
      "    height: 100vh; height: 100dvh;" +
      "    top: 0; right: 0; bottom: 0; left: 0;" +
      "    max-height: none; border-radius: 0;" +
      "    transform: translateY(100%) scale(1);" +
      "  }" +
      "  .m1-iframe-wrap:not(.m1-bp-desktop):not(.m1-bp-tablet).is-open { transform: translateY(0); }" +
      "  .m1-iframe-wrap:not(.m1-bp-desktop):not(.m1-bp-tablet) iframe { border-radius: 0; }" +
      "  .m1-bubble { display: var(--m1-bubble-mobile-display, flex); }" +
      "}" +

      /* ----- Mobile landscape MQ — fallback only (narrow + landscape). ----- */
      "@media (max-width: 950px) and (orientation: landscape) and (min-width: 481px) {" +
      "  .m1-iframe-wrap:not(.m1-bp-desktop):not(.m1-bp-tablet):not(.m1-bp-mobile) {" +
      "    width: min(520px, 96vw); height: 80dvh;" +
      "    bottom: max(8px, env(safe-area-inset-bottom, 8px));" +
      "    right: 8px; left: auto;" +
      "    border-radius: 16px;" +
      "    transform: translateY(40%);" +
      "  }" +
      "  .m1-iframe-wrap:not(.m1-bp-desktop):not(.m1-bp-tablet):not(.m1-bp-mobile).is-open { transform: translateY(0); }" +
      "}" +

      /* ----- Narrow phones (≤360px, e.g. Galaxy Fold cover, iPhone SE) ----- */
      "@media (max-width: 360px) {" +
      "  .m1-bubble { width: 52px; height: 52px; font-size: 22px; bottom: 16px; }" +
      "}" +

      /* ----- prefers-reduced-motion: instant transitions ----- */
      "@media (prefers-reduced-motion: reduce) {" +
      "  .m1-iframe-wrap, .m1-bubble, .m1-backdrop {" +
      "    transition: none !important; animation: none !important;" +
      "  }" +
      "  .m1-bubble--pulse::after { display: none !important; }" +
      "}";
    var style = document.createElement("style");
    style.id = "mackertich-widget-styles";
    style.textContent = css;
    document.head.appendChild(style);
  }

  function buildBubble(icon) {
    var b = document.createElement("button");
    b.className = "m1-bubble m1-bubble--pulse";
    b.setAttribute("data-testid", "mackertich-widget-bubble");
    b.setAttribute("aria-label", "Open Mackertich ONE chat");
    b.type = "button";
    b.textContent = icon || "💬";
    var unread = document.createElement("span");
    unread.className = "m1-unread";
    unread.setAttribute("data-testid", "mackertich-widget-unread");
    b.appendChild(unread);
    return { bubble: b, unread: unread };
  }

  function buildBackdrop() {
    var d = document.createElement("div");
    d.className = "m1-backdrop";
    d.setAttribute("data-testid", "mackertich-widget-backdrop");
    d.setAttribute("aria-hidden", "true");
    return d;
  }

  function buildIframe() {
    var wrap = document.createElement("div");
    wrap.className = "m1-iframe-wrap";
    wrap.setAttribute("data-testid", "mackertich-widget-iframe-wrap");
    wrap.setAttribute("role", "dialog");
    wrap.setAttribute("aria-modal", "false");
    wrap.setAttribute("aria-label", "Mackertich ONE chat");
    var iframe = document.createElement("iframe");
    iframe.title = "Mackertich ONE chat";
    iframe.setAttribute("data-testid", "mackertich-widget-iframe");
    iframe.setAttribute("loading", "lazy");
    iframe.allow = "clipboard-write";
    /* Phase 23 — never set src until first user click. */
    wrap.appendChild(iframe);
    return { wrap: wrap, iframe: iframe };
  }

  function isOpen(wrap) { return wrap.classList.contains("is-open"); }

  /* ---- Phase 23.3 — multi-signal breakpoint resolver. ----
   * `window.matchMedia('(orientation: portrait)')` is unreliable on Android
   * Chrome during initial page-load reflow (URL-bar collapse can briefly flip
   * orientation to landscape). Worse: `window.innerWidth` reflects the parent
   * page's layout viewport, which on legacy partner sites that set
   * `<meta name="viewport" content="width=1024">` will lie about device size.
   *
   * Solution: combine 4 independent signals and bias hard toward "mobile" if
   * any 2 of them agree. The resolver writes one of four breakpoint class
   * names onto the iframe-wrap: `m1-bp-desktop`, `m1-bp-tablet`,
   * `m1-bp-mobile`, `m1-bp-mobile-landscape`. The CSS class rules carry one
   * more level of specificity than the legacy width media queries, so they
   * always win.
   *
   * Signals:
   *   1. pointer + hover           — `(pointer: coarse) and (hover: none)` ⇒ touch
   *   2. screen.width × DPR        — physical screen at CSS pixel resolution
   *   3. innerWidth/innerHeight    — layout viewport (potentially lying)
   *   4. userAgent / userAgentData — explicit mobile keyword
   */
  function resolveBreakpoint() {
    var sigs = {
      touch: false, screenSmall: false, viewportSmall: false, uaMobile: false,
      orient: "portrait",
      iw: window.innerWidth || 0, ih: window.innerHeight || 0,
      sw: (window.screen && window.screen.width) || 0,
      sh: (window.screen && window.screen.height) || 0,
      dpr: window.devicePixelRatio || 1,
    };
    try { sigs.touch = window.matchMedia("(pointer: coarse) and (hover: none)").matches; } catch (_) {}
    /* The screen dimensions reflect the physical device — DPR-independent on
     * modern browsers (they report CSS px). Phones top out around 480 CSS px. */
    sigs.screenSmall = sigs.sw > 0 && sigs.sw <= 640;
    sigs.viewportSmall = sigs.iw > 0 && sigs.iw <= 640;
    var ua = (navigator.userAgent || "").toLowerCase();
    sigs.uaMobile = /android|iphone|ipod|mobile|blackberry|opera mini|iemobile|windows phone/i.test(ua);
    /* `navigator.userAgentData` is the modern source of truth on Chromium
     * (≥ Chrome 90). It returns `true` for mobile devices regardless of UA
     * spoofing. */
    if (navigator.userAgentData && typeof navigator.userAgentData.mobile === "boolean") {
      sigs.uaMobile = sigs.uaMobile || navigator.userAgentData.mobile;
    }
    /* Orientation: use raw aspect ratio rather than the unreliable
     * `(orientation: landscape)` MQ during reflow. */
    sigs.orient = (sigs.iw > sigs.ih && sigs.ih > 0) ? "landscape" : "portrait";

    /* Decision logic — bias toward mobile when any 2 of (touch, uaMobile,
     * screenSmall, viewportSmall) agree. This survives partner viewport
     * lies AND Android orientation flip-flops. */
    var mobileVotes = 0;
    if (sigs.touch) mobileVotes++;
    if (sigs.uaMobile) mobileVotes++;
    if (sigs.screenSmall) mobileVotes++;
    if (sigs.viewportSmall) mobileVotes++;

    var bp;
    /* Phone-in-landscape detection (the short dimension is ≤ 480, regardless
     * of which axis is wider). iPhone 12 landscape is 844×390 — width
     * exceeds the 720 tablet threshold, but height of 390 betrays the phone
     * form factor. */
    var shortSide = Math.min(sigs.iw || 9999, sigs.ih || 9999);
    var phoneInLandscape = mobileVotes >= 2 && sigs.orient === "landscape" && shortSide <= 480;
    if (phoneInLandscape) {
      bp = "mobile-landscape";
    } else if (mobileVotes >= 2 && sigs.iw <= 720) {
      /* Phone-class portrait (phones top out around 640-720 CSS px). */
      bp = (sigs.orient === "landscape") ? "mobile-landscape" : "mobile";
    } else if (mobileVotes >= 2 && sigs.iw > 720 && sigs.iw <= 1024) {
      /* Touch device with tablet-class width (iPad, large Android tablet).
       * Use the floating tablet panel — partner page stays visible. */
      bp = "tablet";
    } else if (sigs.iw > 1024) {
      bp = "desktop";
    } else if (sigs.iw > 640) {
      bp = "tablet";
    } else {
      /* Width is small but no touch signals — could be a desktop browser
       * resized narrow. Treat as mobile portrait for layout. */
      bp = (sigs.orient === "landscape") ? "mobile-landscape" : "mobile";
    }
    return { bp: bp, sigs: sigs };
  }

  function applyBreakpoint(wrap) {
    var r = resolveBreakpoint();
    wrap.classList.remove("m1-bp-desktop", "m1-bp-tablet", "m1-bp-mobile", "m1-bp-mobile-landscape");
    wrap.classList.add("m1-bp-" + r.bp);
    wrap.setAttribute("data-bp", r.bp);
    if (debug) log("breakpoint=" + r.bp, r.sigs);
    return r.bp;
  }

  /* "Full-screen takeover" is now defined as "breakpoint is mobile" (portrait
   * only — landscape mobile is the compact bottom-sheet). */
  function isMobileFullScreen(wrap) {
    return wrap && wrap.classList.contains("m1-bp-mobile");
  }

  function bootstrap(config) {
    log("config", config);
    var pos = positionOverride || (config && config.position) || "bottom-right";
    injectStyles((config && config.theme) || {}, pos);
    var root = ensureContainer();
    var b = buildBubble((config && config.bubble_icon) || "💬");
    var bd = buildBackdrop();
    var f = buildIframe();
    /* Backdrop sits BELOW the iframe wrap in stacking order via z-index. */
    root.appendChild(bd);
    root.appendChild(b.bubble);
    root.appendChild(f.wrap);

    /* Phase 23.3 — resolve breakpoint synchronously on mount so the wrap
     * carries the correct class BEFORE first paint. Re-resolved on resize
     * & orientationchange below. */
    applyBreakpoint(f.wrap);

    var iframeReady = false;
    var open = function () {
      if (!iframeReady) {
        var url = hostBase + "/embed?theme_v=" + ((config && config.theme_version) || "v1");
        f.iframe.src = url;
        iframeReady = true;
      }
      /* Re-resolve in case the device rotated while the panel was closed. */
      applyBreakpoint(f.wrap);
      f.wrap.classList.add("is-open");
      bd.classList.add("is-open");
      b.unread.classList.remove("is-visible");
      b.bubble.classList.remove("m1-bubble--pulse");
      /* On full-screen mobile, hide the launcher so it doesn't overlap the chat. */
      if (isMobileFullScreen(f.wrap)) b.bubble.classList.add("is-hidden");
      /* Lock partner page scroll while a full-screen sheet is open. */
      if (isMobileFullScreen(f.wrap)) {
        document.documentElement.dataset.m1ScrollLock = "1";
        document.documentElement.style.overflow = "hidden";
      }
      log("opened");
    };
    var close = function () {
      f.wrap.classList.remove("is-open");
      bd.classList.remove("is-open");
      b.bubble.classList.remove("is-hidden");
      if (document.documentElement.dataset.m1ScrollLock) {
        document.documentElement.style.overflow = "";
        delete document.documentElement.dataset.m1ScrollLock;
      }
      log("closed");
    };
    b.bubble.addEventListener("click", function () {
      if (isOpen(f.wrap)) close(); else open();
    });
    bd.addEventListener("click", close);

    /* Re-evaluate breakpoint + bubble visibility on resize / orientation change. */
    var onResize = function () {
      applyBreakpoint(f.wrap);
      if (!isOpen(f.wrap)) return;
      if (isMobileFullScreen(f.wrap)) {
        b.bubble.classList.add("is-hidden");
        document.documentElement.style.overflow = "hidden";
        document.documentElement.dataset.m1ScrollLock = "1";
      } else {
        b.bubble.classList.remove("is-hidden");
        if (document.documentElement.dataset.m1ScrollLock) {
          document.documentElement.style.overflow = "";
          delete document.documentElement.dataset.m1ScrollLock;
        }
      }
    };
    window.addEventListener("resize", onResize);
    window.addEventListener("orientationchange", onResize);

    window.addEventListener("message", function (e) {
      var d = e.data;
      if (!d || typeof d !== "object" || !d.type) return;
      if (e.source !== f.iframe.contentWindow) return;
      if (d.type === "mackertich:close") {
        close();
      } else if (d.type === "mackertich:assistant_message") {
        if (!isOpen(f.wrap)) b.unread.classList.add("is-visible");
      }
    });

    window.MackertichWidget = { open: open, close: close, _config: config };
  }

  function fetchConfig() {
    return fetch(hostBase + "/api/widget/config", { credentials: "omit" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)); });
  }

  function init() {
    fetchConfig()
      .then(bootstrap)
      .catch(function (err) {
        log("config fetch failed; using defaults", err);
        bootstrap({
          brand_name: "Mackertich ONE",
          subtitle: "Wealth Management · SMIFS Ltd",
          welcome_message: "Welcome to Mackertich ONE. How may I assist you today?",
          bubble_icon: "💬",
          position: "bottom-right",
          theme: { primary: "#0B1B2B", accent: "#C9A86A", background: "#FFFFFF" },
        });
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
