from __future__ import annotations

from secrets import compare_digest

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class DesktopApiGuard:
    """保护桌面专用本机 API，不缓冲流式响应或把凭据交给页面。"""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if scope["path"] == "/api" or scope["path"].startswith("/api/"):
            supplied = headers.get("x-camel-desktop-token", "")
            server = scope.get("server")
            expected_host = f"127.0.0.1:{server[1]}" if server else ""
            if (
                not compare_digest(supplied.encode(), self._token.encode())
                or headers.get("host") != expected_host
                or headers.get("origin") not in (None, f"http://{expected_host}")
            ):
                await JSONResponse({"detail": "桌面连接验证失败，请重新启动应用"}, 401)(
                    scope, receive, send
                )
                return

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; "
                        b"style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
                        b"connect-src 'self'; object-src 'none'; base-uri 'none'; "
                        b"frame-ancestors 'none'; form-action 'none'",
                    ),
                ]
            await send(message)

        await self._app(scope, receive, secure_send)
