import hashlib
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from google.transit import gtfs_realtime_pb2
from prometheus_client import CollectorRegistry

from traintracker.ai.budget import BudgetExceededError
from traintracker.ai.llm_client import LLMResponse
from traintracker.ai.tools import ToolContext
from traintracker.api.app import _event_source, _scheduled_train, _train, create_app
from traintracker.api.limits import ConnectionTracker, RateLimiter
from traintracker.digests.store import LineStat, WeeklyDigestRecord, WeeklyDigestStore
from traintracker.gateway.client import GatewayClient
from traintracker.gtfs.gtfstime import service_date_for_instant
from traintracker.gtfs.pinning import PinManifest
from traintracker.gtfs.schedule import ScheduledDeparture
from traintracker.gtfs.stops import Stop
from traintracker.gtfs.schedule_cache import PinnedScheduleCache
from traintracker.metrics import Metrics
from traintracker.poller.breaker import CircuitBreaker
from traintracker.poller.loop import PollerLoop
from traintracker.state.alerts import ActivePeriod, Alert, InformedEntity
from traintracker.state.eventhub import InProcessEventHub
from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.ghost import TrackedTrainView
from traintracker.state.merge import StopTimeUpdate, TrainSnapshot
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
    left in `store.latest_snapshots` at all. A fully vanished train must
    still show up in `/api/state`, not silently disappear."""
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
    schedule_cache: PinnedScheduleCache | None = None,
    ai_client=None,
    ai_tool_context=None,
    ai_notify_client=None,
    metrics=None,
    digest_store=None,
    briefing_token=None,
    archive_status_path=None,
) -> httpx.AsyncClient:
    app = create_app(
        loop=loop,
        store=store,
        hub=hub or InProcessEventHub(),
        connections=connections,
        rate_limiter=rate_limiter,
        heartbeat_interval_s=heartbeat_interval_s,
        schedule_cache=schedule_cache,
        ai_client=ai_client,
        ai_tool_context=ai_tool_context,
        ai_notify_client=ai_notify_client,
        metrics=metrics,
        digest_store=digest_store,
        briefing_token=briefing_token,
        archive_status_path=archive_status_path,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _pinned_schedule_cache(tmp_path, sample_static_zip_bytes) -> PinnedScheduleCache:
    """A real `PinnedScheduleCache` over the shared `gtfs_static_sample`
    fixture, pinned to whatever service_date real wall-clock `now` resolves
    to -- the route handler itself calls `datetime.now()` internally (not
    injectable), so this pins "today" rather than a fixed date, same as
    `_running_loop`'s own "real now, not a fixed T0" convention above."""
    digest = hashlib.sha256(sample_static_zip_bytes).hexdigest()
    (tmp_path / f"{digest}.zip").write_bytes(sample_static_zip_bytes)
    manifest = PinManifest(tmp_path / "pin_manifest.json")
    manifest.pin_digest(service_date_for_instant(datetime.now(timezone.utc)), digest)
    return PinnedScheduleCache(tmp_path, manifest)


