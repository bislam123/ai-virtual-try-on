"""HTTP security response headers (see docs/ARCHITECTURE.md's Security
headers & request limits section).

A small, dedicated middleware. starlette.middleware.base.BaseHTTPMiddleware
is fine here, unlike body_size_limit.py's raw ASGI middleware: this only
ever adds headers to a response that's already been built, and never reads
the request body itself, so there's no streaming/buffering concern to
design around (see BaseHTTPMiddleware's own implementation -- it only
consumes the request body lazily, on whatever the *inner* app actually
asks for, never eagerly).

No Strict-Transport-Security header: deliberately not added. HSTS is only
safe to promise once every request is guaranteed to arrive over HTTPS --
this repository has no reverse proxy, TLS termination, or any other
deployment infrastructure yet (confirmed by inspection, same conclusion
docs/DEVELOPMENT.md already draws for scheduling/process-manager concerns).
Sending HSTS from a plain HTTP dev server would be actively wrong, not
just premature. This is a real production deployment responsibility,
documented in docs/ENVIRONMENT.md, not pretended to already exist here.
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# FastAPI's own interactive docs (Swagger UI at /docs, ReDoc at /redoc, and
# the OAuth2 popup target at /docs/oauth2-redirect) load their JS/CSS from
# a CDN and run inline bootstrap scripts -- neither of which this app
# controls or has reviewed for CSP compatibility. A CSP strict enough for
# this API's own JSON/image responses (which need zero script/style/frame
# capability at all) would break those pages outright. Rather than weaken
# the CSP everywhere to accommodate a CDN-loaded developer tool -- which
# would mean allowing 'unsafe-inline' globally, exactly what this
# milestone was told not to do without genuine need -- the CSP is simply
# not sent on these specific paths; every OTHER header below still is.
_CSP_EXEMPT_PATHS = {"/docs", "/redoc", "/docs/oauth2-redirect"}

# This API only ever returns JSON or a raster image (PNG) -- it never
# serves a page meant to load scripts, styles, fonts, or frames, so the
# strictest possible policy is also the *correct* one here, not just a
# cautious default: `default-src 'none'` blocks everything, and the
# explicit frame-ancestors/base-uri/form-action directives close off
# scenarios default-src alone doesn't cover (a response framed by another
# site, or a same-origin bug abusing <base>/a form pointed at this API).
# No 'unsafe-inline' or 'unsafe-eval' anywhere -- nothing here ever needs
# either.
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

# Mirrors frame-ancestors above in the older, more widely-supported
# header -- kept alongside CSP, not instead of it (standard defense-in-
# depth practice, e.g. OWASP's Secure Headers guidance): a browser that
# doesn't understand frame-ancestors yet still refuses to frame this.
_FRAME_OPTIONS = "DENY"

# Browser features this API's own responses never need, restricted so a
# response accidentally rendered as a document (e.g. a JSON error opened
# directly in a browser tab) can't invoke any of them. A short,
# well-understood list, not an exhaustive/speculative one -- the point is
# closing off capabilities this API obviously never uses, not enumerating
# every experimental Permissions-Policy directive that exists.
_PERMISSIONS_POLICY = (
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
    "magnetometer=(), gyroscope=(), interest-cohort=()"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY
        response.headers["X-Frame-Options"] = _FRAME_OPTIONS
        if request.url.path not in _CSP_EXEMPT_PATHS:
            response.headers["Content-Security-Policy"] = _API_CSP

        return response
