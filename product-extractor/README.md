# product-extractor

The modular product/clothing image extraction system described in the project brief's section 7. It exists so the try-on pipeline never has to care *how* a garment image was obtained — a plain upload, a cropped screenshot, or (later) a scrape from a shopping site all end up as the same `ExtractionResult`.

## Methods (brief section 7)

| Method | Status |
|---|---|
| **B. Product image upload** | ✅ This milestone — `SaliencyProductExtractor` |
| **C. Screenshot upload** | ✅ This milestone — same extractor; a screenshot is just a harder case of the same problem (more clutter to crop away) |
| **A. Product URL** | ⬜ Milestone 8 — will add a `services/` dispatcher that fetches a page and hands the product image to the extractors here. Must never bypass auth/paywalls/CAPTCHAs/anti-bot systems (brief section 7) — falls back to asking for an upload (Method B/C) when extraction fails, exactly like the extractor below already does for a single image |
| **D. Browser extension** | ⬜ Milestone 9 — will call the same backend endpoint this milestone adds, `POST /api/extract-product-image` |

## Design

```
extractors/base.py                 -- ProductImageExtractor interface, ExtractionResult
extractors/saliency_extractor.py   -- classical-CV implementation (see its docstring for why
                                       classical CV, not a new deep model, was the right call here)
services/extraction_service.py     -- the seam where per-method dispatch (URL vs. upload vs.
                                       screenshot vs. extension) will live once Methods A/D exist
```

Not a fork of anything — this is our own small MIT-licensed package, matching `ai/vendor/aitryon-bodyparser`'s pattern of a clean, independently-licensed adapter module.
