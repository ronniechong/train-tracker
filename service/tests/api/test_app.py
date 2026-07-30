import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from google.transit import gtfs_realtime_pb2

from traintracker.api.app import _event_source, create_app
from traintracker.api.limits import ConnectionTracker, RateLimiter
from traintracker.gateway.client import GatewayClient
from traintracker.poller.breaker import CircuitBreaker
from traintracker.poller.loop import PollerLoop
from traintracker.state.eventhub import InProcessEventHub
from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.store import StateStore


def _tu_bytes(timestamp: int, trip_id: str = "T1") -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    entity = feed.entity.add()
    entity.id = "tu1"
    entity.trip_update.trip.trip_id = trip_id
    entity.trip_update.trip.route_id = "R1"
    return feed.SerializeToString()


def _vp_bytes(timestamp: int, trip_id: str = "T1") -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    entity = feed.entity.add()
    entity.id = "vp1"
    entity.vehicle.trip.trip_id = trip_id
    entity.vehicle.position.latitude = -37.81
    entity.vehicle.position.longitude = 144.96
    entity.vehicle.position.bearing = 90.0
    entity.vehicle.timestamp = timestamp
    return feed.SerializeToString()


def _sa_bytes(timestamp: int) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    return feed.SerializeToString()


async def _loop_with_vanished_train(first_seen_at: datetime) -> tuple[PollerLoop, StateStore]:
    """T1 is live for one cycle at `first_seen_at`, then drops out of both
    TU and VP entirely (empty entity lists, not just an unchanged header) on
    a second cycle 100s later -- past `COASTING_TIMEOUT_S` (90s), so it's
    tracked as "ghost" with `last_seen_at == first_seen_at` and no snapshot
    left in `store.latest_snapshots` at all. Exercises the M3 fix: a fully
    vanished train must still show up in `/api/state`, not silently
    disappear."""
    vanished = False

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "trip-updates" in path:
            return httpx.Response(200, content=_sa_bytes(2000) if vanished else _tu_bytes(1000))
        if "vehicle-positions" in path:
            return httpx.Response(200, content=_sa_bytes(2000) if vanished else _vp_bytes(1000))
        return httpx.Response(200, content=_sa_bytes(2000 if vanished else 1000))

    gateway = GatewayClient(api_key="test-key")
    gateway._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())
    healthcheck_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    loop = PollerLoop(
        gateway=gateway,
        store=store,
        gap_log=InMemoryEventLog(),
        breaker=CircuitBreaker(),
        healthcheck_client=healthcheck_client,
    )

    await loop.run_cycle(first_seen_at)
    vanished = True
    await loop.run_cycle(first_seen_at + timedelta(seconds=100))
    return loop, store


