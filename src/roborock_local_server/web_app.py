"""Serve a folder of static Web App files from the HTTPS listener.

Kept in its own module so the fork's change stays out of upstream code.
"""

from __future__ import annotations

from pathlib import Path

from starlette.responses import FileResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class WebAppMiddleware:
    """Answers browser requests from ``folder`` and passes everything else through.

    ``/`` is also the robots' region bootstrap, so the index is served there
    only to browser navigations (an ``Accept`` header naming ``text/html``).
    """

    def __init__(self, app: ASGIApp, *, folder: Path) -> None:
        self.app = app
        self.folder = folder.resolve()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        file_path = self._file_for(scope) if scope["type"] == "http" else None
        if file_path is None:
            await self.app(scope, receive, send)
            return
        await FileResponse(file_path)(scope, receive, send)

    def _file_for(self, scope: Scope) -> Path | None:
        if scope["method"] not in ("GET", "HEAD"):
            return None
        path = scope["path"]
        if path == "/admin" or path.startswith("/admin/"):
            return None
        if path == "/mqtt":  # Reserved for MQTT over WebSockets.
            return None
        if path == "/":
            return self._existing_file("index.html") if _accepts_html(scope) else None
        return self._existing_file(path.lstrip("/"))

    def _existing_file(self, relative: str) -> Path | None:
        candidate = (self.folder / relative).resolve()
        if not candidate.is_relative_to(self.folder) or not candidate.is_file():
            return None
        return candidate


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
