# AI Try-On

See how clothes look on you before you buy them — a mobile-first virtual try-on web app powered entirely by self-hosted, commercially-licensed open-source AI. No paid AI API is ever called at runtime.

## Status

✅ **Milestones 1–11 of 13 complete**, plus substantial production-readiness hardening beyond the original 13-milestone plan (through commit `79b9179`) — see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full milestone plan and every dated entry.

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
| 9. Browser extension | ✅ Done |
| 10. PWA / mobile optimization | ✅ Done |
| 11. Usage limits & premium architecture | ✅ Done |
| 12+ | ⬜ Not started |

**Production-readiness hardening beyond the original 13-milestone plan** (dated entries in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md), full detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)/[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)):
- **Security hardening** — rate limiting, CORS lockdown, security headers/CSP, request-body limits, image decompression-bomb protection, SSRF/DNS-rebinding protection for URL-based product extraction, idempotency keys, job-capacity limits and cancellation.
- **Authentication** — password reset (SMTP-backed in production, console-logged in dev), server-side JWT revocation (`auth_version`), account deletion.
- **Admin/Operations** — an authorization gate, account disable/enable, paginated user/job visibility, plan-limit management, a persistent audit log of admin actions with its own read-only viewer, and an operational dashboard.
- **Production deployment preparation** — a Dockerfile, systemd units, an nginx reference config, a CI workflow, a real database-backed health check, and a real smoke test — all reviewed and documented, **none of it deployed anywhere yet**.

**Not yet done, stated plainly**: no production hosting exists — application, database, and reverse-proxy configuration are prepared but not deployed to any real server. No GPU host has been purchased or provisioned; the only real inference measurement is a free-tier Google Colab Tesla T4 run (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)'s GPU deployment section) — no RTX 4090/A40/or any other production-class GPU has been benchmarked. No payments/subscriptions exist.

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
├── extension/         # Browser "Try It On" button — see its own README
├── docs/              # ARCHITECTURE.md, AI_MODEL_LICENSE.md, DEVELOPMENT.md, ENVIRONMENT.md
└── tests/
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design, provider abstractions
- [docs/AI_MODEL_LICENSE.md](docs/AI_MODEL_LICENSE.md) — which AI models are used and why each is commercially safe
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — environment setup, milestone plan, exact commands
- [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md) — installed software, hardware, full environment variable reference
