import httpx
import pytest

import logging

from traintracker.poller import healthcheck
from traintracker.poller.healthcheck import PING_URL_ENV, ping


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(PING_URL_ENV, raising=False)
    healthcheck._consecutive_failures.clear()


async def test_no_url_configured_is_a_noop():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert await ping(client) is False


async def test_successful_ping_returns_true():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await ping(client, url="https://hc-ping.invalid/abc123") is True
    assert seen["url"] == "https://hc-ping.invalid/abc123"


async def test_failed_ping_returns_false_not_raises():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert await ping(client, url="https://hc-ping.invalid/abc123") is False


async def test_reads_url_from_env(monkeypatch):
    monkeypatch.setenv(PING_URL_ENV, "https://hc-ping.invalid/from-env")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await ping(client) is True
    assert seen["url"] == "https://hc-ping.invalid/from-env"


def _client(statuses):
    remaining = list(statuses)
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(remaining.pop(0))))


async def test_isolated_failure_is_not_a_warning(caplog):
    client = _client([500, 200])
    with caplog.at_level(logging.DEBUG, logger="traintracker.poller.healthcheck"):
        await ping(client, url="https://hc-ping.invalid/abc123")
        await ping(client, url="https://hc-ping.invalid/abc123")

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_warns_once_failures_become_consecutive_and_logs_recovery(caplog):
    n = healthcheck.WARN_AFTER_CONSECUTIVE_FAILURES
    client = _client([500] * (n + 1) + [200])
    with caplog.at_level(logging.DEBUG, logger="traintracker.poller.healthcheck"):
        for _ in range(n + 2):
            await ping(client, url="https://hc-ping.invalid/abc123")

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].startswith(f"dead-man ping failing: {n} consecutive failures (HTTPStatusError")
    assert any(f"recovered after {n + 1} consecutive failures" in r.getMessage() for r in caplog.records)


async def test_timeout_detail_names_the_exception_type(caplog):
    def handler(request):
        raise httpx.ReadTimeout("", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.DEBUG, logger="traintracker.poller.healthcheck"):
        await ping(client, url="https://hc-ping.invalid/abc123")

    assert "ReadTimeout" in caplog.records[-1].getMessage()
