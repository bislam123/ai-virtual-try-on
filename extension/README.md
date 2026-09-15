# AI Try-On browser extension

Method D from the project brief's section 7/8. Deliberately thin — per the brief, "Do NOT make the extension the foundation of the application" — this is a content script that detects a likely product page, shows a **✨ Try It On** button, and on click opens the AI Try-On web app with the page's own URL attached. Everything else (fetching the product image, letting the user pick/take a photo, running the AI model) happens in the web app we already built, reusing Milestone 8's `POST /api/extract-product-url` pipeline unchanged — no new backend endpoint exists for this milestone, exactly as the brief's "the same backend must work with web app, extension, and future mobile" intends.

## How it works

```
content.js detects a Product page (JSON-LD Product schema, or
og:type=product, or og:image + a price meta tag together)
    ↓ injects the button
User clicks "✨ Try It On"
    ↓ window.open(`${WEB_APP_URL}/?productUrl=${encodeURIComponent(location.href)}`)
Web app (HomeScreen.tsx) reads ?productUrl= once on load, clears it from
the URL, and auto-runs the exact same extraction ProductUrlInput's
"paste a URL" flow already does (Milestone 8)
```

## Load it locally (unpacked, for development)

1. Chrome/Edge: go to `chrome://extensions` (or `edge://extensions`), enable **Developer mode**, click **Load unpacked**, and select this `extension/` folder.
2. Make sure the backend (`docs/DEVELOPMENT.md`) and frontend (`npm run dev`, `http://localhost:5173`) are both running.
3. Visit a page with product structured data (see "Testing" below for one that reliably has it) and the button should appear bottom-right.

## Configuration

`content.js` opens `http://localhost:5173` by default. For a deployed frontend, either edit that constant before packaging, or set `window.__AI_TRYON_WEB_APP_URL__` earlier on the page (e.g. via another extension mechanism) — the script reads that override if present.

## Permissions, deliberately minimal

No `host_permissions`, no background service worker, no storage access. The manifest only asks for what injecting a button and reading the current page's own DOM require — this script runs on every page the user visits (see "Known limitations"), so keeping its footprint small matters for both review/trust and the user's own peace of mind.

## Testing

`tests/test_extension.py`-adjacent: this extension is verified with Playwright's own extension-loading support (a persistent browser context with `--load-extension`), against a locally-served static HTML fixture with real `Product` JSON-LD — see `docs/DEVELOPMENT.md`'s Milestone 9 section for exactly what was checked and how.

## Known limitations, carried forward on purpose

- Detection runs once at `document_idle`, not on later DOM changes — a single-page-app site that swaps in product content without a full page navigation won't get a re-check. A `MutationObserver` would fix this; not implemented yet, since it adds real complexity for something not yet validated against real SPA shopping sites.
- No per-site logic at all (matching Milestone 8's same choice) — detection relies entirely on the same public JSON-LD/Open Graph conventions the backend's URL extractor already uses, not hand-tuned per-site selectors.
- Runs its (cheap) detection check on every HTTP/HTTPS page the user visits, not just known shopping domains — there's no fixed domain allowlist to maintain (the brief explicitly warns against assuming a fixed set of sites), but it does mean the content script is present everywhere, which is worth being upfront about.
