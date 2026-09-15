# product-extractor

The modular product/clothing image extraction system described in the project brief's section 7. It exists so the try-on pipeline never has to care *how* a garment image was obtained — a plain upload, a cropped screenshot, or a URL fetched from a shopping site all end up as the same `ExtractionResult`.

## Methods (brief section 7)

| Method | Status |
|---|---|
| **B. Product image upload** | ✅ Milestone 7 — `SaliencyProductExtractor` |
| **C. Screenshot upload** | ✅ Milestone 7 — same extractor; a screenshot is just a harder case of the same problem (more clutter to crop away) |
| **A. Product URL** | ✅ Milestone 8 — `fetchers/HttpProductPageFetcher` fetches a page (respecting robots.txt, SSRF-guarded — see `fetchers/ssrf_guard.py`), finds its product image via JSON-LD `Product` schema or Open Graph tags, then hands it to the same extractor above. Falls back to asking for an upload (Method B/C) when extraction fails, for any reason — never bypasses auth/paywalls/CAPTCHAs/anti-bot systems |
| **D. Browser extension** | ✅ Milestone 9 — `extension/` calls the exact same `POST /api/extract-product-url` this milestone added, no new backend code needed here |

## Design

```
extractors/base.py                 -- ProductImageExtractor interface, ExtractionResult
extractors/saliency_extractor.py   -- classical-CV implementation (see its docstring for why
                                       classical CV, not a new deep model, was the right call here)
fetchers/base.py                   -- ProductPageFetcher interface, ProductExtractionError
fetchers/ssrf_guard.py             -- assert_safe_url() -- the real security control behind Method A
fetchers/http_fetcher.py           -- HttpProductPageFetcher: robots.txt + fetch + JSON-LD/OG parse
services/extraction_service.py     -- the dispatch seam: extract_from_image() (Methods B/C),
                                       extract_from_url() (Method A) -- Method D (Milestone 9) plugs
                                       in the same way once it exists
```

Not a fork of anything — this is our own small MIT-licensed package, matching `ai/vendor/aitryon-bodyparser`'s pattern of a clean, independently-licensed adapter module.
