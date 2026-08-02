import logging

from traintracker.redaction import SecretRedactionFilter, configure_logging


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def _logger_with_filter(name: str, secrets: list[str]) -> tuple[logging.Logger, ListHandler]:
    handler = ListHandler()
    handler.addFilter(SecretRedactionFilter(secrets))
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, handler


def test_redacts_inbound_401_body_leak():
    # spike/probes.md: a 401 for a recognized-but-unauthorized key echoes
    # it verbatim in WWW-Authenticate's error_description.
    fake_key = "fake-real-key-xyz-999"
    logger, handler = _logger_with_filter("test.inbound", [fake_key])

    logger.error(
        "gateway 401 body: %s",
        f'error_description="API Key not authorized: {fake_key}"',
    )

    assert len(handler.lines) == 1
    assert fake_key not in handler.lines[0]
    assert "REDACTED" in handler.lines[0]


def test_redacts_outbound_header_value_leak():
    # Even if something logs the outgoing KeyId header (not something the
    # gateway client does today), the key must never survive to a handler.
    api_key = "super-secret-keyid-value"
    logger, handler = _logger_with_filter("test.outbound", [api_key])

    logger.debug("outgoing request headers: %s", {"KeyId": api_key})

    assert len(handler.lines) == 1
    assert api_key not in handler.lines[0]
    assert "REDACTED" in handler.lines[0]


def test_redacts_secret_embedded_in_a_logged_url():
    # 2b incident (2026-07-21, live): the dead-man ping URL carries its
    # secret as a path segment, not a header. httpx logs full request URLs
    # at INFO level on every successful cycle -- unlike the API key (a
    # header value), this leak vector is the URL string itself, and it
    # actually happened live before this secret was registered here.
    ping_url = "https://hc-ping.com/00000000-fake-uuid-0000-000000000000"
    logger, handler = _logger_with_filter("test.ping_url", [ping_url])

    logger.info('HTTP Request: GET %s "HTTP/1.1 200 OK"', ping_url)

    assert len(handler.lines) == 1
    assert ping_url not in handler.lines[0]
    assert "REDACTED" in handler.lines[0]


def test_redacts_slack_webhook_url():
    # M7 P0 (2026-08-02): same bug class as the dead-man ping URL above --
    # the Slack webhook URL carries its secret as a path segment, and
    # httpx logs full request URLs at INFO on every briefing/weekly-digest
    # send. Confirmed live: `docker compose logs` wrote the real webhook
    # URL on every successful delivery before this was registered.
    webhook_url = "https://hooks.invalid/services/T00000000/B00000000/fakefakefakefakefakefake"
    logger, handler = _logger_with_filter("test.webhook_url", [webhook_url])

    logger.info('HTTP Request: POST %s "HTTP/1.1 200 OK"', webhook_url)

    assert len(handler.lines) == 1
    assert webhook_url not in handler.lines[0]
    assert "REDACTED" in handler.lines[0]


def test_configure_logging_raises_httpx_logger_to_warning():
    # Belt-and-braces: even if a future secret-bearing URL is never
    # registered above, httpx's own INFO-level request logging (which
    # prints full URLs) should never fire at all.
    configure_logging("some-secret", level=logging.INFO)

    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING


def test_leaves_unrelated_messages_untouched():
    logger, handler = _logger_with_filter("test.unrelated", ["some-secret"])

    logger.info("gateway request: GET %s", "https://example.invalid/feed")

    assert handler.lines == ["gateway request: GET https://example.invalid/feed"]


def test_no_secrets_configured_is_a_noop():
    logger, handler = _logger_with_filter("test.empty", [])

    logger.info("message with %s", "no configured secret")

    assert handler.lines == ["message with no configured secret"]
