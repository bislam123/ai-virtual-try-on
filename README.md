# AI Try-On

See how clothes look on you before you buy them — a mobile-first virtual try-on web app powered entirely by self-hosted, commercially-licensed open-source AI. No paid AI API is ever called at runtime.

## Status

🚧 **Milestone 8 of 13** — see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full milestone plan and current progress.

| Milestone | Status |
|---|---|
| 1. Hardware & AI model evaluation | ✅ Done — see [docs/AI_MODEL_LICENSE.md](docs/AI_MODEL_LICENSE.md) |
| 2. AI model installation & first test inference | ✅ Done |
| 3. AI inference FastAPI service (`POST /api/try-on`) | ✅ Done |
| 4. Mobile-first web UI | ✅ Done |
| 5. Connect frontend to AI backend | ✅ Done as part of Milestone 4 (frontend already calls the real API) |
| 6. User accounts & secure image handling | ✅ Done |
| 7. Product image extraction | ✅ Done |
| 8. Product URL support | ✅ Done |
| 9+ | ⬜ Not started |

## Non-negotiables (see full brief for details)

- **No paid AI APIs** — no OpenAI/Gemini/Replicate/Stability/hosted-FASHN inference, ever, as a runtime dependency.
- **Self-hosted, commercially-licensed AI only** — every model's code, weights, and preprocessing dependencies are individually license-checked; see [docs/AI_MODEL_LICENSE.md](docs/AI_MODEL_LICENSE.md).
- **Mobile-first PWA** — must work well on Android/iPhone/tablet/desktop browsers.
- **Privacy-first** — uploaded photos are temporary by default; nothing is retained unless the user explicitly saves it, and nothing is used for training.

## Project structure

```
ai-try-on/
├── frontend/          # React + TypeScript + Tailwind (PWA)
├── backend/           # FastAPI application
├── ai/                # Self-hosted VTON model, inference, preprocessing
├── product-extractor/ # Modular product/clothing image extraction (see its own README)
├── extension/         # Browser extension (later milestone)
├── docs/              # ARCHITECTURE.md, AI_MODEL_LICENSE.md, DEVELOPMENT.md, ENVIRONMENT.md
└── tests/
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design, provider abstractions
- [docs/AI_MODEL_LICENSE.md](docs/AI_MODEL_LICENSE.md) — which AI models are used and why each is commercially safe
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — environment setup, milestone plan, exact commands
- [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md) — installed software, hardware, full environment variable reference
