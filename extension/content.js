/**
 * AI Try-On content script (Milestone 9, brief section 8 — Method D).
 *
 * Deliberately thin, per the brief: "Do NOT make the extension the
 * foundation of the application." This script only detects a likely
 * product page and injects a button; everything else — actually fetching
 * the product image, letting the user pick/take a photo, running the AI
 * model — happens in the web app we already built (Milestones 4-8),
 * reusing the exact same POST /api/extract-product-url pipeline from
 * Milestone 8 rather than duplicating any of that logic here in JS.
 *
 * No host_permissions, no background service worker, no browser storage:
 * the only capability this needs is reading the current page's own DOM
 * (which a content script already has) and opening a new tab via the
 * standard window.open() — the smallest permission footprint that can
 * do the job, which matters both for review/trust and because this
 * script runs on every page the user visits.
 */

(function () {
  "use strict";

  // Override via a page-injected `window.__AI_TRYON_WEB_APP_URL__` for local
  // dev against a non-default port — see extension/README.md. In a packaged
  // build this should point at the production web app's own origin.
  const WEB_APP_URL = window.__AI_TRYON_WEB_APP_URL__ || "http://localhost:5173";

  const BUTTON_ID = "ai-tryon-button";

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

  function injectButton() {
    if (document.getElementById(BUTTON_ID)) return; // already injected

    const button = document.createElement("button");
    button.id = BUTTON_ID;
    button.type = "button";
    button.textContent = "✨ Try It On";
    button.setAttribute("aria-label", "Try this item on with AI Try-On");
    button.addEventListener("click", handleClick);
    document.body.appendChild(button);
  }

  function handleClick() {
    const target = new URL(WEB_APP_URL);
    target.searchParams.set("productUrl", window.location.href);
    // A genuine user click, so this isn't treated as an unwanted popup.
    window.open(target.toString(), "_blank", "noopener,noreferrer");
  }

  if (pageLooksLikeAProduct()) {
    injectButton();
  }
})();
