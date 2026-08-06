from prometheus_client import CollectorRegistry

from traintracker.api.http_metrics import HttpMetricsMiddleware
from traintracker.metrics import Metrics


class _FakeRoute:
    def __init__(self, path: str):
        self.path = path


async def _run_app(scope, receive, send, messages):
    """A minimal ASGI app that sends exactly `messages`, in order, with no
    buffering of its own."""
    for message in messages:
        await send(message)


def _make_middleware(metrics):
    async def inner_app(scope, receive, send):
        route = scope.get("_test_route")
        if route is not None:
            scope["route"] = route
        messages = scope["_test_messages"]
        await _run_app(scope, receive, send, messages)

    return HttpMetricsMiddleware(inner_app, metrics)


async def _call(middleware, scope):
    sent = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


def _http_scope(method="GET", messages=(), route=None):
    return {
        "type": "http",
        "method": method,
        "path": "/whatever",
        "_test_messages": list(messages),
        "_test_route": route,
    }


async def test_records_request_total_and_duration_on_response_start():
    registry = CollectorRegistry()
    metrics = Metrics(registry)
    middleware = _make_middleware(metrics)
    scope = _http_scope(
        route=_FakeRoute("/healthz"),
        messages=[
            {"type": "http.response.start", "status": 200, "headers": []},
            {"type": "http.response.body", "body": b"ok"},
        ],
    )

    await _call(middleware, scope)

    assert registry.get_sample_value(
        "traintracker_http_requests_total",
        {"route": "/healthz", "method": "GET", "status": "200"},
    ) == 1.0
    assert registry.get_sample_value(
        "traintracker_http_request_duration_seconds_count",
        {"route": "/healthz", "method": "GET"},
    ) == 1.0


async def test_forwards_every_message_unmodified_in_order():
    # No buffering: each message the inner app sends must reach the real
    # `send` callable, in the same order and content, to not break streaming.
    registry = CollectorRegistry()
    metrics = Metrics(registry)
    middleware = _make_middleware(metrics)
    body_messages = [
        {"type": "http.response.body", "body": b"chunk-1", "more_body": True},
        {"type": "http.response.body", "body": b"chunk-2", "more_body": True},
        {"type": "http.response.body", "body": b"chunk-3", "more_body": False},
    ]
    scope = _http_scope(
        route=_FakeRoute("/api/stream"),
        messages=[{"type": "http.response.start", "status": 200, "headers": []}, *body_messages],
    )

    sent = await _call(middleware, scope)

    assert sent[0] == {"type": "http.response.start", "status": 200, "headers": []}
    assert sent[1:] == body_messages


async def test_does_not_wait_for_the_final_message_before_forwarding_the_first():
    # Simulates a slow/infinite generator: the middleware must forward
    # http.response.start (and record the metric) as soon as it arrives,
    # not wait for the whole sequence to complete.
    registry = CollectorRegistry()
    metrics = Metrics(registry)
    forwarded = []

    async def inner_app(scope, receive, send):
        scope["route"] = _FakeRoute("/api/stream")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        forwarded.append("start-forwarded")
        # An infinite SSE generator would sit here indefinitely.

    middleware = HttpMetricsMiddleware(inner_app, metrics)

    async def receive():
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    scope = _http_scope()
    await middleware(scope, receive, send)  # inner_app returns after one message, not hung

    assert forwarded == ["start-forwarded"]
    assert sent == [{"type": "http.response.start", "status": 200, "headers": []}]
    assert registry.get_sample_value(
        "traintracker_http_requests_total",
        {"route": "/api/stream", "method": "GET", "status": "200"},
    ) == 1.0


async def test_unmatched_route_uses_the_fixed_label():
    registry = CollectorRegistry()
    metrics = Metrics(registry)
    middleware = _make_middleware(metrics)
    scope = _http_scope(
        route=None,  # no FastAPI route matched
        messages=[{"type": "http.response.start", "status": 404, "headers": []}],
    )

    await _call(middleware, scope)

    assert registry.get_sample_value(
        "traintracker_http_requests_total",
        {"route": "unmatched", "method": "GET", "status": "404"},
    ) == 1.0


async def test_is_a_noop_without_a_metrics_instance():
    middleware = _make_middleware(None)
    scope = _http_scope(
        route=_FakeRoute("/healthz"),
        messages=[{"type": "http.response.start", "status": 200, "headers": []}],
    )

    sent = await _call(middleware, scope)  # must not raise

    assert sent == [{"type": "http.response.start", "status": 200, "headers": []}]


async def test_ignores_non_http_scopes():
    # A lifespan/websocket scope must pass straight through; this
    # middleware only instruments "type": "http".
    registry = CollectorRegistry()
    metrics = Metrics(registry)
    middleware = _make_middleware(metrics)
    scope = {"type": "lifespan", "_test_messages": [{"type": "lifespan.startup.complete"}]}

    sent = await _call(middleware, scope)

    assert sent == [{"type": "lifespan.startup.complete"}]
