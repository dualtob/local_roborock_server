"""CORS on the HTTPS listener for the origins in network.cors_allowed_origins."""

import pytest
from fastapi.testclient import TestClient

from conftest import WEB_ORIGIN, write_release_config
from roborock_local_server.config import load_config, resolve_paths
from roborock_local_server.server import ReleaseSupervisor

API_PATH = "/api/v1/activity/latest"


@pytest.fixture
def make_client(tmp_path):
    def make(*, cors_allowed_origins: list[str] | None = None) -> TestClient:
        network_settings = {} if cors_allowed_origins is None else {"cors_allowed_origins": cors_allowed_origins}
        config_file = write_release_config(tmp_path, listener_mode="external_tls", network_settings=network_settings)
        config = load_config(config_file)
        supervisor = ReleaseSupervisor(config=config, paths=resolve_paths(config_file, config))
        return TestClient(supervisor.app)

    return make


def _preflight(client: TestClient, origin: str):
    return client.options(
        API_PATH,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )


def test_preflight_from_a_listed_origin_is_allowed_with_authorization_and_json(make_client) -> None:
    client = make_client(cors_allowed_origins=[WEB_ORIGIN])

    response = _preflight(client, WEB_ORIGIN)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == WEB_ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers


def test_request_from_a_listed_origin_is_answered_with_its_origin(make_client) -> None:
    client = make_client(cors_allowed_origins=["https://other.example.com", WEB_ORIGIN])

    response = client.get(API_PATH, headers={"Origin": WEB_ORIGIN, "Authorization": "token"})

    assert response.headers["access-control-allow-origin"] == WEB_ORIGIN


@pytest.mark.parametrize(
    ("cors_allowed_origins", "origin"),
    [
        ([WEB_ORIGIN], "https://evil.example.com"),
        ([WEB_ORIGIN], "http://web.example.com"),
        (None, WEB_ORIGIN),
        ([], WEB_ORIGIN),
    ],
)
def test_unlisted_origins_get_no_cors_headers(make_client, cors_allowed_origins, origin) -> None:
    client = make_client(cors_allowed_origins=cors_allowed_origins)
    unlisted = make_client(cors_allowed_origins=None)

    preflight = _preflight(client, origin)
    request = client.get(API_PATH, headers={"Origin": origin, "Authorization": "token"})

    for response in (preflight, request):
        assert not [name for name in response.headers if name.lower().startswith("access-control-")]
    # The preflight reaches the routes exactly as it does without any CORS setting.
    assert preflight.status_code == _preflight(unlisted, origin).status_code
