"""CORS for requests made with an API key, so the widget and the TypeScript SDK can call /v1 from other sites.

The app's own CORS settings (CORS_ORIGINS) let a few known sites call it with cookies. A request with an API key
needs no cookie: the key says who is asking, and the key's own origin allowlist (app/keys.py) says which sites may
use it. So for /v1 requests that carry an Authorization header, any origin may read the response, never with
credentials; the key check then refuses origins the key does not list.
"""

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ALLOW_HEADERS = "authorization, content-type, accept, idempotency-key"
ALLOW_METHODS = "GET, POST, DELETE"


class KeyCORS:
    def __init__(self, app: ASGIApp, prefix: str = "/v1/"):
        self.app, self.prefix = app, prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if not origin:
            return await self.app(scope, receive, send)
        asked = {h.strip().lower() for h in headers.get("access-control-request-headers", "").split(",")}
        if scope["method"] == "OPTIONS" and "access-control-request-method" in headers and "authorization" in asked:
            response = Response(status_code=204, headers={
                "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": ALLOW_METHODS,
                "Access-Control-Allow-Headers": ALLOW_HEADERS, "Access-Control-Max-Age": "600", "Vary": "Origin",
            })
            return await response(scope, receive, send)
        if "authorization" not in headers:
            return await self.app(scope, receive, send)

        async def with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                for name in [k for k in out.keys() if k.startswith("access-control-")]:
                    del out[name]  # a cookie-based CORS answer does not apply to a keyed request
                out["Access-Control-Allow-Origin"] = origin
                out["Access-Control-Expose-Headers"] = "Retry-After"
                out.add_vary_header("Origin")
            await send(message)

        await self.app(scope, receive, with_cors)
