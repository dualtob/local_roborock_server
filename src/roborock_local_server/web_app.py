"""Serve a folder of static Web App files from the HTTPS listener.

Kept in its own module so the fork's change stays out of upstream code.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Callable

from starlette.responses import FileResponse
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from .mqtt_websocket import MQTT_WEBSOCKET_PATH

# What a Web App build contains (see the `localrock-web.zip` release). Anything
# else in the folder is never served, so a stray file can't answer for an API.
STATIC_ASSET_SUFFIXES = frozenset(
    {
        ".html", ".js", ".mjs", ".wasm", ".css", ".map", ".txt",
        ".png", ".svg", ".ico", ".webp",
        ".ttf", ".otf", ".woff", ".woff2",
    }
)
# Compose resources: strings (.cvr), vector drawables (.xml) and other data files.
COMPOSE_RESOURCES_DIR = "composeResources"
COMPOSE_RESOURCE_SUFFIXES = frozenset({".cvr", ".xml", ".json"})
# The protocol's root and catch-all routes (`_register_protocol_routes`) match
# every path; the Web App's files take precedence over those two only.
CATCH_ALL_ROUTE_PATHS = frozenset({"/", "/{full_path:path}"})

ApiPathCheck = Callable[[str, str], bool]


class WebAppMiddleware:
    """Answers browser requests from ``folder`` and passes everything else through.

    ``/`` is also the robots' region bootstrap, so the index is served there
    only to browser navigations (an ``Accept`` header naming ``text/html``).
    Other paths are served only when they name a static asset file and no API
    route answers them: neither a registered route other than the catch-all,
    nor a path ``is_api_path(path, method)`` claims.
    """

    def __init__(self, app: ASGIApp, *, folder: Path, is_api_path: ApiPathCheck | None = None) -> None:
        self.app = app
        self.folder = folder.resolve()
        self.is_api_path = is_api_path or (lambda _path, _method: False)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        file_path = self._file_for(scope) if scope["type"] == "http" else None
        if file_path is None:
            await self.app(scope, receive, send)
            return
        await FileResponse(file_path)(scope, receive, send)

    def _file_for(self, scope: Scope) -> Path | None:
        method = scope["method"]
        if method not in ("GET", "HEAD"):
            return None
        path = scope["path"]
        if path == "/":
            return self._existing_file("index.html") if _accepts_html(scope) else None
        if path == "/admin" or path.startswith("/admin/") or path == MQTT_WEBSOCKET_PATH:
            return None
        relative = path.lstrip("/")
        if not _is_static_asset(relative):
            return None
        if _matches_registered_route(scope) or self.is_api_path(path, method):
            return None
        return self._existing_file(relative)

    def _existing_file(self, relative: str) -> Path | None:
        candidate = (self.folder / relative).resolve()
        if not candidate.is_relative_to(self.folder) or not candidate.is_file():
            return None
        return candidate


def _is_static_asset(relative: str) -> bool:
    path = PurePosixPath(relative)
    suffix = path.suffix.lower()
    if suffix in STATIC_ASSET_SUFFIXES:
        return True
    return suffix in COMPOSE_RESOURCE_SUFFIXES and path.parts[:1] == (COMPOSE_RESOURCES_DIR,)


def _matches_registered_route(scope: Scope) -> bool:
    app = scope.get("app")
    for route in getattr(app, "routes", ()):
        if getattr(route, "path", None) in CATCH_ALL_ROUTE_PATHS:
            continue
        match, _ = route.matches(scope)
        if match != Match.NONE:
            return True
    return False


def _accepts_html(scope: Scope) -> bool:
    for name, value in scope["headers"]:
        if name == b"accept" and b"text/html" in value.lower():
            return True
    return False


def web_app_folder(folder: str, config_file: Path) -> Path | None:
    """Resolves ``web_app.folder`` against the config file's directory; None when unset."""
    if not folder:
        return None
    path = Path(folder)
    return path if path.is_absolute() else config_file.parent / path
