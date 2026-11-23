"""Let listed web origins call the HTTPS listener from a browser (CORS).

With ``network.cors_allowed_origins`` empty, nothing here touches a request.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


def parse_allowed_origins(value: object, field_name: str) -> tuple[str, ...]:
    """Read a list of origins such as ``https://web.example.com`` into their canonical form."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list of origins such as 'https://web.example.com'")
    origins: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        parts = urlsplit(text)
        if (
            parts.scheme.lower() not in ("http", "https")
            or not parts.netloc
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                f"{field_name} entry {text!r} is not an origin such as 'https://web.example.com'"
            )
        origins.append(f"{parts.scheme.lower()}://{parts.netloc.lower()}")
    return tuple(origins)


class AllowedOriginsCorsMiddleware:
    """Answer CORS preflights and add CORS headers, for the listed origins only.

    A request from any other origin, or without one, passes through untouched: it gets
    no CORS headers, and an ``OPTIONS`` request reaches the routes just as it did before.
    """

    def __init__(self, app: ASGIApp, *, allowed_origins: tuple[str, ...]) -> None:
        self._app = app
        self._allowed_origins = frozenset(allowed_origins)
        self._cors = CORSMiddleware(
            app,
            allow_origins=list(allowed_origins),
            allow_methods=["*"],
            allow_headers=["*"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and request_origin(scope) in self._allowed_origins:
            await self._cors(scope, receive, send)
            return
        await self._app(scope, receive, send)


def request_origin(scope: Scope) -> str | None:
    """The ``Origin`` header of an HTTP or WebSocket request, if it sent one."""
    for name, value in scope.get("headers", []):
        if name == b"origin":
            return value.decode("latin-1")
    return None
