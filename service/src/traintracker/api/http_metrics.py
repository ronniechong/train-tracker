"""Per-route HTTP observability middleware.

Gap this closes: the only per-route Prometheus signal before this was
`traintracker_rate_limited_requests_total` (429s only, by endpoint + which
cap tripped) -- no general hit counter, no status-code breakdown, no
latency. A route silently returning 500s, or getting hammered below the
rate-limit threshold, was invisible.

Raw ASGI middleware, deliberately NOT Starlette's `BaseHTTPMiddleware`:
this app has a real long-lived SSE endpoint (`/api/stream`, see
`_event_source`/`StreamingResponse` in `api/app.py`) that
`BaseHTTPMiddleware` is documented to buffer/mishandle. "Duration" here
means time to first byte (response START, not full completion) -- for
every ordinary JSON route this is indistinguishable from full latency
anyway, and for `/api/stream` specifically it avoids an hours-long open
connection becoming a single enormous outlier in the latency histogram.
"""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..metrics import Metrics

# No FastAPI route matched (a 404 / arbitrary probed path) -- a fixed
# label, not the raw requested path, so scanning/probing traffic can't
# create unbounded label cardinality (same discipline
# `traintracker_briefings_sent_total` already had to apply).
UNMATCHED_ROUTE_LABEL = "unmatched"


class HttpMetricsMiddleware:
    def __init__(self, app: ASGIApp, metrics: Metrics | None):
        self._app = app
        self._metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._metrics is None:
            await self._app(scope, receive, send)
            return

        start = time.monotonic()

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                # `scope["route"]` is set by Starlette's router once a
                # route has matched, before the endpoint (or a 404) is
                # reached -- already resolved by response-start time.
                route_obj = scope.get("route")
                route = route_obj.path if route_obj is not None else UNMATCHED_ROUTE_LABEL
                self._metrics.record_http_request(
                    route=route,
                    method=scope["method"],
                    status=message["status"],
                    duration_s=time.monotonic() - start,
                )
            await send(message)

        await self._app(scope, receive, _send)
