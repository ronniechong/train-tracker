import hashlib
from datetime import datetime, timezone

import httpx
import pytest
from google.transit import gtfs_realtime_pb2

from traintracker.gateway.client import Feed, GatewayClient
from traintracker.gtfs.gtfstime import service_date_for_instant
from traintracker.gtfs.pinning import PinManifest
from traintracker.gtfs.schedule_cache import PinnedScheduleCache
from traintracker.poller.breaker import CircuitBreaker
from traintracker.poller.loop import PollerLoop
from traintracker.state.eventhub import InProcessEventHub
from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.store import StateStore
from traintracker.vline_poller.app import _event_source, create_vline_app


def _pinned_schedule_cache(tmp_path, sample_static_zip_bytes) -> PinnedScheduleCache:
    """Same fixture-construction pattern as `api/test_app.py`'s own
    `_pinned_schedule_cache` -- a real cache over the shared static-GTFS
    sample, pinned to today (the route handler calls `datetime.now()`
    internally, not injectable)."""
    digest = hashlib.sha256(sample_static_zip_bytes).hexdigest()
    (tmp_path / f"{digest}.zip").write_bytes(sample_static_zip_bytes)
    manifest = PinManifest(tmp_path / "pin_manifest.json")
    manifest.pin_digest(service_date_for_instant(datetime.now(timezone.utc)), digest)
    return PinnedScheduleCache(tmp_path, manifest)


def _tu_bytes(timestamp: int, trip_id: str = "V1") -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    entity = feed.entity.add()
    entity.id = "tu1"
    entity.trip_update.trip.trip_id = trip_id
    entity.trip_update.trip.route_id = "R1"
    return feed.SerializeToString()


def _vp_bytes(timestamp: int, trip_id: str = "V1") -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    entity = feed.entity.add()
    entity.id = "vp1"
    entity.vehicle.trip.trip_id = trip_id
    entity.vehicle.position.latitude = -37.5
    entity.vehicle.position.longitude = 144.0
    entity.vehicle.timestamp = timestamp
    return feed.SerializeToString()


async def _running_vline_loop() -> tuple[PollerLoop, StateStore]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "trip-updates" in path:
            return httpx.Response(200, content=_tu_bytes(1000))
        if "vehicle-positions" in path:
            return httpx.Response(200, content=_vp_bytes(1000))
        raise AssertionError(f"V/Line poller must never request {path} (no Service Alerts)")

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
        feeds=(Feed.TRIP_UPDATES, Feed.VEHICLE_POSITIONS),
    )
    await loop.run_cycle(datetime.now(timezone.utc))
    return loop, store


async def _client_for(
    loop: PollerLoop, store: StateStore, schedule_cache: PinnedScheduleCache | None = None
) -> httpx.AsyncClient:
    app = create_vline_app(loop=loop, store=store, hub=InProcessEventHub(), schedule_cache=schedule_cache)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_healthz_ok():
    loop, store = await _running_vline_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_state_reports_only_the_two_vline_feeds_no_service_alerts():
    loop, store = await _running_vline_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/api/vline/state")

    assert response.status_code == 200
    body = response.json()
    assert set(body["feeds"]) == {"trip-updates", "vehicle-positions"}
    assert [t["trip_id"] for t in body["trains"]] == ["V1"]


async def test_no_alerts_route_exists():
    """V/Line has no Service Alerts feed -- there must be no
    /api/vline/alerts route at all, not an empty one."""
    loop, store = await _running_vline_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/api/vline/alerts")
    assert response.status_code == 404


async def test_station_schedule_returns_503_when_not_configured():
    loop, store = await _running_vline_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/api/vline/stations/STATION_A/schedule")
    assert response.status_code == 503


async def test_station_schedule_returns_404_for_unknown_station(tmp_path, sample_static_zip_bytes):
    loop, store = await _running_vline_loop()
    schedule_cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)
    async with await _client_for(loop, store, schedule_cache=schedule_cache) as client:
        response = await client.get("/api/vline/stations/NOT_A_REAL_STATION/schedule")
    assert response.status_code == 404


async def test_station_schedule_returns_well_formed_response_for_known_station(
    tmp_path, sample_static_zip_bytes
):
    loop, store = await _running_vline_loop()
    schedule_cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)
    async with await _client_for(loop, store, schedule_cache=schedule_cache) as client:
        response = await client.get("/api/vline/stations/STATION_A/schedule")

    assert response.status_code == 200
    body = response.json()
    assert body["station_id"] == "STATION_A"
    assert isinstance(body["departures"], list)
    assert isinstance(body["lines_no_service_today"], list)
    assert "wheelchair_boarding" in body
    for train in body["departures"]:
        assert train["trip_id"]
        assert train["scheduled_time"]
        assert "platform_code" in train


async def test_event_source_emits_initial_snapshot_then_stops_on_disconnect():
    """Drives `_event_source` directly, not through a live ASGI connection
    — httpx's `ASGITransport` fully awaits an ASGI app to completion before
    returning anything, so it can't drive an infinite SSE generator (same
    reason `api/app.py`'s own `_event_source` is tested this way)."""
    loop, store = await _running_vline_loop()
    hub = InProcessEventHub()
    disconnected = False

    async def is_disconnected() -> bool:
        return disconnected

    gen = _event_source(loop, store, hub, is_disconnected, heartbeat_interval_s=20.0, schedule_cache=None)
    first = await anext(gen)
    assert first.startswith("event: snapshot\n")

    disconnected = True
    with pytest.raises(StopAsyncIteration):
        await anext(gen)
