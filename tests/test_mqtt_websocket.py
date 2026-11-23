"""MQTT over WebSockets at /mqtt on the HTTPS listener."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import shutil
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest
from websockets.sync.client import connect as websocket_connect
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import WEB_ORIGIN, write_release_config
from roborock_local_server.config import load_config, resolve_paths
from roborock_local_server.server import ReleaseSupervisor

HAWK_ID = "hawk-user-123"
HAWK_SESSION = "hawk-session-123"
MQTT_KEY = "hawk-mqtt-key-123"
# The App derives its MQTT login from rriot the same way (deriveMqttCreds).
APP_MQTT_USERNAME = hashlib.md5(f"{HAWK_ID}:{MQTT_KEY}".encode()).hexdigest()[2:10]
APP_MQTT_PASSWORD = hashlib.md5(f"{HAWK_SESSION}:{MQTT_KEY}".encode()).hexdigest()[16:]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _encode_remaining_length(length: int) -> bytes:
    encoded = bytearray()
    while True:
        byte, length = length % 128, length // 128
        encoded.append(byte | 0x80 if length else byte)
        if not length:
            return bytes(encoded)


def _packet(first_byte: int, body: bytes) -> bytes:
    return bytes([first_byte]) + _encode_remaining_length(len(body)) + body


def _mqtt_string(value: str) -> bytes:
    raw = value.encode()
    return len(raw).to_bytes(2, "big") + raw


def _connect_packet(*, client_id: str, username: str, password: str) -> bytes:
    variable_header = _mqtt_string("MQTT") + bytes([4, 0xC2]) + (60).to_bytes(2, "big")
    payload = _mqtt_string(client_id) + _mqtt_string(username) + _mqtt_string(password)
    return _packet(0x10, variable_header + payload)


def _subscribe_packet(*, packet_id: int, topic: str) -> bytes:
    return _packet(0x82, packet_id.to_bytes(2, "big") + _mqtt_string(topic) + b"\x00")


def _publish_packet(*, topic: str, payload: bytes) -> bytes:
    return _packet(0x30, _mqtt_string(topic) + payload)


def _split_packet(buffer: bytearray) -> bytes | None:
    """Remove and return the first complete MQTT packet in buffer, if there is one."""
    length = 0
    multiplier = 1
    index = 1
    while True:
        if index >= len(buffer):
            return None
        byte = buffer[index]
        length += (byte & 0x7F) * multiplier
        multiplier *= 128
        index += 1
        if not byte & 0x80:
            break
    total = index + length
    if len(buffer) < total:
        return None
    packet = bytes(buffer[:total])
    del buffer[:total]
    return packet


class _FakeBroker:
    """A tiny MQTT 3.1.1 broker standing in for Mosquitto: QoS 0, exact topic matches."""

    def __init__(self) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(10)
        self.port = self._server.getsockname()[1]
        self.connects = 0
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[socket.socket]] = {}
        self._running = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while self._running:
            try:
                conn, _addr = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        buffer = bytearray()
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buffer.extend(chunk)
                while (packet := _split_packet(buffer)) is not None:
                    if packet[0] >> 4 == 14:  # DISCONNECT
                        return
                    self._handle(conn, packet)
        except OSError:
            return
        finally:
            conn.close()

    def _handle(self, conn: socket.socket, packet: bytes) -> None:
        packet_type = packet[0] >> 4
        body = packet[2:] if packet[1] < 0x80 else packet[3:]
        if packet_type == 1:
            with self._lock:
                self.connects += 1
            conn.sendall(b"\x20\x02\x00\x00")
        elif packet_type == 8:
            packet_id = body[:2]
            topic_len = int.from_bytes(body[2:4], "big")
            topic = body[4 : 4 + topic_len].decode()
            with self._lock:
                self._subscribers.setdefault(topic, []).append(conn)
            conn.sendall(b"\x90\x03" + packet_id + b"\x00")
        elif packet_type == 3:
            topic_len = int.from_bytes(body[:2], "big")
            topic = body[2 : 2 + topic_len].decode()
            with self._lock:
                subscribers = list(self._subscribers.get(topic, []))
            for subscriber in subscribers:
                subscriber.sendall(packet)
        elif packet_type == 12:
            conn.sendall(b"\xd0\x00")

    def stop(self) -> None:
        self._running = False
        self._server.close()


def _write_self_signed_cert(cert_file: Path, key_file: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "api-roborock.example.com")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key=private_key, algorithm=hashes.SHA256())
    )
    cert_file.write_bytes(certificate.public_bytes(encoding=serialization.Encoding.PEM))
    key_file.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _write_config(
    tmp_path: Path,
    *,
    broker_port: int,
    mqtt_websocket_enabled: bool | None,
    listener_mode: str,
    cors_allowed_origins: list[str] | None = None,
) -> Path:
    network_settings: dict[str, object] = {"bind_host": "127.0.0.1"}
    if mqtt_websocket_enabled is not None:
        network_settings["mqtt_websocket_enabled"] = mqtt_websocket_enabled
    if cors_allowed_origins is not None:
        network_settings["cors_allowed_origins"] = cors_allowed_origins
    config_file = write_release_config(
        tmp_path,
        listener_mode=listener_mode,
        https_port=_free_port(),
        mqtt_tls_port=_free_port(),
        network_settings=network_settings,
    )
    if listener_mode == "local_tls":
        _write_self_signed_cert(tmp_path / "certs" / "fullchain.pem", tmp_path / "certs" / "privkey.pem")
    text = config_file.read_text(encoding="utf-8").replace("port = 1883", f"port = {broker_port}")
    config_file.write_text(text, encoding="utf-8")
    return config_file


def _seed_cloud_login(paths) -> None:
    _write_json(paths.inventory_path, {"home": {"id": 12345, "name": "Test Home"}, "devices": []})
    _write_json(
        paths.cloud_snapshot_path,
        {
            "user_data": {
                "uid": 1001,
                "token": "local-token-123",
                "rruid": "local-rruid-123",
                "rriot": {
                    "u": HAWK_ID,
                    "s": HAWK_SESSION,
                    "h": "hawk-secret-123",
                    "k": MQTT_KEY,
                    "r": {
                        "r": "US",
                        "a": "https://api-us.roborock.com",
                        "m": "ssl://mqtt-us.roborock.com:8883",
                        "l": "https://wood-us.roborock.com",
                    },
                },
            }
        },
    )


@pytest.fixture
def broker():
    fake = _FakeBroker()
    yield fake
    fake.stop()


@contextmanager
def _supervisors(tmp_path: Path, broker_port: int):
    started: list[ReleaseSupervisor] = []

    def make(
        *,
        mqtt_websocket_enabled: bool | None = True,
        listener_mode: str = "external_tls",
        web_app_folder: str | None = None,
        cors_allowed_origins: list[str] | None = None,
    ) -> ReleaseSupervisor:
        config_file = _write_config(
            tmp_path,
            broker_port=broker_port,
            mqtt_websocket_enabled=mqtt_websocket_enabled,
            listener_mode=listener_mode,
            cors_allowed_origins=cors_allowed_origins,
        )
        if web_app_folder is not None:
            with config_file.open("a", encoding="utf-8") as handle:
                handle.write(f'\n\n[web_app]\nfolder = "{web_app_folder}"\n')
        config = load_config(config_file)
        paths = resolve_paths(config_file, config)
        _seed_cloud_login(paths)
        supervisor = ReleaseSupervisor(config=config, paths=paths)
        supervisor._start_mqtt_proxy()
        started.append(supervisor)
        return supervisor

    try:
        yield make
    finally:
        for supervisor in started:
            if supervisor._mqtt_proxy is not None:
                supervisor._mqtt_proxy.stop()


@pytest.fixture
def make_supervisor(tmp_path, broker):
    with _supervisors(tmp_path, broker.port) as make:
        yield make


def _mosquitto_binary() -> str | None:
    homebrew = Path("/opt/homebrew/opt/mosquitto/sbin/mosquitto")
    if homebrew.is_file():
        return str(homebrew)
    return shutil.which("mosquitto")


@pytest.fixture
def mosquitto(tmp_path):
    """A real Mosquitto on a free local port, configured like the embedded broker."""
    binary = _mosquitto_binary()
    if binary is None:
        pytest.skip("Mosquitto is not installed")
    port = _free_port()
    config = tmp_path / "mosquitto.conf"
    config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\n", encoding="utf-8")
    process = subprocess.Popen([binary, "-c", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail("Mosquitto did not start")
                time.sleep(0.05)
        yield port
    finally:
        process.terminate()
        process.wait(timeout=5)


class _WebSocketMqtt:
    """Reads whole MQTT packets from a WebSocket, however they were split into frames."""

    def __init__(self, websocket) -> None:
        self._websocket = websocket
        self._buffer = bytearray()

    def send(self, packet: bytes) -> None:
        self._websocket.send_bytes(packet)

    def receive(self) -> bytes:
        while (packet := _split_packet(self._buffer)) is None:
            self._buffer.extend(self._websocket.receive_bytes())
        return packet


def test_app_login_round_trips_publish_and_subscribe_over_websocket(make_supervisor) -> None:
    supervisor = make_supervisor()
    client = TestClient(supervisor.app)

    with client.websocket_connect("/mqtt", subprotocols=["mqtt"]) as websocket:
        assert websocket.accepted_subprotocol == "mqtt"
        mqtt = _WebSocketMqtt(websocket)
        mqtt.send(_connect_packet(client_id="web-app", username=APP_MQTT_USERNAME, password=APP_MQTT_PASSWORD))
        assert mqtt.receive() == b"\x20\x02\x00\x00"

        mqtt.send(_subscribe_packet(packet_id=1, topic="rr/m/o/user/abc/123"))
        assert mqtt.receive() == b"\x90\x03\x00\x01\x00"

        publish = _publish_packet(topic="rr/m/o/user/abc/123", payload=b"hello robot")
        mqtt.send(publish)
        assert mqtt.receive() == publish

        mqtt.send(b"\xe0\x00")  # DISCONNECT
        with pytest.raises(WebSocketDisconnect):
            mqtt.receive()


def test_mqtt_websocket_round_trips_through_a_real_mosquitto(tmp_path, mosquitto) -> None:
    topic = "rr/m/o/user/abc/123"
    with _supervisors(tmp_path, mosquitto) as make:
        client = TestClient(make().app)

        with client.websocket_connect("/mqtt", subprotocols=["mqtt"]) as websocket:
            mqtt = _WebSocketMqtt(websocket)
            mqtt.send(_connect_packet(client_id="web-app", username=APP_MQTT_USERNAME, password=APP_MQTT_PASSWORD))
            assert mqtt.receive() == b"\x20\x02\x00\x00"
            mqtt.send(_subscribe_packet(packet_id=1, topic=topic))
            assert mqtt.receive() == b"\x90\x03\x00\x01\x00"
            publish = _publish_packet(topic=topic, payload=b"hello robot")
            mqtt.send(publish)
            assert mqtt.receive() == publish
            mqtt.send(b"\xe0\x00")  # DISCONNECT
            with pytest.raises(WebSocketDisconnect):
                mqtt.receive()

        with client.websocket_connect("/mqtt", subprotocols=["mqtt"]) as websocket:
            mqtt = _WebSocketMqtt(websocket)
            mqtt.send(_connect_packet(client_id="web-app", username=APP_MQTT_USERNAME, password="wrong-password"))
            assert mqtt.receive() == b"\x20\x02\x00\x05"
            with pytest.raises(WebSocketDisconnect):
                mqtt.receive()


def test_wrong_password_is_refused_and_never_reaches_the_broker(make_supervisor, broker) -> None:
    supervisor = make_supervisor()
    client = TestClient(supervisor.app)

    with client.websocket_connect("/mqtt", subprotocols=["mqtt"]) as websocket:
        mqtt = _WebSocketMqtt(websocket)
        mqtt.send(_connect_packet(client_id="web-app", username=APP_MQTT_USERNAME, password="wrong-password"))
        # CONNACK return code 5: not authorized.
        assert mqtt.receive() == b"\x20\x02\x00\x05"
        with pytest.raises(WebSocketDisconnect):
            mqtt.receive()

    assert broker.connects == 0


def test_web_app_folder_neither_intercepts_nor_serves_mqtt(make_supervisor, tmp_path) -> None:
    web_app = tmp_path / "webapp"
    web_app.mkdir()
    (web_app / "index.html").write_text("<!doctype html><title>LocalRock</title>\n", encoding="utf-8")
    (web_app / "mqtt").write_text("shadow\n", encoding="utf-8")
    supervisor = make_supervisor(web_app_folder="webapp")
    client = TestClient(supervisor.app)

    assert client.get("/", headers={"accept": "text/html"}).text.startswith("<!doctype html>")
    assert client.get("/mqtt").text != "shadow\n"
    with client.websocket_connect("/mqtt", subprotocols=["mqtt"]) as websocket:
        mqtt = _WebSocketMqtt(websocket)
        mqtt.send(_connect_packet(client_id="web-app", username=APP_MQTT_USERNAME, password=APP_MQTT_PASSWORD))
        assert mqtt.receive() == b"\x20\x02\x00\x00"
        mqtt.send(b"\xe0\x00")  # DISCONNECT
        with pytest.raises(WebSocketDisconnect):
            mqtt.receive()


@pytest.mark.parametrize("mqtt_websocket_enabled", [None, False])
def test_mqtt_websocket_is_off_unless_switched_on(make_supervisor, mqtt_websocket_enabled) -> None:
    supervisor = make_supervisor(mqtt_websocket_enabled=mqtt_websocket_enabled)
    client = TestClient(supervisor.app)

    assert supervisor.config.network.mqtt_websocket_enabled is False
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/mqtt", subprotocols=["mqtt"]):
            pass
    # Plain HTTP requests to /mqtt still fall through to the catch-all route, as before.
    assert client.get("/mqtt").status_code == 200


def test_websocket_without_the_mqtt_subprotocol_is_refused(make_supervisor, broker) -> None:
    supervisor = make_supervisor()
    client = TestClient(supervisor.app)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/mqtt"):
            pass
    assert broker.connects == 0


def test_app_login_is_refused_when_protocol_auth_is_off_just_like_mqtt_over_tls(make_supervisor, broker) -> None:
    supervisor = make_supervisor()
    supervisor.set_protocol_auth_enabled(False)
    client = TestClient(supervisor.app)

    with client.websocket_connect("/mqtt", subprotocols=["mqtt"]) as websocket:
        mqtt = _WebSocketMqtt(websocket)
        mqtt.send(_connect_packet(client_id="web-app", username=APP_MQTT_USERNAME, password=APP_MQTT_PASSWORD))
        assert mqtt.receive() == b"\x20\x02\x00\x05"
        with pytest.raises(WebSocketDisconnect):
            mqtt.receive()

    assert broker.connects == 0


def _logs_in_over_websocket(client: TestClient, headers: dict[str, str]) -> None:
    with client.websocket_connect("/mqtt", subprotocols=["mqtt"], headers=headers) as websocket:
        mqtt = _WebSocketMqtt(websocket)
        mqtt.send(_connect_packet(client_id="web-app", username=APP_MQTT_USERNAME, password=APP_MQTT_PASSWORD))
        assert mqtt.receive() == b"\x20\x02\x00\x00"
        mqtt.send(b"\xe0\x00")  # DISCONNECT
        with pytest.raises(WebSocketDisconnect):
            mqtt.receive()


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "https://evil.example.com"}, {"Origin": "http://web.example.com"}, {}],
    ids=["unlisted", "other-scheme", "missing"],
)
def test_listed_origins_guard_mqtt_websocket(make_supervisor, broker, headers) -> None:
    supervisor = make_supervisor(cors_allowed_origins=[WEB_ORIGIN])
    client = TestClient(supervisor.app)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/mqtt", subprotocols=["mqtt"], headers=headers):
            pass
    assert broker.connects == 0

    _logs_in_over_websocket(client, {"Origin": WEB_ORIGIN})


@pytest.mark.parametrize(
    ("cors_allowed_origins", "origin"),
    [(["https://Web.Example.com:443"], WEB_ORIGIN), ([WEB_ORIGIN], "https://WEB.example.com:443")],
    ids=["listed-mixed-case-with-default-port", "origin-mixed-case-with-default-port"],
)
def test_listed_origins_match_mqtt_websocket_regardless_of_case_and_default_port(
    make_supervisor, cors_allowed_origins, origin
) -> None:
    client = TestClient(make_supervisor(cors_allowed_origins=cors_allowed_origins).app)

    _logs_in_over_websocket(client, {"Origin": origin})


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example.com"}, {}], ids=["any", "missing"])
def test_mqtt_websocket_accepts_any_origin_without_listed_origins(make_supervisor, headers) -> None:
    client = TestClient(make_supervisor().app)

    _logs_in_over_websocket(client, headers)


@contextmanager
def _serving_https(supervisor: ReleaseSupervisor):
    """Run the supervisor's real uvicorn HTTPS listener on a background event loop."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    asyncio.run_coroutine_threadsafe(supervisor._start_http_server(), loop).result(timeout=10)
    try:
        yield
    finally:
        asyncio.run_coroutine_threadsafe(supervisor._http_server.stop(), loop).result(timeout=10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def _trust_any_certificate() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _read_tls_packet(conn: ssl.SSLSocket, buffer: bytearray) -> bytes:
    while (packet := _split_packet(buffer)) is None:
        chunk = conn.recv(4096)
        assert chunk, "MQTT over TLS connection closed"
        buffer.extend(chunk)
    return packet


def test_wss_on_the_https_port_meets_mqtt_over_tls_on_its_own_port(make_supervisor) -> None:
    supervisor = make_supervisor(listener_mode="local_tls")
    network = supervisor.config.network
    topic = "rr/m/o/user/abc/123"

    with _serving_https(supervisor):
        with websocket_connect(
            f"wss://127.0.0.1:{network.https_port}/mqtt",
            ssl=_trust_any_certificate(),
            subprotocols=["mqtt"],
            open_timeout=5,
        ) as websocket:
            assert websocket.subprotocol == "mqtt"
            websocket.send(_connect_packet(client_id="web-app", username=APP_MQTT_USERNAME, password=APP_MQTT_PASSWORD))
            assert websocket.recv(timeout=5) == b"\x20\x02\x00\x00"
            websocket.send(_subscribe_packet(packet_id=1, topic=topic))
            assert websocket.recv(timeout=5) == b"\x90\x03\x00\x01\x00"

            with socket.create_connection(("127.0.0.1", network.mqtt_tls_port), timeout=5) as raw:
                with _trust_any_certificate().wrap_socket(raw, server_hostname="api-roborock.example.com") as tls:
                    tls_buffer = bytearray()
                    tls.sendall(_connect_packet(client_id="app", username=APP_MQTT_USERNAME, password=APP_MQTT_PASSWORD))
                    assert _read_tls_packet(tls, tls_buffer) == b"\x20\x02\x00\x00"
                    publish = _publish_packet(topic=topic, payload=b"from the App")
                    tls.sendall(publish)

                    assert websocket.recv(timeout=5) == publish
