"""Let listed web origins call the HTTPS listener from a browser (CORS).

With ``network.cors_allowed_origins`` empty, nothing here touches a request.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonical_origin(text: str) -> str | None:
    """An origin such as ``HTTPS://Web.Example.com:443`` as ``https://web.example.com``.

    Scheme and host are lowercased and the scheme's default port is dropped, so a
    configured origin and a browser's ``Origin`` header compare equal when they name
    the same site. Anything that isn't an http(s) origin gives ``None``.
    """
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        return None
    if (
        scheme not in _DEFAULT_PORTS
        or not parts.hostname
        or parts.username is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


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
        origin = canonical_origin(text)
        if origin is None:
            raise ValueError(
                f"{field_name} entry {text!r} is not an origin such as 'https://web.example.com'"
            )
        origins.append(origin)
    return tuple(origins)


class AllowedOrigins:
    """The origins in ``network.cors_allowed_origins``, matched the way browsers mean them."""

    def __init__(self, origins: tuple[str, ...]) -> None:
        self._origins = frozenset(filter(None, map(canonical_origin, origins)))

    def __bool__(self) -> bool:
        return bool(self._origins)

    def allows(self, origin: str | None) -> bool:
        """Whether a request's ``Origin`` header names one of the listed origins."""
        return origin is not None and canonical_origin(origin) in self._origins


class AllowedOriginsCorsMiddleware:
    """Answer CORS preflights and add CORS headers, for the listed origins only.

    A request from any other origin, or without one, passes through untouched: it gets
    no CORS headers, and an ``OPTIONS`` request reaches the routes just as it did before.
    """

    def __init__(self, app: ASGIApp, *, allowed_origins: AllowedOrigins) -> None:
        self._app = app
        self._allowed_origins = allowed_origins
        # Only requests from a listed origin reach this, so it may accept every origin;
        # matching by pattern rather than "*" makes it echo the browser's own Origin.
        self._cors = CORSMiddleware(
            app,
            allow_origin_regex=".*",
            allow_methods=["*"],
            allow_headers=["*"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self._allowed_origins.allows(request_origin(scope)):
            await self._cors(scope, receive, send)
            return
        await self._app(scope, receive, send)


def request_origin(scope: Scope) -> str | None:
    """The ``Origin`` header of an HTTP or WebSocket request, if it sent one."""
    for name, value in scope.get("headers", []):
        if name == b"origin":
            return value.decode("latin-1")
    return None
