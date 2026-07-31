import httpx
import pytest

from traintracker.poller.slack import WEBHOOK_URL_ENV, post_message


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(WEBHOOK_URL_ENV, raising=False)


async def test_no_url_configured_is_a_noop():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert await post_message(client, "hello") is False


async def test_successful_post_returns_true_and_sends_text():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await post_message(client, "Belgrave line disrupted", webhook_url="https://hooks.invalid/abc") is True
    assert seen["url"] == "https://hooks.invalid/abc"
    assert b"Belgrave line disrupted" in seen["body"]


async def test_failed_post_returns_false_not_raises():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert await post_message(client, "hello", webhook_url="https://hooks.invalid/abc") is False


async def test_reads_url_from_env(monkeypatch):
    monkeypatch.setenv(WEBHOOK_URL_ENV, "https://hooks.invalid/from-env")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await post_message(client, "hello") is True
    assert seen["url"] == "https://hooks.invalid/from-env"
