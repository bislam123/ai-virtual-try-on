"""User-facing error type.

Raised anywhere in the request path when something is the *user's* fault
(bad file, unsupported option, rate limit) and deserves a clear message —
never a stack trace (requirement #25 in the project brief).
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)


class UserFacingError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class CatchUnhandledExceptionsMiddleware(BaseHTTPMiddleware):
    """Converts a genuinely unhandled exception (a real bug -- not a
    UserFacingError/HTTPException/validation error, all of which already
    get a clean response from Starlette's ExceptionMiddleware before
    reaching here) into a generic 500 JSON response, instead of letting it
    propagate.

    Deliberately a middleware, NOT `@app.exception_handler(Exception)`:
    confirmed directly (not assumed) that Starlette special-cases a
    registered Exception/500 handler by attaching it to
    ServerErrorMiddleware, which sits OUTSIDE every app.add_middleware(...)
    layer -- including core/security_headers.py's SecurityHeadersMiddleware
    -- so a response built that way would reach the client with none of
    this app's security headers. Must be registered in main.py as the
    *innermost* app middleware (added first, before
    RequestBodySizeLimitMiddleware/SecurityHeadersMiddleware/CORSMiddleware)
    so the response it builds instead flows back out through all of them
    normally, the same as any other response.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except Exception:
            # Logged server-side for diagnosis; never returned to the
            # client -- an unhandled exception's message/traceback can
            # contain internal detail (paths, query fragments, library
            # internals) that has no business reaching a response body.
            logger.exception("Unhandled exception handling %s %s", request.method, request.url.path)
            return JSONResponse(status_code=500, content={"detail": "An unexpected error occurred."})


def configure_exception_handlers(app: FastAPI) -> None:
    """Register the UserFacingError -> clean JSON response handler.

    Called from both app/main.py and tests/test_tryon_api.py's test app
    builder, so the two never drift apart on how this error is surfaced.
    """

    @app.exception_handler(UserFacingError)
    async def user_facing_error_handler(request: Request, exc: UserFacingError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
