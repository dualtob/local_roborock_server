import json
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import write_release_config
from roborock_local_server.config import load_config, resolve_paths
from roborock_local_server.server import ReleaseSupervisor

BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def _write_web_app(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text("<!doctype html><title>LocalRock</title>\n", encoding="utf-8")
    (folder / "composeApp.js").write_text("console.log('web app');\n", encoding="utf-8")
    (folder / "composeApp.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00")


def _client(tmp_path: Path, *, web_app_folder: str | None) -> TestClient:
    config_file = write_release_config(tmp_path, stack_fqdn="api-roborock.example.com")
    if web_app_folder is not None:
        with config_file.open("a", encoding="utf-8") as handle:
            handle.write(f'\n\n[web_app]\nfolder = "{web_app_folder}"\n')
    config = load_config(config_file)
    paths = resolve_paths(config_file, config)
    paths.inventory_path.parent.mkdir(parents=True, exist_ok=True)
    paths.inventory_path.write_text(
        json.dumps({"home": {"id": 12345, "name": "Test Home"}, "devices": []}),
        encoding="utf-8",
    )
    supervisor = ReleaseSupervisor(config=config, paths=paths)
    return TestClient(supervisor.app, base_url="https://api-roborock.example.com")


def test_browser_opening_root_gets_web_app_index(tmp_path: Path) -> None:
    _write_web_app(tmp_path / "webapp")
    client = _client(tmp_path, web_app_folder="webapp")

    response = client.get("/", headers={"accept": BROWSER_ACCEPT})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>LocalRock</title>" in response.text


def test_web_app_assets_are_served_with_their_content_types(tmp_path: Path) -> None:
    _write_web_app(tmp_path / "webapp")
    client = _client(tmp_path, web_app_folder="webapp")

    script = client.get("/composeApp.js")
    wasm = client.get("/composeApp.wasm")

    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert script.text == "console.log('web app');\n"
    assert wasm.status_code == 200
    assert wasm.headers["content-type"] == "application/wasm"
    assert wasm.content == b"\x00asm\x01\x00\x00\x00"


def _responses(client: TestClient, requests: list[tuple[str, str]]) -> list[tuple[int, str, bytes]]:
    results = []
    for method, path in requests:
        response = client.request(method, path, headers={"host": "api-roborock.example.com"})
        results.append((response.status_code, response.headers.get("content-type", ""), response.content))
    return results


PROTOCOL_REQUESTS = [
    ("GET", "/"),
    ("POST", "/"),
    ("GET", "/region"),
    ("GET", "/api/v1/activity/latest"),
    ("GET", "/api/v1/some/unknown/route"),
    ("GET", "/composeApp.js/extra"),
    ("GET", "/admin"),
    ("GET", "/admin/"),
]


def test_protocol_and_admin_routes_are_unchanged_by_the_web_app(tmp_path: Path) -> None:
    _write_web_app(tmp_path / "webapp")
    (tmp_path / "webapp" / "admin").mkdir()
    (tmp_path / "webapp" / "admin" / "index.html").write_text("shadow\n", encoding="utf-8")
    (tmp_path / "webapp" / "admin.html").write_text("shadow\n", encoding="utf-8")

    expected = _responses(_client(tmp_path, web_app_folder=None), PROTOCOL_REQUESTS)
    actual = _responses(_client(tmp_path, web_app_folder="webapp"), PROTOCOL_REQUESTS)

    assert actual == expected
    assert expected[0][1].startswith("application/json")


def test_admin_paths_are_never_served_from_the_web_app_folder(tmp_path: Path) -> None:
    _write_web_app(tmp_path / "webapp")
    (tmp_path / "webapp" / "admin").mkdir()
    (tmp_path / "webapp" / "admin" / "app.js").write_text("shadow\n", encoding="utf-8")
    client = _client(tmp_path, web_app_folder="webapp")

    response = client.get("/admin/app.js")

    assert response.text != "shadow\n"


def test_files_outside_the_web_app_folder_are_not_served(tmp_path: Path) -> None:
    _write_web_app(tmp_path / "webapp")
    client = _client(tmp_path, web_app_folder="webapp")

    response = client.get("/..%2Fconfig.toml")

    assert "session_secret" not in response.text
    assert response.headers["content-type"].startswith("application/json")


def test_without_the_option_a_browser_at_root_gets_the_protocol_answer(tmp_path: Path) -> None:
    _write_web_app(tmp_path / "webapp")
    client = _client(tmp_path, web_app_folder=None)

    response = client.get("/", headers={"accept": BROWSER_ACCEPT})
    asset = client.get("/composeApp.js")

    assert response.headers["content-type"].startswith("application/json")
    assert asset.headers["content-type"].startswith("application/json")
