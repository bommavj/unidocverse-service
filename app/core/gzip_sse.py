"""GZip that never touches Server-Sent Events.

Starlette 0.41's GZipMiddleware compresses streaming responses too; the gzip
buffer holds small SSE chunks until the stream ends, so progress events and
answer tokens reach the browser in one burst and the UI times out.
"""
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware, GZipResponder
from starlette.types import Message, Receive, Scope, Send


class _Responder(GZipResponder):
    async def send_with_gzip(self, message: Message) -> None:
        await super().send_with_gzip(message)
        if message["type"] == "http.response.start":
            content_type = Headers(raw=message["headers"]).get("content-type", "")
            if content_type.startswith("text/event-stream"):
                # Same path the base class uses for pre-encoded bodies: pass through
                self.content_encoding_set = True


class GZipExceptEventStream(GZipMiddleware):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and "gzip" in Headers(scope=scope).get("Accept-Encoding", ""):
            await _Responder(self.app, self.minimum_size, compresslevel=self.compresslevel)(scope, receive, send)
            return
        await self.app(scope, receive, send)
