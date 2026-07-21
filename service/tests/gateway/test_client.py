import json

import httpx
import pytest

from traintracker.gateway.client import (
    API_KEY_ENV,
    BASE_URL_ENV,
    Feed,
    GatewayAuthError,
    GatewayClient,
    GatewayError,
    ThrottleWindow,
    base_url,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(BASE_URL_ENV, raising=False)


def _client_with_transport(handler, **kwargs) -> GatewayClient:
    client = GatewayClient(api_key="test-key", **kwargs)
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_requires_api_key(monkeypatch):
    with pytest.raises(GatewayError):
        GatewayClient()


def test_reads_api_key_from_env(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "from-env")
    client = GatewayClient()
    assert client._api_key == "from-env"


def test_base_url_override_env(monkeypatch):
    monkeypatch.setenv(BASE_URL_ENV, "https://example.invalid/custom")
    assert base_url() == "https://example.invalid/custom"


def test_sends_keyid_header_and_parses_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"protobuf-bytes-stand-in")

    client = _client_with_transport(handler)
    result = client.fetch(Feed.VEHICLE_POSITIONS)

    assert seen["headers"]["KeyId"] == "test-key"
    assert seen["url"].endswith("/vehicle-positions")
    assert result.payload == b"protobuf-bytes-stand-in"
    assert result.throttle == ()


def test_parses_rate_limit_header():
    windows = [
        {"window": 0, "type": "throttle", "remaining": 23},
        {"window": 59, "type": "throttle", "remaining": 959},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x",
            headers={"x-rate-limit": json.dumps(windows)},
        )

    client = _client_with_transport(handler)
    result = client.fetch(Feed.TRIP_UPDATES)

    assert result.throttle == (
        ThrottleWindow(window=0, type="throttle", remaining=23),
        ThrottleWindow(window=59, type="throttle", remaining=959),
    )


def test_malformed_rate_limit_header_degrades_gracefully():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x", headers={"x-rate-limit": "not-json"})

    client = _client_with_transport(handler)
    result = client.fetch(Feed.SERVICE_ALERTS)

    assert result.throttle == ()


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_raise_without_body_or_headers_in_message(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            headers={
                "WWW-Authenticate": 'ApiKey realm="api-realm", error="Invalid API-Key", '
                'error_description="API Key not authorized: test-key"'
            },
        )

    client = _client_with_transport(handler)

    with pytest.raises(GatewayAuthError) as excinfo:
        client.fetch(Feed.VEHICLE_POSITIONS)

    assert excinfo.value.status_code == status
    # The exception message must never carry the raw header/body content —
    # that's exactly where the API echoes the key back (spike/probes.md).
    assert "test-key" not in str(excinfo.value)
    assert "WWW-Authenticate" not in str(excinfo.value)


def test_other_http_errors_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"soap fault stand-in")

    client = _client_with_transport(handler)

    with pytest.raises(httpx.HTTPStatusError):
        client.fetch(Feed.SERVICE_ALERTS)


def test_context_manager_closes_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x")

    client = _client_with_transport(handler)
    with client as c:
        assert c is client
    assert client._client.is_closed