async def test_attribution_returns_cc_by_credit():
    loop, store = await _running_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/attribution")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "Victoria Department of Transport and Planning"
    assert body["license"] == "CC BY 4.0"
    assert body["license_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert "derived" in body["note"]


async def test_archive_status_returns_503_when_not_configured():
    loop, store = await _running_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/archive/status")

    assert response.status_code == 503


async def test_archive_status_returns_503_when_file_missing(tmp_path):
    loop, store = await _running_loop()
    missing_path = tmp_path / "public_status.json"
    async with await _client_for(loop, store, archive_status_path=missing_path) as client:
        response = await client.get("/archive/status")

    assert response.status_code == 503


async def test_archive_status_returns_last_archived_date(tmp_path):
    loop, store = await _running_loop()
    status_path = tmp_path / "public_status.json"
    status_path.write_text('{"last_archived_date": "2026-08-13", "updated_at": "2026-08-14T03:30:00+00:00"}')
    async with await _client_for(loop, store, archive_status_path=status_path) as client:
        response = await client.get("/archive/status")

    assert response.status_code == 200
    assert response.json() == {"last_archived_date": "2026-08-13"}


async def test_archive_status_returns_null_before_first_successful_pass(tmp_path):
    loop, store = await _running_loop()
    status_path = tmp_path / "public_status.json"
    status_path.write_text('{"last_archived_date": null, "updated_at": "2026-08-14T03:30:00+00:00"}')
    async with await _client_for(loop, store, archive_status_path=status_path) as client:
        response = await client.get("/archive/status")

    assert response.status_code == 200
    assert response.json() == {"last_archived_date": None}


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
    monkeypatch.setenv("TT_CORS_ORIGINS", "https://example.com")
    loop, store = await _running_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/api/state", headers={"Origin": "https://evil.invalid"})

    # CORSMiddleware doesn't block the request itself, it withholds the
    # allow-origin header -- the browser is what actually enforces this.
    assert "access-control-allow-origin" not in response.headers


async def test_cors_allows_configured_origin(monkeypatch):
    monkeypatch.setenv("TT_CORS_ORIGINS", "https://example.com")
    loop, store = await _running_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/api/state", headers={"Origin": "https://example.com"})

    assert response.headers["access-control-allow-origin"] == "https://example.com"


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
    test -- a known coverage gap."""
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


def _departure(**overrides) -> ScheduledDeparture:
    defaults = dict(
        trip_id="T1",
        route_id="R1",
        direction_id=0,
        headsign="Pakenham",
        scheduled_time=datetime(2026, 7, 20, 22, 0, tzinfo=timezone.utc),
        stop_id="PLAT_A1",
    )
    defaults.update(overrides)
    return ScheduledDeparture(**defaults)


def _empty_store() -> StateStore:
    return StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())


def test_scheduled_train_looks_up_platform_code_from_departures_stop_id():
    stop = Stop(
        stop_id="PLAT_A1",
        name="Richmond Platform 1",
        latitude=-37.82,
        longitude=144.99,
        parent_station="STA_RICH",
        platform_code="1",
    )

    train = _scheduled_train(_empty_store(), _departure(stop_id="PLAT_A1"), {"PLAT_A1": stop})

    assert train.platform_code == "1"


def test_scheduled_train_platform_code_is_none_when_stop_unknown():
    train = _scheduled_train(_empty_store(), _departure(stop_id="PLAT_A1"), {})

    assert train.platform_code is None


def test_scheduled_train_is_schedule_only_when_no_live_snapshot():
    train = _scheduled_train(_empty_store(), _departure(), {})

    assert train.is_live is False
    assert train.is_cancelled is False
    assert train.predicted_time is None
    assert train.delay_seconds is None
    assert train.scheduled_time == datetime(2026, 7, 20, 22, 0, tzinfo=timezone.utc)


def test_scheduled_train_overlays_live_predicted_time_and_delay():
    # departure_time is a STRING here, not an int -- matches the real
    # runtime shape (protobuf's JSON mapping stringifies int64 fields),
    # despite StopTimeUpdate's own `int | None` type hint. A plain
    # `datetime.fromtimestamp(time_epoch, ...)` crashes with "'str' object
    # cannot be interpreted as an integer" against real data --
    # state/station.py has an `_epoch()` helper for this reason.
    store = _empty_store()
    predicted_at = datetime(2026, 7, 20, 22, 4, tzinfo=timezone.utc)
    store.latest_snapshots["T1"] = TrainSnapshot(
        trip_id="T1",
        route_id="R1",
        start_time=None,
        start_date=None,
        schedule_relationship=None,
        stop_time_updates=(
            StopTimeUpdate(
                stop_sequence=1,
                stop_id="PLAT_A1",
                arrival_delay=None,
                arrival_time=None,
                departure_delay=240,
                departure_time=str(int(predicted_at.timestamp())),
                schedule_relationship=None,
            ),
        ),
        schedule_updated_at=datetime.now(timezone.utc),
        latitude=None,
        longitude=None,
        bearing=None,
        position_updated_at=None,
    )

    train = _scheduled_train(store, _departure(), {})

    assert train.is_live is True
    assert train.is_cancelled is False
    assert train.delay_seconds == 240
    assert train.predicted_time == predicted_at


def test_scheduled_train_falls_back_to_delay_only_when_no_predicted_time():
    store = _empty_store()
    store.latest_snapshots["T1"] = TrainSnapshot(
        trip_id="T1",
        route_id="R1",
        start_time=None,
        start_date=None,
        schedule_relationship=None,
        stop_time_updates=(
            StopTimeUpdate(
                stop_sequence=1,
                stop_id="PLAT_A1",
                arrival_delay=None,
                arrival_time=None,
                departure_delay=90,
                departure_time=None,
                schedule_relationship=None,
            ),
        ),
        schedule_updated_at=datetime.now(timezone.utc),
        latitude=None,
        longitude=None,
        bearing=None,
        position_updated_at=None,
    )
    dep = _departure()

    train = _scheduled_train(store, dep, {})

    assert train.is_live is True
    assert train.delay_seconds == 90
    assert train.predicted_time == dep.scheduled_time + timedelta(seconds=90)


def test_scheduled_train_ignores_snapshot_for_a_different_platform():
    store = _empty_store()
    store.latest_snapshots["T1"] = TrainSnapshot(
        trip_id="T1",
        route_id="R1",
        start_time=None,
        start_date=None,
        schedule_relationship=None,
        stop_time_updates=(
            StopTimeUpdate(
                stop_sequence=1,
                stop_id="SOME_OTHER_PLATFORM",
                arrival_delay=30,
                arrival_time=None,
                departure_delay=30,
                departure_time=None,
                schedule_relationship=None,
            ),
        ),
        schedule_updated_at=datetime.now(timezone.utc),
        latitude=None,
        longitude=None,
        bearing=None,
        position_updated_at=None,
    )

    train = _scheduled_train(store, _departure(stop_id="PLAT_A1"), {})

    assert train.is_live is False


def test_scheduled_train_is_cancelled_for_a_whole_trip_cancellation():
    # TU's trip-level `schedule_relationship` (not the per-stop one below) --
    # the entire trip is off, regardless of what any individual
    # stop_time_update says.
    store = _empty_store()
    store.latest_snapshots["T1"] = TrainSnapshot(
        trip_id="T1",
        route_id="R1",
        start_time=None,
        start_date=None,
        schedule_relationship="CANCELED",
        stop_time_updates=(),
        schedule_updated_at=datetime.now(timezone.utc),
        latitude=None,
        longitude=None,
        bearing=None,
        position_updated_at=None,
    )

    train = _scheduled_train(store, _departure(), {})

    assert train.is_cancelled is True


def test_scheduled_train_is_cancelled_for_a_skipped_stop():
    # A train that still runs but skips THIS platform -- the trip
    # itself is "SCHEDULED", only this stop_time_update is "SKIPPED".
    store = _empty_store()
    store.latest_snapshots["T1"] = TrainSnapshot(
        trip_id="T1",
        route_id="R1",
        start_time=None,
        start_date=None,
        schedule_relationship="SCHEDULED",
        stop_time_updates=(
            StopTimeUpdate(
                stop_sequence=1,
                stop_id="PLAT_A1",
                arrival_delay=None,
                arrival_time=None,
                departure_delay=None,
                departure_time=None,
                schedule_relationship="SKIPPED",
            ),
        ),
        schedule_updated_at=datetime.now(timezone.utc),
        latitude=None,
        longitude=None,
        bearing=None,
        position_updated_at=None,
    )

    train = _scheduled_train(store, _departure(stop_id="PLAT_A1"), {})

    assert train.is_cancelled is True


async def test_station_schedule_returns_503_when_not_configured():
    loop, store = await _running_loop()
    async with await _client_for(loop, store) as client:
        response = await client.get("/stations/STATION_A/schedule")

    assert response.status_code == 503


async def test_station_schedule_returns_404_for_unknown_station(tmp_path, sample_static_zip_bytes):
    loop, store = await _running_loop()
    schedule_cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)

    async with await _client_for(loop, store, schedule_cache=schedule_cache) as client:
        response = await client.get("/stations/NOT_A_REAL_STATION/schedule")

    assert response.status_code == 404


async def test_station_schedule_returns_well_formed_response_for_known_station(
    tmp_path, sample_static_zip_bytes
):
    loop, store = await _running_loop()
    schedule_cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)

    async with await _client_for(loop, store, schedule_cache=schedule_cache) as client:
        response = await client.get("/stations/STATION_A/schedule")

    assert response.status_code == 200
    body = response.json()
    assert body["station_id"] == "STATION_A"
    assert isinstance(body["departures"], list)
    assert isinstance(body["lines_no_service_today"], list)
    assert "wheelchair_boarding" in body
    for train in body["departures"]:
        assert "platform_code" in train
    # Whether any departures are actually present depends on the real time
    # of day vs. the fixture's fixed ~08-09am schedule -- see
    # gtfs/test_schedule.py's next_departures tests (fixed `after` values)
    # for that coverage. This only verifies the route wires
    # station_id -> cache -> response correctly and every entry matches
    # the schema.
    for train in body["departures"]:
        assert train["trip_id"]
        assert train["scheduled_time"]


def test_train_reports_next_stop_and_delay_from_live_snapshot():
    # `_train` wires `state/station.py`'s `next_stop_and_delay` + the
    # caller-supplied stops dict into the public Train shape (M12 #2).
    from traintracker.gtfs.stops import Stop

    now = datetime.fromtimestamp(1050, tz=timezone.utc)
    store = _empty_store()
    store.latest_snapshots["T1"] = TrainSnapshot(
        trip_id="T1",
        route_id="R1",
        start_time="19:00:00",
        start_date="20260718",
        schedule_relationship="SCHEDULED",
        stop_time_updates=(
            StopTimeUpdate(
                stop_sequence=1, stop_id="A", arrival_delay=None, arrival_time=None,
                departure_delay=None, departure_time="1000", schedule_relationship="SCHEDULED",
            ),
            StopTimeUpdate(
                stop_sequence=2, stop_id="B", arrival_delay=90, arrival_time="1100",
                departure_delay=60, departure_time="1120", schedule_relationship="SCHEDULED",
            ),
        ),
        schedule_updated_at=now,
        latitude=-37.81, longitude=144.96, bearing=90.0, position_updated_at=now,
    )
    tracked = TrackedTrainView(
        trip_id="T1", status="live", last_seen_at=now, last_position=(-37.81, 144.96), last_touched_at=now,
    )
    stops = {"B": Stop("B", "B Station", -37.810, 144.950)}

    train = _train(store, tracked, schedule_cache=None, now=now, stops=stops)

    assert train.next_stop_id == "B"
    assert train.next_stop_name == "B Station"
    assert train.next_stop_delay_seconds == 90


def test_train_next_stop_is_none_when_stops_dict_not_supplied():
    now = datetime.fromtimestamp(1050, tz=timezone.utc)
    store = _empty_store()
    store.latest_snapshots["T1"] = TrainSnapshot(
        trip_id="T1", route_id="R1", start_time=None, start_date=None,
        schedule_relationship="SCHEDULED", stop_time_updates=(), schedule_updated_at=now,
        latitude=None, longitude=None, bearing=None, position_updated_at=None,
    )
    tracked = TrackedTrainView(
        trip_id="T1", status="live", last_seen_at=now, last_position=None, last_touched_at=now,
    )

    train = _train(store, tracked, schedule_cache=None, now=now, stops=None)

    assert train.next_stop_id is None
    assert train.next_stop_name is None
    assert train.next_stop_delay_seconds is None


def test_scheduled_train_is_added_for_a_real_time_only_trip():
    # TU schedule_relationship ADDED means a real-time-only
    # extra service (no static row) -- `_scheduled_train` reads it off the
    # same live-snapshot lookup `is_cancelled` already uses.
    store = _empty_store()
    store.latest_snapshots["EXTRA1"] = TrainSnapshot(
        trip_id="EXTRA1",
        route_id="R1",
        start_time=None,
        start_date=None,
        schedule_relationship="ADDED",
        stop_time_updates=(),
        schedule_updated_at=datetime.now(timezone.utc),
        latitude=None,
        longitude=None,
        bearing=None,
        position_updated_at=None,
    )

    train = _scheduled_train(store, _departure(trip_id="EXTRA1"), {})

    assert train.is_added is True
    assert train.is_cancelled is False


async def test_station_schedule_folds_in_added_trip_via_live_snapshots(
    tmp_path, sample_static_zip_bytes
):
    loop, store = await _running_loop()
    schedule_cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)
    now = datetime.now(timezone.utc)
    departs_in_5_min = str(int((now + timedelta(minutes=5)).timestamp()))
    store.latest_snapshots["EXTRA1"] = TrainSnapshot(
        trip_id="EXTRA1",
        route_id="R1",
        start_time=None,
        start_date=None,
        schedule_relationship="ADDED",
        stop_time_updates=(
            StopTimeUpdate(
                stop_sequence=1,
                stop_id="PLAT_A1",
                arrival_delay=None,
                arrival_time=None,
                departure_delay=None,
                departure_time=departs_in_5_min,
                schedule_relationship="SCHEDULED",
            ),
            StopTimeUpdate(
                stop_sequence=2,
                stop_id="PLAT_B1",
                arrival_delay=None,
                arrival_time=departs_in_5_min,
                departure_delay=None,
                departure_time=None,
                schedule_relationship="SCHEDULED",
            ),
        ),
        schedule_updated_at=now,
        latitude=None,
        longitude=None,
        bearing=None,
        position_updated_at=None,
    )

    async with await _client_for(loop, store, schedule_cache=schedule_cache) as client:
        response = await client.get("/stations/STATION_A/schedule")

    assert response.status_code == 200
    departures = response.json()["departures"]
    extra = next(d for d in departures if d["trip_id"] == "EXTRA1")
    assert extra["is_added"] is True
    assert extra["headsign"] == "B Station Platform 1"
    assert extra["is_live"] is True


async def test_get_alerts_returns_currently_active_alerts():
    loop, store = await _running_loop()
    now = datetime.now(timezone.utc)
    store.latest_alerts = {
        "active-alert": Alert(
            id="active-alert",
            cause="CONSTRUCTION",
            effect="MODIFIED_SERVICE",
            header_text="Buses replace trains",
            description_text="Details",
            url="https://example.invalid/d/1",
            active_periods=(),  # no active_period => always active
            informed_entities=(InformedEntity(route_id="R1", stop_id=None, direction_id=None),),
        ),
        "expired-alert": Alert(
            id="expired-alert",
            cause="OTHER_CAUSE",
            effect="OTHER_EFFECT",
            header_text="Old disruption",
            description_text=None,
            url=None,
            active_periods=(
                ActivePeriod(
                    start=now - timedelta(days=2), end=now - timedelta(days=1)
                ),
            ),
            informed_entities=(),
        ),
    }

    async with await _client_for(loop, store) as client:
        response = await client.get("/api/alerts")

    assert response.status_code == 200
    body = response.json()
    ids = {a["id"] for a in body["alerts"]}
    assert ids == {"active-alert"}
    alert = body["alerts"][0]
    assert alert["header_text"] == "Buses replace trains"
    assert alert["informed_entities"] == [
        {"route_id": "R1", "route_name": None, "stop_id": None, "direction_id": None}
    ]


async def test_get_alerts_resolves_route_name_from_pinned_schedule(tmp_path, sample_static_zip_bytes):
    loop, store = await _running_loop()
    store.latest_alerts = {
        "active-alert": Alert(
            id="active-alert",
            cause="CONSTRUCTION",
            effect="MODIFIED_SERVICE",
            header_text="Buses replace trains",
            description_text="Details",
            url=None,
            active_periods=(),
            informed_entities=(InformedEntity(route_id="2-PKM", stop_id=None, direction_id=None),),
        ),
    }
    schedule_cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)

    async with await _client_for(loop, store, schedule_cache=schedule_cache) as client:
        response = await client.get("/api/alerts")

    body = response.json()
    assert body["alerts"][0]["informed_entities"][0]["route_name"] == "Pakenham - City"


async def test_get_alerts_leaves_route_name_none_for_ambiguous_stop_only_entities(
    tmp_path, sample_static_zip_bytes
):
    # Real-world shape: a single-trip cancellation lists only stop_ids, no
    # route_id at all. PLAT_A1/PLAT_B1
    # are shared by both fixture routes (2-PKM, 2-CRB) with no majority
    # either way, so this must stay None rather than guess -- see
    # routes_most_likely_for_stops's docstring.
    loop, store = await _running_loop()
    store.latest_alerts = {
        "active-alert": Alert(
            id="active-alert",
            cause="OTHER_CAUSE",
            effect="NO_SERVICE",
            header_text="Cancellation",
            description_text=None,
            url=None,
            active_periods=(),
            informed_entities=(
                InformedEntity(route_id=None, stop_id="PLAT_A1", direction_id=None),
                InformedEntity(route_id=None, stop_id="PLAT_B1", direction_id=None),
            ),
        ),
    }
    schedule_cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)

    async with await _client_for(loop, store, schedule_cache=schedule_cache) as client:
        response = await client.get("/api/alerts")

    body = response.json()
    names = {e["route_name"] for e in body["alerts"][0]["informed_entities"]}
    assert names == {None}


async def test_get_alerts_resolves_route_name_by_majority_for_stop_only_entities(tmp_path):
    # Same real-world shape as the majority-vote unit tests in
    # test_schedule_cache.py: 2 of 3 listed stops are A-only, 1 is shared
    # with B -- majority still resolves A end-to-end through the API.
    from tests.gtfs.test_schedule_cache import _MAJORITY_VOTE_FILES, _pinned_cache_from_files

    loop, store = await _running_loop()
    store.latest_alerts = {
        "active-alert": Alert(
            id="active-alert",
            cause="OTHER_CAUSE",
            effect="NO_SERVICE",
            header_text="Cancellation",
            description_text=None,
            url=None,
            active_periods=(),
            informed_entities=(
                InformedEntity(route_id=None, stop_id="STOP_1", direction_id=None),
                InformedEntity(route_id=None, stop_id="STOP_2", direction_id=None),
                InformedEntity(route_id=None, stop_id="STOP_3", direction_id=None),
            ),
        ),
    }
    schedule_cache = _pinned_cache_from_files(tmp_path, _MAJORITY_VOTE_FILES)

    async with await _client_for(loop, store, schedule_cache=schedule_cache) as client:
        response = await client.get("/api/alerts")

    body = response.json()
    names = {e["route_name"] for e in body["alerts"][0]["informed_entities"]}
    assert names == {"A - City"}


async def test_get_alerts_filters_by_route_id():
    loop, store = await _running_loop()
    store.latest_alerts = {
        "on-r1": Alert(
            id="on-r1", cause=None, effect=None, header_text=None, description_text=None,
            url=None, active_periods=(),
            informed_entities=(InformedEntity(route_id="R1", stop_id=None, direction_id=None),),
        ),
        "on-r2": Alert(
            id="on-r2", cause=None, effect=None, header_text=None, description_text=None,
            url=None, active_periods=(),
            informed_entities=(InformedEntity(route_id="R2", stop_id=None, direction_id=None),),
        ),
    }

    async with await _client_for(loop, store) as client:
        response = await client.get("/api/alerts", params={"route_id": "R1"})

    assert response.status_code == 200
    assert {a["id"] for a in response.json()["alerts"]} == {"on-r1"}


class _ScriptedLLMClient:
    """Not a real tool-calling loop -- returns `end_turn` immediately, same
    minimal shape `tests/ai/test_briefing.py` already uses, sufficient for
    exercising the route's own sent/reason branching rather than the agent
    loop itself (covered separately)."""

    def __init__(self, text: str):
        self._text = text
        self.calls = 0

    async def complete(self, *, system, messages, tools=None, max_tokens):
        self.calls += 1
        return LLMResponse(text=self._text, tool_uses=(), stop_reason="end_turn", input_tokens=1, output_tokens=1)


class _RaisingLLMClient:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def complete(self, *, system, messages, tools=None, max_tokens):
        raise self._exc


def _briefable_alert() -> Alert:
    return Alert(
        id="A1", cause="OTHER_CAUSE", effect="SIGNIFICANT_DELAYS", header_text="Major Delay",
        description_text=None, url=None, active_periods=(),
        informed_entities=(InformedEntity(route_id="2-BEG", stop_id=None, direction_id=None),),
    )


async def test_trigger_briefing_returns_503_when_ai_stack_not_configured():
    loop, store = await _running_loop()
    async with await _client_for(loop, store) as client:  # no ai_* kwargs
        response = await client.post("/briefing/trigger")

    assert response.status_code == 503


async def test_trigger_briefing_skips_the_llm_when_no_briefable_alerts():
    loop, store = await _running_loop()
    ai_client = _ScriptedLLMClient("should never be produced")

    async with await _client_for(
        loop, store, ai_client=ai_client, ai_tool_context=ToolContext(store=store, schedule_cache=None),
        ai_notify_client=httpx.AsyncClient(),
    ) as client:
        response = await client.post("/briefing/trigger")

    assert response.status_code == 200
    body = response.json()
    assert body["sent"] is False
    assert "route/line" in body["reason"]
    assert ai_client.calls == 0


async def test_trigger_briefing_sends_and_records_the_metric_on_success(monkeypatch):
    loop, store = await _running_loop()
    store.latest_alerts = {"A1": _briefable_alert()}
    ai_client = _ScriptedLLMClient("Belgrave line: major delays due to a signal fault.")
    registry = CollectorRegistry()
    metrics = Metrics(registry)

    async def _fake_post_message(client, text, webhook_url=None):
        return True

    monkeypatch.setattr("traintracker.api.app.post_message", _fake_post_message)

    async with await _client_for(
        loop, store, ai_client=ai_client, ai_tool_context=ToolContext(store=store, schedule_cache=None),
        ai_notify_client=httpx.AsyncClient(), metrics=metrics,
    ) as client:
        response = await client.post("/briefing/trigger")

    assert response.status_code == 200
    body = response.json()
    assert body["sent"] is True
    assert body["text"] == "Belgrave line: major delays due to a signal fault."
    assert ai_client.calls == 1
    assert registry.get_sample_value("traintracker_briefings_sent_total") == 1.0


async def test_trigger_briefing_reports_slack_failure_without_crashing(monkeypatch):
    loop, store = await _running_loop()
    store.latest_alerts = {"A1": _briefable_alert()}
    ai_client = _ScriptedLLMClient("Belgrave line: major delays due to a signal fault.")
    registry = CollectorRegistry()
    metrics = Metrics(registry)

    async def _fake_post_message(client, text, webhook_url=None):
        return False  # e.g. webhook not configured / Slack outage

    monkeypatch.setattr("traintracker.api.app.post_message", _fake_post_message)

    async with await _client_for(
        loop, store, ai_client=ai_client, ai_tool_context=ToolContext(store=store, schedule_cache=None),
        ai_notify_client=httpx.AsyncClient(), metrics=metrics,
    ) as client:
        response = await client.post("/briefing/trigger")

    assert response.status_code == 200
    body = response.json()
    assert body["sent"] is False
    assert "Slack" in body["reason"]
    assert registry.get_sample_value("traintracker_briefings_sent_total") == 0.0  # never counted as sent


async def test_trigger_briefing_reports_budget_exceeded_without_calling_slack(monkeypatch):
    loop, store = await _running_loop()
    store.latest_alerts = {"A1": _briefable_alert()}
    ai_client = _RaisingLLMClient(BudgetExceededError("monthly AI budget ($20.00) reached for 2026-08"))
    posted = []

    async def _fake_post_message(client, text, webhook_url=None):
        posted.append(text)
        return True

    monkeypatch.setattr("traintracker.api.app.post_message", _fake_post_message)

    async with await _client_for(
        loop, store, ai_client=ai_client, ai_tool_context=ToolContext(store=store, schedule_cache=None),
        ai_notify_client=httpx.AsyncClient(),
    ) as client:
        response = await client.post("/briefing/trigger")

    assert response.status_code == 200
    body = response.json()
    assert body["sent"] is False
    assert "budget" in body["reason"].lower()
    assert posted == []


async def test_trigger_briefing_rejects_missing_bearer_token_when_configured():
    loop, store = await _running_loop()
    ai_client = _ScriptedLLMClient("should never be produced")

    async with await _client_for(
        loop, store, ai_client=ai_client, ai_tool_context=ToolContext(store=store, schedule_cache=None),
        ai_notify_client=httpx.AsyncClient(), briefing_token="s3cret",
    ) as client:
        response = await client.post("/briefing/trigger")

    assert response.status_code == 401
    assert ai_client.calls == 0


async def test_trigger_briefing_rejects_wrong_bearer_token():
    loop, store = await _running_loop()
    ai_client = _ScriptedLLMClient("should never be produced")

    async with await _client_for(
        loop, store, ai_client=ai_client, ai_tool_context=ToolContext(store=store, schedule_cache=None),
        ai_notify_client=httpx.AsyncClient(), briefing_token="s3cret",
    ) as client:
        response = await client.post(
            "/briefing/trigger", headers={"Authorization": "Bearer wrong"}
        )

    assert response.status_code == 401
    assert ai_client.calls == 0


async def test_trigger_briefing_accepts_correct_bearer_token():
    loop, store = await _running_loop()
    store.latest_alerts = {"A1": _briefable_alert()}
    ai_client = _ScriptedLLMClient("Belgrave line: major delays due to a signal fault.")

    async with await _client_for(
        loop, store, ai_client=ai_client, ai_tool_context=ToolContext(store=store, schedule_cache=None),
        ai_notify_client=httpx.AsyncClient(), briefing_token="s3cret",
    ) as client:
        response = await client.post(
            "/briefing/trigger", headers={"Authorization": "Bearer s3cret"}
        )

    assert response.status_code == 200
    assert ai_client.calls == 1


async def test_trigger_briefing_reports_a_generic_failure_without_leaking_internals(monkeypatch):
    loop, store = await _running_loop()
    store.latest_alerts = {"A1": _briefable_alert()}
    ai_client = _RaisingLLMClient(RuntimeError("some internal detail that shouldn't reach the client"))

    async with await _client_for(
        loop, store, ai_client=ai_client, ai_tool_context=ToolContext(store=store, schedule_cache=None),
        ai_notify_client=httpx.AsyncClient(),
    ) as client:
        response = await client.post("/briefing/trigger")

    assert response.status_code == 200
    body = response.json()
    assert body["sent"] is False
    assert "some internal detail" not in body["reason"]


def _digest_store(tmp_path):
    store = WeeklyDigestStore(tmp_path / "weekly.db")
    store.record(
        WeeklyDigestRecord(
            week_start=date(2026, 7, 27), week_end=date(2026, 8, 2), days_covered=7,
            on_time_count=305, late_count=6, cancelled_count=0, on_time_pct=98.07,
            narrative="A solid week overall.", slack_delivered=True,
            line_stats=(
                LineStat(
                    route_id="2-BEG", trip_count=25, on_time_count=20, late_count=5,
                    cancelled_count=0, on_time_pct=80.0,
                ),
            ),
        )
    )
    return store


async def test_weekly_digests_returns_503_when_not_configured():
    loop, store = await _running_loop()
    async with await _client_for(loop, store) as client:  # no digest_store kwarg
        response = await client.get("/digests/weekly")

    assert response.status_code == 503


async def test_weekly_digests_returns_the_stored_list(tmp_path):
    loop, store = await _running_loop()
    digest_store = _digest_store(tmp_path)

    async with await _client_for(loop, store, digest_store=digest_store) as client:
        response = await client.get("/digests/weekly")

    assert response.status_code == 200
    body = response.json()
    assert len(body["digests"]) == 1
    digest = body["digests"][0]
    assert digest["week_start"] == "2026-07-27"
    assert digest["week_end"] == "2026-08-02"
    assert digest["days_covered"] == 7
    assert digest["on_time_count"] == 305
    assert digest["late_count"] == 6
    assert digest["cancelled_count"] == 0
    assert digest["on_time_pct"] == 98.07
    assert digest["narrative"] == "A solid week overall."
    assert digest["slack_delivered"] is True
    assert digest["line_stats"] == [
        {
            "route_id": "2-BEG", "trip_count": 25, "on_time_count": 20,
            "late_count": 5, "cancelled_count": 0, "on_time_pct": 80.0,
        }
    ]


async def test_weekly_digests_respects_the_limit_query_param(tmp_path):
    loop, store = await _running_loop()
    digest_store = _digest_store(tmp_path)
    digest_store.record(
        WeeklyDigestRecord(
            week_start=date(2026, 8, 3), week_end=date(2026, 8, 9), days_covered=7,
            on_time_count=310, late_count=4, cancelled_count=1, on_time_pct=98.73,
            narrative="Another good week.", slack_delivered=True, line_stats=(),
        )
    )

    async with await _client_for(loop, store, digest_store=digest_store) as client:
        response = await client.get("/digests/weekly", params={"limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert len(body["digests"]) == 1
    assert body["digests"][0]["week_start"] == "2026-08-03"  # most recent first


async def test_http_metrics_records_count_and_status_for_a_simple_route():
    loop, store = await _running_loop()
    registry = CollectorRegistry()
    metrics = Metrics(registry)

    async with await _client_for(loop, store, metrics=metrics) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert registry.get_sample_value(
        "traintracker_http_requests_total",
        {"route": "/healthz", "method": "GET", "status": "200"},
    ) == 1.0


async def test_http_metrics_records_duration():
    loop, store = await _running_loop()
    registry = CollectorRegistry()
    metrics = Metrics(registry)

    async with await _client_for(loop, store, metrics=metrics) as client:
        await client.get("/healthz")

    count = registry.get_sample_value(
        "traintracker_http_request_duration_seconds_count",
        {"route": "/healthz", "method": "GET"},
    )
    assert count == 1.0


async def test_http_metrics_uses_the_route_template_not_the_raw_path(tmp_path, sample_static_zip_bytes):
    # /stations/STATION_A/schedule must be labelled as the TEMPLATE
    # /stations/{station_id}/schedule -- a raw-path label would create
    # unbounded cardinality (one series per station ever queried).
    loop, store = await _running_loop()
    schedule_cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)
    registry = CollectorRegistry()
    metrics = Metrics(registry)

    async with await _client_for(loop, store, schedule_cache=schedule_cache, metrics=metrics) as client:
        await client.get("/stations/NOT_A_REAL_STATION/schedule")

    assert registry.get_sample_value(
        "traintracker_http_requests_total",
        {"route": "/stations/{station_id}/schedule", "method": "GET", "status": "404"},
    ) == 1.0
    assert registry.get_sample_value(
        "traintracker_http_requests_total",
        {"route": "/stations/NOT_A_REAL_STATION/schedule", "method": "GET", "status": "404"},
    ) is None


async def test_http_metrics_labels_a_genuinely_unmatched_path(tmp_path):
    loop, store = await _running_loop()
    registry = CollectorRegistry()
    metrics = Metrics(registry)

    async with await _client_for(loop, store, metrics=metrics) as client:
        response = await client.get("/this/route/does/not/exist")

    assert response.status_code == 404
    assert registry.get_sample_value(
        "traintracker_http_requests_total",
        {"route": "unmatched", "method": "GET", "status": "404"},
    ) == 1.0


async def test_http_metrics_is_a_noop_without_a_metrics_instance():
    # metrics=None (the default) -- must not raise, same "feature not
    # wired" convention as ai_client/digest_store elsewhere in this app.
    loop, store = await _running_loop()

    async with await _client_for(loop, store) as client:  # no metrics kwarg
        response = await client.get("/healthz")

    assert response.status_code == 200


# No end-to-end /api/stream HTTP test here -- `_event_source`'s own
# docstring documents why: httpx's ASGITransport fully awaits an ASGI app
# to completion before returning anything, so it cannot drive an infinite
# SSE generator at all (confirmed the hard way: an early version of this
# test hung indefinitely). `tests/api/test_http_metrics.py` verifies the
# middleware's no-buffering behavior directly against a scripted ASGI
# send sequence instead, without needing a real streaming transport.
