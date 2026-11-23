"""MQTT over WebSockets at /mqtt on the HTTPS listener, for browser clients.

Each WebSocket is handed to the MQTT TLS proxy as if it were one more client socket,
so the login check, onboarding rules and tracing are the very same ones MQTT over TLS uses.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from typing import Callable

from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect

from .backend import MqttTlsProxy
from .cors import AllowedOrigins, request_origin

MQTT_WEBSOCKET_PATH = "/mqtt"
MQTT_SUBPROTOCOL = "mqtt"
_READ_CHUNK_BYTES = 4096
# RFC 6455 close code for "try again later": the MQTT proxy is restarting or not up yet.
_CLOSE_TRY_AGAIN_LATER = 1013


def register_mqtt_websocket_route(
    app: FastAPI,
    *,
    mqtt_proxy: Callable[[], MqttTlsProxy | None],
    allowed_origins: AllowedOrigins = AllowedOrigins(()),
) -> None:
    """Accept MQTT over WebSockets at /mqtt and relay it through the running MQTT proxy.

    With ``allowed_origins`` set, only browser pages from those origins may connect.
    Browsers don't apply CORS to WebSockets, so this check is what keeps other websites
    from reaching the broker through a visitor's browser.
    """

    @app.websocket(MQTT_WEBSOCKET_PATH)
    async def mqtt_websocket(websocket: WebSocket) -> None:
        if MQTT_SUBPROTOCOL not in websocket.scope.get("subprotocols", []):
            # Closing before accept answers the upgrade with HTTP 403.
            await websocket.close()
            return
        if allowed_origins and not allowed_origins.allows(request_origin(websocket.scope)):
            await websocket.close()
            return
        proxy = mqtt_proxy()
        if proxy is None:
            await websocket.close(code=_CLOSE_TRY_AGAIN_LATER)
            return
        await websocket.accept(subprotocol=MQTT_SUBPROTOCOL)
        client = websocket.client
        addr = (client.host, client.port) if client is not None else ("", 0)
        await _relay_through_proxy(websocket, proxy, addr)


async def _relay_through_proxy(websocket: WebSocket, proxy: MqttTlsProxy, addr: tuple[str, int]) -> None:
    relay_end, proxy_end = socket.socketpair()
    # The proxy reads the CONNECT, checks the login and relays to the broker on its own thread,
    # exactly as it does for a socket accepted on the MQTT TLS port.
    threading.Thread(
        target=proxy._handle_client,
        args=(proxy_end, addr),
        daemon=True,
        name="mqtt-websocket",
    ).start()
    reader, writer = await asyncio.open_connection(sock=relay_end)

    async def websocket_to_proxy() -> None:
        while True:
            message = await websocket.receive()
            data = message.get("bytes")
            # MQTT over WebSockets only uses binary frames; anything else ends the session.
            if message["type"] == "websocket.disconnect" or data is None:
                return
            writer.write(data)
            await writer.drain()

    async def proxy_to_websocket() -> None:
        while chunk := await reader.read(_READ_CHUNK_BYTES):
            await websocket.send_bytes(chunk)

    tasks = [asyncio.create_task(websocket_to_proxy()), asyncio.create_task(proxy_to_websocket())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect, OSError):
            pass
