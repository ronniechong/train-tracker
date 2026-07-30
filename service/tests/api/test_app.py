from datetime import datetime, timezone

import httpx
from google.transit import gtfs_realtime_pb2

from traintracker.api.app import create_app
from traintracker.gateway.client import GatewayClient
from traintracker.poller.breaker import CircuitBreaker
from traintracker.poller.loop import PollerLoop
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


async def _client_for(loop: PollerLoop, store: StateStore) -> httpx.AsyncClient:
    app = create_app(loop=loop, store=store)
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
