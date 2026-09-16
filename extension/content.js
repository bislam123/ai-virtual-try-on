/**
 * AI Try-On content script (Milestone 9, brief section 8 — Method D),
 * extended with richer detection, a floating panel, and an image-URL
 * handoff so the extension works against sites whose page route is
 * behind a CAPTCHA/anti-bot wall — see extension/README.md for the
 * full "why".
 *
 * Deliberately thin, per the brief: "Do NOT make the extension the
 * foundation of the application." This script only detects a likely
 * product page and shows a small panel; everything else — actually
 * fetching/validating the product image, letting the user pick/take a
 * photo, running the AI model — happens in the web app we already built,
 * reusing the existing POST /api/extract-product-url and POST
 * /api/extract-image-url endpoints (the latter added alongside this
 * update) rather than duplicating any of that logic here in JS.
 *
 * No host_permissions, no background service worker, no browser storage:
 * the only capabilities this uses are reading the current page's own DOM
 * (which a content script already has) and opening a new tab via the
 * standard window.open() — the smallest permission footprint that can do
 * the job, which matters both for review/trust and because this script
 * runs on every page the user visits.
 */

(function () {
  "use strict";

  // Override via a page-injected `window.__AI_TRYON_WEB_APP_URL__` for local
  // dev against a non-default port — see extension/README.md. In a packaged
  // build this should point at the production web app's own origin.
  const WEB_APP_URL = window.__AI_TRYON_WEB_APP_URL__ || "http://localhost:5173";

  const HOST_ID = "ai-tryon-host";

  // How long to keep re-checking a page that didn't look like a product at
  // document_idle, for single-page-app sites that render their product data
  // (JSON-LD/Open Graph) in after the initial load — see "Known
  // limitations" in README.md before this milestone. Debounced so a burst
  // of mutations only triggers one re-check, not dozens.
  const RESCAN_WINDOW_MS = 8000;
  const RESCAN_DEBOUNCE_MS = 500;
  const AUTO_COLLAPSE_MS = 4000;

  // --- product-page detection (unchanged from Milestone 9) ----------------

  function pageLooksLikeAProduct() {
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const data = JSON.parse(script.textContent || "");
        if (jsonLdHasProductType(data)) return true;
      } catch {
        // malformed JSON-LD on the page — not our concern, just skip it
      }
    }

    const ogType = document.querySelector('meta[property="og:type"]');
    if (ogType && (ogType.content || "").toLowerCase() === "product") return true;

    // A weaker signal on its own, so only treat it as a match alongside
    // something that reads as commerce-specific (a price meta tag is a
    // common, fairly reliable one across shopping sites' Open Graph tags).
    const hasOgImage = document.querySelector('meta[property="og:image"]');
    const hasPriceMeta = document.querySelector(
      'meta[property="product:price:amount"], meta[property="og:price:amount"]',
    );
    return Boolean(hasOgImage && hasPriceMeta);
  }

  function jsonLdHasProductType(node) {
    if (Array.isArray(node)) return node.some(jsonLdHasProductType);
    if (node && typeof node === "object") {
      if (node["@graph"]) return jsonLdHasProductType(node["@graph"]);
      const type = node["@type"];
      const types = Array.isArray(type) ? type : [type];
      if (types.includes("Product")) return true;
    }
    return false;
  }

  // --- product image URL discovery -----------------------------------------
  //
  // Reuses the exact same two signals as detection above (JSON-LD Product
  // schema, then og:image) — deliberately not a new/different heuristic —
  // so this only ever offers an image the page itself already told us,
  // directly or via Open Graph, is its product image. Recomputed fresh on
  // every click rather than cached from the initial scan, so a late-
  // rendering SPA that filled in its image after our first check still
  // hands off correctly.

  function findProductImageUrl() {
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const data = JSON.parse(script.textContent || "");
        const url = jsonLdProductImage(data);
        if (url) return absoluteUrl(url);
      } catch {
        // malformed JSON-LD — skip, same as detection above
      }
    }
    const ogImage = document.querySelector('meta[property="og:image"]');
    if (ogImage && ogImage.content) return absoluteUrl(ogImage.content);
    return null;
  }

  function jsonLdProductImage(node) {
    if (Array.isArray(node)) {
      for (const item of node) {
        const found = jsonLdProductImage(item);
        if (found) return found;
      }
      return null;
    }
    if (node && typeof node === "object") {
      if (node["@graph"]) return jsonLdProductImage(node["@graph"]);
      const type = node["@type"];
      const types = Array.isArray(type) ? type : [type];
      if (types.includes("Product") && node.image) return firstImageUrl(node.image);
    }
    return null;
  }

  function firstImageUrl(image) {
    if (typeof image === "string") return image;
    if (Array.isArray(image)) return image.length ? firstImageUrl(image[0]) : null;
    if (image && typeof image === "object" && typeof image.url === "string") return image.url;
    return null;
  }

  function absoluteUrl(maybeRelative) {
    try {
      return new URL(maybeRelative, window.location.href).toString();
    } catch {
      return null;
    }
  }

  // --- floating panel, Shadow DOM ------------------------------------------
  //
  // A Shadow DOM root (not just a scoped CSS id like Milestone 9's single
  // button) so a richer multi-element panel is genuinely isolated from the
  // host page's own styles in both directions — no host stylesheet can
  // reach in, and nothing here can leak out. The stylesheet is inlined
  // (rather than loaded from content.css via the manifest's own `css`
  // array) because manifest-injected CSS targets the main document, not a
  // shadow root we create ourselves.

  const PANEL_CSS = `
    :host {
      all: initial;
      position: fixed;
      z-index: 2147483647;
      bottom: 20px;
      right: 20px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    .panel {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 10px;
    }
    button {
      all: unset;
      box-sizing: border-box;
      cursor: pointer;
    }
    .pill {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 12px 20px;
      border-radius: 999px;
      background: #4f46e5;
      color: #ffffff;
      font-size: 15px;
      font-weight: 700;
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
      user-select: none;
      transition: transform 0.1s ease;
    }
    .pill:hover { background: #4338ca; }
    .pill:active { transform: scale(0.97); }
    .card {
      position: relative;
      width: 220px;
      background: #ffffff;
      border-radius: 16px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.22);
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .card[hidden] { display: none; }
    .dismiss {
      position: absolute;
      top: 6px;
      right: 6px;
      width: 24px;
      height: 24px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      color: #64748b;
      font-size: 13px;
      line-height: 1;
    }
    .dismiss:hover { background: #f1f5f9; }
    .thumb {
      width: 100%;
      height: 140px;
      object-fit: cover;
      border-radius: 10px;
      background: #f1f5f9;
    }
    .thumb[hidden] { display: none; }
    .title {
      margin: 0;
      font-size: 13px;
      font-weight: 600;
      color: #0f172a;
    }
    .primary {
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 10px 16px;
      border-radius: 10px;
      background: #4f46e5;
      color: #ffffff;
      font-size: 14px;
      font-weight: 700;
      text-align: center;
    }
    .primary:hover { background: #4338ca; }
    .primary:active { transform: scale(0.98); }
  `;

  let panelController = null;

  function ensurePanel() {
    if (panelController) return panelController;
    if (document.getElementById(HOST_ID)) return null; // guard re-entry mid-injection

    const host = document.createElement("div");
    host.id = HOST_ID;
    const shadow = host.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = PANEL_CSS;
    shadow.appendChild(style);

    const root = document.createElement("div");
    root.className = "panel";
    root.innerHTML = `
      <button type="button" class="pill" aria-label="Try this item on with AI Try-On" aria-expanded="false">
        <span>✨ Try It On</span>
      </button>
      <div class="card" hidden>
        <button type="button" class="dismiss" aria-label="Dismiss">✕</button>
        <img class="thumb" alt="" hidden />
        <p class="title">Try this item on with AI Try-On</p>
        <button type="button" class="primary">✨ Try It On</button>
      </div>
    `;
    shadow.appendChild(root);
    document.body.appendChild(host);

    const pillButton = root.querySelector(".pill");
    const card = root.querySelector(".card");
    const dismissButton = root.querySelector(".dismiss");
    const thumb = root.querySelector(".thumb");
    const primaryButton = root.querySelector(".primary");

    let collapseTimer = null;

    function expand() {
      card.hidden = false;
      pillButton.setAttribute("aria-expanded", "true");
      clearTimeout(collapseTimer);
    }
    function collapse() {
      card.hidden = true;
      pillButton.setAttribute("aria-expanded", "false");
      clearTimeout(collapseTimer);
    }
    function scheduleAutoCollapse() {
      clearTimeout(collapseTimer);
      collapseTimer = setTimeout(collapse, AUTO_COLLAPSE_MS);
    }

    pillButton.addEventListener("click", () => (card.hidden ? expand() : collapse()));
    dismissButton.addEventListener("click", collapse);
    primaryButton.addEventListener("click", handleTryItOnClick);

    panelController = {
      showWithPreview(imageUrl) {
        if (imageUrl) {
          thumb.src = imageUrl;
          thumb.hidden = false;
        }
        expand();
        scheduleAutoCollapse();
      },
    };
    return panelController;
  }

  // --- handoff (Milestone 9's page-URL path, plus the newer image-URL path)

  function handleTryItOnClick() {
    const target = new URL(WEB_APP_URL);
    // Recomputed at click-time, not reused from initial detection, so a
    // page whose product data finished rendering after our first scan
    // still hands off the image URL correctly.
    const imageUrl = findProductImageUrl();
    if (imageUrl) {
      target.searchParams.set("productImageUrl", imageUrl);
      target.searchParams.set("sourceUrl", window.location.href);
    } else {
      // No direct image URL available (Product schema/OG matched, but
      // without an image field) — same page-URL fallback Milestone 9 always
      // used. The web app re-scrapes the page server-side for this case,
      // which is the path that can't reach a CAPTCHA-gated site; see
      // product-extractor/fetchers/http_fetcher.py.
      target.searchParams.set("productUrl", window.location.href);
    }
    // A genuine user click, so this isn't treated as an unwanted popup.
    window.open(target.toString(), "_blank", "noopener,noreferrer");
  }

  // --- detection entry point + late-render re-check ------------------------

  function tryInject() {
    if (document.getElementById(HOST_ID)) return true;
    if (!pageLooksLikeAProduct()) return false;
    const panel = ensurePanel();
    if (panel) panel.showWithPreview(findProductImageUrl());
    return true;
  }

  function startObservingForLateRender() {
    const deadline = Date.now() + RESCAN_WINDOW_MS;
    let debounceTimer = null;

    const observer = new MutationObserver(() => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        if (tryInject() || Date.now() > deadline) observer.disconnect();
      }, RESCAN_DEBOUNCE_MS);
    });
    observer.observe(document.body, { childList: true, subtree: true });
    // Hard stop even if mutations go quiet near the deadline without ever
    // triggering one last debounced check.
    setTimeout(() => observer.disconnect(), RESCAN_WINDOW_MS + RESCAN_DEBOUNCE_MS);
  }

  if (!tryInject()) {
    startObservingForLateRender();
  }
})();
