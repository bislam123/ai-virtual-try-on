"""User-facing error type.

Raised anywhere in the request path when something is the *user's* fault
(bad file, unsupported option, rate limit) and deserves a clear message —
never a stack trace (requirement #25 in the project brief).
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class UserFacingError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def configure_exception_handlers(app: FastAPI) -> None:
    """Register the UserFacingError -> clean JSON response handler.

    Called from both app/main.py and tests/test_tryon_api.py's test app
    builder, so the two never drift apart on how this error is surfaced.
    """

    @app.exception_handler(UserFacingError)
    async def user_facing_error_handler(request: Request, exc: UserFacingError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
