# AI Try-On browser extension

Method D from the project brief's section 7/8. Deliberately thin — per the brief, "Do NOT make the extension the foundation of the application" — this is a content script that detects a likely product page and shows a small **✨ Try It On** panel. Everything else (fetching/validating the product image, letting the user pick/take a photo, running the AI model) happens in the web app we already built, reusing existing backend endpoints unchanged.

Milestone 9 shipped the original single-button version, handing off only the page's own URL. This update adds: a late-render re-check for single-page-app sites, a Shadow-DOM-isolated floating panel (pill → card with a product thumbnail preview), and — the important functional change — an **image-URL handoff** that lets Method D work against sites whose page route sits behind a CAPTCHA/anti-bot wall (see "Why the image-URL handoff exists" below).

## How it works

```
content.js checks for a Product page (JSON-LD Product schema, or
og:type=product, or og:image + a price meta tag together) at
document_idle, then keeps re-checking on DOM mutations for up to 8s
(debounced) if nothing matched yet — covers SPA product pages that
render their data in late.
    ↓ match found: shows the "✨ Try It On" pill (briefly auto-expanded
      into a card with a product thumbnail preview, then collapses)
User clicks "Try It On" (pill or card button)
    ↓ content.js re-checks the page for a direct product image URL
      (same JSON-LD/og:image signals, recomputed fresh at click-time)
  Found an image URL:
    ↓ window.open(`${WEB_APP_URL}/?productImageUrl=${encodeURIComponent(imageUrl)}&sourceUrl=${encodeURIComponent(location.href)}`)
    Web app calls POST /api/extract-image-url — fetches only that one
    image resource, never the page itself.
  No image URL found (Product schema/OG matched, but no image field):
    ↓ window.open(`${WEB_APP_URL}/?productUrl=${encodeURIComponent(location.href)}`)
    Web app calls POST /api/extract-product-url (Milestone 8's original
    page-scrape path) — same behavior as before this update.
Web app (HomeScreen.tsx) reads whichever query param is present once on
load, clears it from the URL, and auto-runs the matching extraction flow
ProductUrlInput already exposes manually.
```

## Why the image-URL handoff exists

Milestone 9's page-URL handoff sends the backend off to re-fetch and parse the shopping site's own page HTML server-side. That works for sites with permissive robots.txt/no bot-detection, but fails outright against sites behind an anti-bot wall (e.g. a real Flipkart product page returns HTTP 403 to any non-browser fetch, regardless of headers — a reCAPTCHA Enterprise challenge, not a bug in our fetcher). The project's constraints rule out ever trying to defeat that wall (no header/UA spoofing, no headless-browser fingerprinting, no CAPTCHA solving).

The image-URL handoff sidesteps the problem instead of solving it head-on: the *user's own browser*, with its own session, already legitimately rendered the page and its product image. The content script reads that image's URL straight out of the page's own JSON-LD/`og:image` — the same signals used for detection — and hands the backend only that one image resource to fetch, typically served from a separate CDN host with no anti-bot gate at all. The backend never requests the gated page route. If the image host also happens to be gated, extraction fails with the same honest message as before — never worked around.

## Load it locally (unpacked, for development)

1. Chrome/Edge: go to `chrome://extensions` (or `edge://extensions`), enable **Developer mode**, click **Load unpacked**, and select this `extension/` folder.
2. Make sure the backend (`docs/DEVELOPMENT.md`) and frontend (`npm run dev`, `http://localhost:5173`) are both running.
3. Visit a page with product structured data (see "Testing" below for one that reliably has it) and the panel should appear bottom-right.

## Configuration

`content.js` opens `http://localhost:5173` by default. For a deployed frontend, either edit that constant before packaging, or set `window.__AI_TRYON_WEB_APP_URL__` earlier on the page (e.g. via another extension mechanism) — the script reads that override if present.

## Permissions, deliberately minimal

No `host_permissions`, no background service worker, no storage access. The manifest only asks for what showing a panel and reading the current page's own DOM require — this script runs on every page the user visits (see "Known limitations"), so keeping its footprint small matters for both review/trust and the user's own peace of mind. The panel itself is pure DOM/CSS/JS inside the content script (Shadow DOM for style isolation) — no new capability was added to add it.

`content.css` (Milestone 9's stylesheet for the single-button version) is no longer loaded by the manifest and is left in the repo unreferenced rather than deleted, for now: the panel's styles are inlined directly in `content.js` and injected into its Shadow DOM root instead, since a manifest-injected stylesheet can only reach the main document, not a shadow root the script creates itself.

## Testing

This extension is verified with Playwright's own extension-loading support (a persistent browser context with `--load-extension`), against locally-served static HTML fixtures — see `docs/DEVELOPMENT.md`'s Milestone 9 section (including its "extended" notes) for exactly what was checked and how (a JSON-LD product fixture with an image field, a late-rendering SPA-style fixture, and a non-product page).

## Known limitations, carried forward on purpose

- No per-site logic at all (matching Milestone 8's same choice) — detection relies entirely on the same public JSON-LD/Open Graph conventions the backend's URL extractor already uses, not hand-tuned per-site selectors. Sites with neither convention still won't show the panel (a deliberate choice — see the extended notes in `docs/DEVELOPMENT.md`'s Milestone 9 section — to avoid false positives and a per-site maintenance burden, rather than adding generic structural heuristics like price-regex or button-text matching).
- The late-render re-check (`MutationObserver`) only runs for up to 8 seconds after `document_idle` and only while the panel hasn't appeared yet — a page that renders its product data even later than that, or an SPA that swaps to a *different* product without a full navigation after the panel already appeared once, won't get a fresh check.
- Runs its (cheap) detection check on every HTTP/HTTPS page the user visits, not just known shopping domains — there's no fixed domain allowlist to maintain (the brief explicitly warns against assuming a fixed set of sites), but it does mean the content script is present everywhere, which is worth being upfront about.
