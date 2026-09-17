"""Request body size limit (defense-in-depth against oversized/DoS
requests -- see app/config.py's max_request_body_bytes and
docs/ARCHITECTURE.md's Security headers & request limits section).

A raw ASGI middleware, deliberately NOT a starlette.middleware.base.
BaseHTTPMiddleware and NOT a check inside a route or dependency: neither
of those can reject an oversized body without first letting something
downstream (Starlette's multipart parser, i.e. python-multipart, or
FastAPI's UploadFile machinery) read and spool/buffer it. The concrete gap
this closes: an image upload field was previously only size-checked in
core/validation.py's validate_and_load_image, *after* `await file.read()`
had already pulled the entire body into memory -- nothing stopped a
client from sending a multi-gigabyte request and having this process
spend real time, memory, and disk receiving all of it before that check
ever ran.

Two layers, in order, neither of which ever buffers more than `max_bytes`
worth of body before rejecting:

1. A `Content-Length`-header fast path: if the client honestly declares a
   size over the limit, the request is rejected immediately, before a
   single body byte is read. This covers the overwhelming majority of
   real requests (browsers/HTTP clients set Content-Length for a
   known-size body like a file upload).

2. A streaming counter, for a missing Content-Length (chunked transfer
   encoding) or one that simply lies: `receive()` is wrapped so every
   chunk actually pulled off the wire -- by whatever's parsing the body
   downstream, multipart or otherwise -- is counted as it arrives. The
   instant the running total would exceed the limit, the *next* chunk is
   never handed to the app at all: the wrapped receive() returns a
   synthetic `http.disconnect` instead, which is exactly the message
   Starlette already expects a real client disconnect to look like (see
   starlette.requests.Request.stream(), which raises ClientDisconnect on
   exactly this) -- so whatever was mid-parse stops cleanly rather than
   receiving more data or crashing. This middleware catches that same
   ClientDisconnect at its own layer and, only when *it* was the one that
   triggered it (never for a genuine client disconnect, which it lets
   propagate unchanged), turns it into a clean 413 response instead.

Must be registered (main.py's app.add_middleware) *before* CORSMiddleware
in source order, so CORSMiddleware ends up outermost and still gets to
add Access-Control-* headers to the 413 responses this middleware
generates directly -- see main.py's own comment on why ordering matters
here specifically.
"""

import json

from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Receive, Scope, Send

# Deliberately the exact wording the frontend is expected to show as-is
# (see frontend/src/api/http.ts's readErrorDetail, which surfaces this
# `detail` field directly) -- generic, no filesystem paths, no exception
# detail, no uploaded filename.
TOO_LARGE_DETAIL = "The uploaded files are too large. Please use smaller images."


async def _send_413(send: Send) -> None:
    body = json.dumps({"detail": TOO_LARGE_DETAIL}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


class RequestBodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None  # malformed header -- let normal request handling deal with it
            if declared is not None and declared > self.max_bytes:
                await _send_413(send)
                return

        received = 0
        limit_exceeded = False

        async def limited_receive():
            nonlocal received, limit_exceeded
            if limit_exceeded:
                # Already over budget and mid-abort -- keep telling
                # whatever's still asking that the client is gone, rather
                # than pulling (and counting) more real data.
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    limit_exceeded = True
                    return {"type": "http.disconnect"}
            return message

        try:
            await self.app(scope, limited_receive, send)
        except ClientDisconnect:
            if not limit_exceeded:
                raise  # a genuine client disconnect -- nothing to respond to
            await _send_413(send)
