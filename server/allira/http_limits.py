"""Bound request bodies before JSON parsing; do not log payloads or credentials."""

import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MAX_REQUEST_BYTES = 2 * 1024 * 1024


class RequestBodyLimit:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BYTES) -> None:
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        async def reject(status: int, code: str, message: str) -> None:
            response = JSONResponse({"error": {"code": code, "message": message}}, status_code=status,
                                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
            await response(scope, receive, send)

        headers = dict(scope["headers"])
        if headers.get(b"content-encoding", b"identity").lower() != b"identity":
            await reject(415, "unsupported_encoding", "Compressed request bodies are not supported.")
            return
        length = headers.get(b"content-length")
        if length is not None:
            if not length.isdigit():
                await reject(400, "invalid_request", "Content length is not valid.")
                return
            if len(length) > 10 or int(length) > self.max_bytes:
                await reject(413, "request_too_large", "Request body exceeds the 2 MiB wire limit.")
                return
        body = bytearray()
        try:
            async with asyncio.timeout(15):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self.max_bytes:
                        await reject(413, "request_too_large", "Request body exceeds the 2 MiB wire limit.")
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await reject(408, "request_timeout", "Request body was not received in time.")
            return

        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, bounded_receive, send)
