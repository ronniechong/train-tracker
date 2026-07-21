import httpx
import pytest

from traintracker.poller.healthcheck import PING_URL_ENV, ping


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(PING_URL_ENV, raising=False)


def test_no_url_configured_is_a_noop():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert ping(client) is False


def test_successful_ping_returns_true():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert ping(client, url="https://hc-ping.invalid/abc123") is True
    assert seen["url"] == "https://hc-ping.invalid/abc123"


def test_failed_ping_returns_false_not_raises():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert ping(client, url="https://hc-ping.invalid/abc123") is False


def test_reads_url_from_env(monkeypatch):
    monkeypatch.setenv(PING_URL_ENV, "https://hc-ping.invalid/from-env")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert ping(client) is True
    assert seen["url"] == "https://hc-ping.invalid/from-env"
