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


DECOY_PATHS = [
    "region",
    "time",
    "location",
    "nc/prepare",
    "api/v1/getUrlByEmail",
    "api/v1/loginWithCode",
    "api/v1/decoy.js",
    "user/homes/12345.json",
    "ui/api/health",
    "mqtt",
    # Home-screen manifests and icons are served from the folder, but never under these routes.
    "api/v1/manifest.webmanifest",
    "api/v1/apple-touch-icon.png",
    "user/homes/manifest.webmanifest",
    "user/homes/icon-192.png",
    "admin/manifest.webmanifest",
    "admin/apple-touch-icon.png",
]


def test_files_named_like_api_or_admin_routes_are_not_served(tmp_path: Path) -> None:
    _write_web_app(tmp_path / "webapp")
    for relative in DECOY_PATHS:
        decoy = tmp_path / "webapp" / relative
        decoy.parent.mkdir(parents=True, exist_ok=True)
        decoy.write_text("decoy\n", encoding="utf-8")
    client = _client(tmp_path, web_app_folder="webapp")

    served = [path for path in DECOY_PATHS if client.get(f"/{path}").text == "decoy\n"]

    assert served == []


def test_only_static_asset_files_are_served(tmp_path: Path) -> None:
    folder = tmp_path / "webapp"
    _write_web_app(folder)
    resources = folder / "composeResources" / "com.kodraliu.localrock.resources"
    (resources / "values").mkdir(parents=True)
    (resources / "values" / "strings.commonMain.cvr").write_text("cvr\n", encoding="utf-8")
    (resources / "drawable").mkdir()
    (resources / "drawable" / "app_icon.xml").write_text("<vector/>\n", encoding="utf-8")
    (folder / "localrock.js.map").write_text("{}\n", encoding="utf-8")
    (folder / "styles.css").write_text("body{}\n", encoding="utf-8")
    (folder / "font.woff2").write_bytes(b"wOF2")
    for unwanted in ("notes", "config.toml", "secrets.json", "feed.xml", "run.sh"):
        (folder / unwanted).write_text("unwanted\n", encoding="utf-8")
    client = _client(tmp_path, web_app_folder="webapp")

    served = {
        path: client.get(f"/{path}").status_code == 200 and client.get(f"/{path}").text != "unwanted\n"
        for path in (
            "composeResources/com.kodraliu.localrock.resources/values/strings.commonMain.cvr",
            "composeResources/com.kodraliu.localrock.resources/drawable/app_icon.xml",
            "localrock.js.map",
            "styles.css",
            "font.woff2",
        )
    }
    unwanted = [
        path
        for path in ("notes", "config.toml", "secrets.json", "feed.xml", "run.sh")
        if client.get(f"/{path}").text == "unwanted\n"
    ]

    assert all(served.values()), served
    assert unwanted == []


def test_home_screen_manifest_is_served_as_a_web_app_manifest(tmp_path: Path) -> None:
    folder = tmp_path / "webapp"
    _write_web_app(folder)
    (folder / "manifest.webmanifest").write_text('{"name": "LocalRock"}\n', encoding="utf-8")
    client = _client(tmp_path, web_app_folder="webapp")

    response = client.get("/manifest.webmanifest")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/manifest+json"
    assert response.json() == {"name": "LocalRock"}


HOME_SCREEN_ICONS = [
    "apple-touch-icon.png",
    "apple-touch-icon-180x180.png",
    "icon-192.png",
    "icon-512.png",
    "favicon.ico",
]


def test_home_screen_icons_at_the_root_are_served_as_images(tmp_path: Path) -> None:
    folder = tmp_path / "webapp"
    _write_web_app(folder)
    for name in HOME_SCREEN_ICONS:
        (folder / name).write_bytes(b"icon:" + name.encode())
    client = _client(tmp_path, web_app_folder="webapp")

    served = {name: client.get(f"/{name}") for name in HOME_SCREEN_ICONS}

    assert {name: r.status_code for name, r in served.items()} == {name: 200 for name in HOME_SCREEN_ICONS}
    assert {name: r.content for name, r in served.items()} == {name: b"icon:" + name.encode() for name in HOME_SCREEN_ICONS}
    assert served["apple-touch-icon.png"].headers["content-type"] == "image/png"
    assert served["icon-512.png"].headers["content-type"] == "image/png"
    assert served["favicon.ico"].headers["content-type"].startswith("image/")
