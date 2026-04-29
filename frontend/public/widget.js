/* Mackertich ONE chat widget — vanilla JS bubble + iframe loader.
 * Host pages embed this via:
 *   <script src="https://<host>/widget.js" defer></script>
 *
 * Behaviour:
 *   1. Reads the script tag's src to derive the host URL (so the same artifact
 *      works on staging, prod, etc.).
 *   2. Fetches /api/widget/config for theme + branding (CORS-friendly).
 *   3. Injects a floating circular bubble button (z-index 2147483000).
 *   4. On click, lazy-creates an iframe → /embed (display:none/block on
 *      minimize so chat state persists).
 *   5. iframe ↔ widget postMessage protocol:
 *        - {type:"mackertich:close"}             → minimise back to bubble
 *        - {type:"mackertich:assistant_message"} → unread badge dot if minimised
 *
 * Optional script-tag attributes (override config defaults):
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
    var css =
      "#" + CONTAINER_ID + " { all: initial; font-family: system-ui, -apple-system, sans-serif; }" +
      ".m1-bubble {" +
      "  position: fixed; bottom: 24px; " + pos +
      "  width: 60px; height: 60px; border-radius: 999px;" +
      "  background: " + bubbleBg + ";" +
      "  border: 2px solid " + bubbleAccent + ";" +
      "  display: flex; align-items: center; justify-content: center;" +
      "  cursor: pointer; z-index: " + Z + ";" +
      "  box-shadow: 0 12px 30px rgba(11, 27, 43, 0.35);" +
      "  transition: transform 0.18s, box-shadow 0.18s;" +
      "  color: white; font-size: 26px; line-height: 1; user-select: none;" +
      "}" +
      ".m1-bubble:hover { transform: scale(1.06); box-shadow: 0 16px 40px rgba(11, 27, 43, 0.45); }" +
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
      ".m1-iframe-wrap {" +
      "  position: fixed; bottom: 100px; " + iframePos +
      "  width: 380px; height: 600px; max-height: calc(100vh - 130px);" +
      "  z-index: " + Z + ";" +
      "  border-radius: 14px; overflow: hidden;" +
      "  box-shadow: 0 22px 60px rgba(11, 27, 43, 0.32);" +
      "  display: none;" +
      "  background: " + ((theme && theme.background) || "#FFFFFF") + ";" +
      "}" +
      ".m1-iframe-wrap.is-open { display: block; }" +
      ".m1-iframe-wrap iframe { width: 100%; height: 100%; border: 0; display: block; }" +
      "@media (max-width: 480px) {" +
      "  .m1-iframe-wrap { width: calc(100vw - 24px); height: calc(100vh - 120px); right: 12px; left: 12px; bottom: 90px; }" +
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

  function buildIframe() {
    var wrap = document.createElement("div");
    wrap.className = "m1-iframe-wrap";
    wrap.setAttribute("data-testid", "mackertich-widget-iframe-wrap");
    var iframe = document.createElement("iframe");
    iframe.title = "Mackertich ONE chat";
    iframe.setAttribute("data-testid", "mackertich-widget-iframe");
    iframe.allow = "clipboard-write";
    wrap.appendChild(iframe);
    return { wrap: wrap, iframe: iframe };
  }

  function isOpen(wrap) { return wrap.classList.contains("is-open"); }

  function bootstrap(config) {
    log("config", config);
    var pos = positionOverride || (config && config.position) || "bottom-right";
    injectStyles((config && config.theme) || {}, pos);
    var root = ensureContainer();
    var b = buildBubble((config && config.bubble_icon) || "💬");
    var f = buildIframe();
    root.appendChild(b.bubble);
    root.appendChild(f.wrap);

    var iframeReady = false;
    var open = function () {
      if (!iframeReady) {
        var url = hostBase + "/embed?theme_v=" + ((config && config.theme_version) || "v1");
        f.iframe.src = url;
        iframeReady = true;
      }
      f.wrap.classList.add("is-open");
      b.unread.classList.remove("is-visible");
      b.bubble.classList.remove("m1-bubble--pulse");
      log("opened");
    };
    var close = function () {
      f.wrap.classList.remove("is-open");
      log("closed");
    };
    b.bubble.addEventListener("click", function () {
      if (isOpen(f.wrap)) close(); else open();
    });

    window.addEventListener("message", function (e) {
      var d = e.data;
      if (!d || typeof d !== "object" || !d.type) return;
      if (e.source !== f.iframe.contentWindow) return; // origin verification — only listen to our iframe
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