async def _running_loop() -> tuple[PollerLoop, StateStore]:
    """A `PollerLoop` that's completed one real `run_cycle` against a
    scripted transport, so `last_changed_at`/`breaker`/`store` are all
    populated the same way they would be in production -- avoids guessing
    at the API layer's dependencies' internal shapes from outside."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "trip-updates" in path:
            return httpx.Response(200, content=_tu_bytes(1000))
        if "vehicle-positions" in path:
            return httpx.Response(200, content=_vp_bytes(1000))
        return httpx.Response(200, content=_sa_bytes(1000))

    gateway = GatewayClient(api_key="test-key")
    gateway._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    store = StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())
    gap_log = InMemoryEventLog()
    healthcheck_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    loop = PollerLoop(
        gateway=gateway,
        store=store,
        gap_log=gap_log,
        breaker=CircuitBreaker(),
        healthcheck_client=healthcheck_client,
    )
    # Real "now", not the fixed T0 constant other test files use for replay
    # determinism -- the API's staleness check compares against wall-clock
    # `datetime.now()`, so a fixed historical cycle_time would always read
    # as stale here regardless of the feed actually being "fresh".
    await loop.run_cycle(datetime.now(timezone.utc))
    return loop, store


async def _client_for(
    loop: PollerLoop,
    store: StateStore,
    hub: InProcessEventHub | None = None,
    connections: ConnectionTracker | None = None,
    rate_limiter: RateLimiter | None = None,
    heartbeat_interval_s: float = 20.0,
) -> httpx.AsyncClient:
    app = create_app(
        loop=loop,
        store=store,
        hub=hub or InProcessEventHub(),
        connections=connections,
        rate_limiter=rate_limiter,
        heartbeat_interval_s=heartbeat_interval_s,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_healthz_returns_ok():
    loop, store = await _running_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_state_reflects_live_train_and_fresh_feeds():
    loop, store = await _running_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/api/state")

    body = response.json()
    assert response.status_code == 200
    assert body["backoff_active"] is False

    assert len(body["trains"]) == 1
    train = body["trains"][0]
    assert train["trip_id"] == "T1"
    assert train["route_id"] == "R1"
    assert train["status"] == "live"
    assert train["latitude"] == -37.81

    for feed in ("trip-updates", "vehicle-positions", "service-alerts"):
        assert body["feeds"][feed]["stale"] is False
        assert body["feeds"][feed]["last_changed_at"] is not None


async def test_state_marks_feed_stale_when_never_changed():
    store = StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())
    gap_log = InMemoryEventLog()
    healthcheck_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    gateway = GatewayClient(api_key="test-key")
    gateway._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    loop = PollerLoop(gateway=gateway, store=store, gap_log=gap_log, healthcheck_client=healthcheck_client)
    # No successful cycle at all -- last_changed_at is None for every feed.

    async with await _client_for(loop, store) as client:
        response = await client.get("/api/state")

    body = response.json()
    for feed in ("trip-updates", "vehicle-positions", "service-alerts"):
        assert body["feeds"][feed]["stale"] is True
        assert body["feeds"][feed]["last_changed_at"] is None


async def test_cors_rejects_origin_not_on_allowlist(monkeypatch):
    monkeypatch.setenv("TT_CORS_ORIGINS", "https://ronniechong.com")
    loop, store = await _running_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/api/state", headers={"Origin": "https://evil.invalid"})

    # CORSMiddleware doesn't block the request itself, it withholds the
    # allow-origin header -- the browser is what actually enforces this.
    assert "access-control-allow-origin" not in response.headers


async def test_cors_allows_configured_origin(monkeypatch):
    monkeypatch.setenv("TT_CORS_ORIGINS", "https://ronniechong.com")
    loop, store = await _running_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/api/state", headers={"Origin": "https://ronniechong.com"})

    assert response.headers["access-control-allow-origin"] == "https://ronniechong.com"


async def test_state_includes_vanished_train_as_ghost_with_last_known_position():
    # Recent last_seen_at (100s ago in cycle-time terms, effectively "now"
    # in wall-clock terms) -- well inside MAX_GHOST_AGE_S, must still show.
    loop, store = await _loop_with_vanished_train(datetime.now(timezone.utc))

    async with await _client_for(loop, store) as client:
        response = await client.get("/api/state")

    trains = response.json()["trains"]
    assert len(trains) == 1
    train = trains[0]
    assert train["trip_id"] == "T1"
    assert train["status"] == "ghost"
    # No fresher data available once vanished from both feeds -- honestly
    # null, not invented.
    assert train["route_id"] is None
    assert train["position_updated_at"] is None
    assert train["schedule_updated_at"] is None
    # But the last confirmed fix and when it was confirmed are retained.
    assert train["latitude"] == -37.81
    assert train["longitude"] == 144.96
    assert train["last_seen_at"] is not None


async def test_state_excludes_ghost_train_older_than_max_age():
    # last_seen_at 3 hours before real "now" -- past MAX_GHOST_AGE_S (2h),
    # so this is presentation-layer "definitely journey-ended", not a
    # currently-relevant ghost.
    stale_sighting = datetime.now(timezone.utc) - timedelta(hours=3)
    loop, store = await _loop_with_vanished_train(stale_sighting)

    async with await _client_for(loop, store) as client:
        response = await client.get("/api/state")

    assert response.json()["trains"] == []


def _parse_sse_chunk(chunk: str) -> tuple[str, dict | None]:
    """Parses one already-yielded `_event_source` chunk. Not testing this
    against a real HTTP/ASGI transport at all -- httpx's `ASGITransport`
    fully awaits an ASGI app to completion before returning anything,
    which cannot work for a route whose body never ends on its own. Driving
    `_event_source` directly (below) is what actually exercises the event
    logic; the FastAPI route wiring around it (headers, media type,
    connection-cap 503) needs a real live server to verify, not a unit
    test -- flagged as a genuine coverage gap in the M3 milestone doc."""
    if chunk.startswith(":"):
        return "heartbeat", None
    event_line, data_line, _ = chunk.split("\n", 2)
    return event_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))


async def _never_disconnected() -> bool:
    return False


async def test_event_source_sends_snapshot_then_delta_on_change():
    hub = InProcessEventHub()
    loop, store = await _running_loop()

    gen = _event_source(loop, store, hub, _never_disconnected, heartbeat_interval_s=20.0)
    try:
        event_type, body = _parse_sse_chunk(await gen.__anext__())
        assert event_type == "snapshot"
        assert body["trains"][0]["trip_id"] == "T1"
        assert body["trains"][0]["status"] == "live"

        # Simulate the poll loop completing another cycle in which T1 has
        # moved -- a real deployment does this via `hub.publish` in
        # poller/__main__.py's `_run_poll_loop`; driven directly here since
        # this test doesn't run the full main loop.
        def moved_handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if "trip-updates" in path:
                return httpx.Response(200, content=_tu_bytes(3000))
            if "vehicle-positions" in path:
                feed = gtfs_realtime_pb2.FeedMessage()
                feed.header.gtfs_realtime_version = "2.0"
                feed.header.timestamp = 3000
                entity = feed.entity.add()
                entity.id = "vp1"
                entity.vehicle.trip.trip_id = "T1"
                entity.vehicle.position.latitude = -37.90
                entity.vehicle.position.longitude = 145.00
                entity.vehicle.timestamp = 3000
                return httpx.Response(200, content=feed.SerializeToString())
            return httpx.Response(200, content=_sa_bytes(3000))

        loop._gateway._client = httpx.AsyncClient(transport=httpx.MockTransport(moved_handler))
        await loop.run_cycle(datetime.now(timezone.utc))
        hub.publish("tick")

        event_type, body = _parse_sse_chunk(await gen.__anext__())
        assert event_type == "delta"
        assert body["changed"][0]["trip_id"] == "T1"
        assert body["changed"][0]["latitude"] == -37.90
        assert body["removed"] == []
    finally:
        await gen.aclose()


async def test_event_source_sends_heartbeat_when_idle():
    hub = InProcessEventHub()
    loop, store = await _running_loop()

    gen = _event_source(loop, store, hub, _never_disconnected, heartbeat_interval_s=0.05)
    try:
        await gen.__anext__()  # the initial snapshot
        event_type, _body = _parse_sse_chunk(await gen.__anext__())
        assert event_type == "heartbeat"
    finally:
        await gen.aclose()


async def test_event_source_unsubscribes_from_hub_on_disconnect():
    hub = InProcessEventHub()
    loop, store = await _running_loop()

    async def already_disconnected() -> bool:
        return True

    gen = _event_source(loop, store, hub, already_disconnected, heartbeat_interval_s=20.0)
    await gen.__anext__()  # the initial snapshot -- disconnect isn't checked until after this
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()  # disconnect check now trips, generator ends

    assert len(hub._subscribers) == 0


async def test_stream_rejects_connection_over_global_cap():
    loop, store = await _running_loop()
    connections = ConnectionTracker(max_global=0, max_per_ip=5)

    async with await _client_for(loop, store, connections=connections) as client:
        response = await client.get("/api/stream")

    assert response.status_code == 503


async def test_state_rejects_request_over_per_ip_rate_limit():
    loop, store = await _running_loop()
    rate_limiter = RateLimiter(max_per_ip=1, max_global=100, window_s=60)

    async with await _client_for(loop, store, rate_limiter=rate_limiter) as client:
        first = await client.get("/api/state")
        second = await client.get("/api/state")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"


async def test_healthz_rejects_request_over_global_rate_limit():
    loop, store = await _running_loop()
    rate_limiter = RateLimiter(max_per_ip=100, max_global=1, window_s=60)

    async with await _client_for(loop, store, rate_limiter=rate_limiter) as client:
        first = await client.get("/healthz")
        second = await client.get("/healthz")

    assert first.status_code == 200
    assert second.status_code == 429


async def test_rate_limit_is_scoped_per_endpoint_and_client():
    # A limiter shared across the whole app still tracks distinct requests
    # correctly -- healthz and state hitting the same global counter is
    # intended (that's the point of a *global* cap), but this confirms two
    # different clients don't bleed into each other's per-IP counters.
    loop, store = await _running_loop()
    rate_limiter = RateLimiter(max_per_ip=1, max_global=100, window_s=60)

    async with await _client_for(loop, store, rate_limiter=rate_limiter) as client:
        a = await client.get("/api/state", headers={"x-forwarded-for": "1.1.1.1"})
        b = await client.get("/api/state", headers={"x-forwarded-for": "2.2.2.2"})

    assert a.status_code == 200
    assert b.status_code == 200
