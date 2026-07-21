from datetime import datetime, timedelta, timezone

import httpx
import pytest
from google.transit import gtfs_realtime_pb2

from traintracker.gateway.client import GatewayClient
from traintracker.poller.breaker import CircuitBreaker
from traintracker.poller.loop import ALL_FEEDS, PollerLoop
from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.store import StateStore

T0 = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)  # daytime AEST, avoids overnight cadence


def _tu_bytes(timestamp: int, trip_id: str = "T1") -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    entity = feed.entity.add()
    entity.id = "tu1"
    entity.trip_update.trip.trip_id = trip_id
    entity.trip_update.trip.route_id = "R1"
    return feed.SerializeToString()


def _vp_bytes(timestamp: int, trip_id: str = "T1", lat: float = -37.81) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    entity = feed.entity.add()
    entity.id = "vp1"
    entity.vehicle.trip.trip_id = trip_id
    entity.vehicle.position.latitude = lat
    entity.vehicle.position.longitude = 144.96
    entity.vehicle.timestamp = timestamp
    return feed.SerializeToString()


def _sa_bytes(timestamp: int) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    return feed.SerializeToString()


class ScriptedGateway:
    """Drives a GatewayClient's underlying transport from a per-feed
    timestamp/status script the test controls between `run_cycle()` calls,
    without needing a real network or a real API key."""

    def __init__(self):
        self.tu_ts = 1000
        self.vp_ts = 1000
        self.sa_ts = 1000
        self.fail_feeds: set[str] = set()
        self.rate_limit_remaining: int | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if any(f in path for f in self.fail_feeds):
                return httpx.Response(500)
            headers = {}
            if self.rate_limit_remaining is not None:
                headers["x-rate-limit"] = (
                    f'[{{"window":0,"type":"throttle","remaining":{self.rate_limit_remaining}}}]'
                )
            if "trip-updates" in path:
                return httpx.Response(200, content=_tu_bytes(self.tu_ts), headers=headers)
            if "vehicle-positions" in path:
                return httpx.Response(200, content=_vp_bytes(self.vp_ts), headers=headers)
            if "service-alerts" in path:
                return httpx.Response(200, content=_sa_bytes(self.sa_ts), headers=headers)
            raise AssertionError(f"unexpected path {path}")

        self.client = GatewayClient(api_key="test-key")
        self.client._client = httpx.Client(transport=httpx.MockTransport(handler))


def _new_loop(scripted: ScriptedGateway) -> tuple[PollerLoop, StateStore]:
    store = StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())
    gap_log = InMemoryEventLog()
    healthcheck_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    loop = PollerLoop(
        gateway=scripted.client,
        store=store,
        gap_log=gap_log,
        breaker=CircuitBreaker(),
        healthcheck_client=healthcheck_client,
    )
    return loop, store


def test_successful_cycle_ingests_all_three_feeds_and_pings():
    scripted = ScriptedGateway()
    loop, store = _new_loop(scripted)

    result = loop.run_cycle(T0)

    assert result.ok is True
    assert result.changed_feeds == frozenset(ALL_FEEDS)
    assert "T1" in store.latest_snapshots
    assert store.status_of("T1") == "live"


def test_unchanged_header_is_deduped_but_store_still_ticks_forward():
    scripted = ScriptedGateway()
    loop, store = _new_loop(scripted)
    loop.run_cycle(T0)

    # Second cycle: nothing changed upstream (headers identical).
    result = loop.run_cycle(T0 + timedelta(seconds=10))

    assert result.changed_feeds == frozenset()
    # Cached content must still be re-ingested with the new cycle_time, not
    # dropped -- otherwise the trip would look VP-less and start coasting
    # even though nothing upstream actually changed.
    assert "T1" in store.latest_snapshots
    assert store.status_of("T1") == "live"


def test_coasting_advances_in_real_time_across_unchanged_cycles():
    scripted = ScriptedGateway()
    loop, store = _new_loop(scripted)
    loop.run_cycle(T0)

    # Trip vanishes from both feeds entirely (new timestamp, no entities).
    scripted.tu_ts = 2000
    scripted.vp_ts = 2000
    empty_tu = gtfs_realtime_pb2.FeedMessage()
    empty_tu.header.gtfs_realtime_version = "2.0"
    empty_tu.header.timestamp = 2000

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "trip-updates" in path or "vehicle-positions" in path:
            return httpx.Response(200, content=empty_tu.SerializeToString())
        return httpx.Response(200, content=_sa_bytes(2000))

    scripted.client._client = httpx.Client(transport=httpx.MockTransport(handler))

    loop.run_cycle(T0 + timedelta(seconds=100))  # past COASTING_TIMEOUT_S (90s)

    assert store.status_of("T1") == "ghost"


def test_request_failure_escalates_breaker_and_marks_cycle_not_ok():
    scripted = ScriptedGateway()
    scripted.fail_feeds = {"vehicle-positions"}
    loop, store = _new_loop(scripted)

    result = loop.run_cycle(T0)

    assert result.ok is False
    assert loop.breaker.backoff_active is True


def test_backoff_active_is_passed_through_to_state_store():
    scripted = ScriptedGateway()
    loop, store = _new_loop(scripted)
    loop.breaker.record_failure(T0)  # force backoff without needing a real failed fetch
    assert loop.breaker.backoff_active is True

    loop.run_cycle(T0 + timedelta(seconds=5))

    # A live trip must not start coasting/ghosting just because backoff is
    # active this tick (2d finding #6) -- confirmed indirectly: the trip
    # seen with a fresh position stays "live" even mid-backoff.
    assert store.status_of("T1") == "live"


def test_low_rate_limit_remaining_escalates_breaker_even_without_failures():
    scripted = ScriptedGateway()
    scripted.rate_limit_remaining = 1
    loop, _store = _new_loop(scripted)

    result = loop.run_cycle(T0)

    assert result.ok is True
    assert loop.breaker.backoff_active is True


def test_gap_episode_recorded_in_gap_log_on_recovery():
    scripted = ScriptedGateway()
    store = StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())
    gap_log = InMemoryEventLog()
    healthcheck_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    loop = PollerLoop(gateway=scripted.client, store=store, gap_log=gap_log, healthcheck_client=healthcheck_client)

    scripted.fail_feeds = {"vehicle-positions"}
    loop.run_cycle(T0)
    assert gap_log.events == []  # still escalating, not recovered yet

    scripted.fail_feeds = set()
    loop.run_cycle(T0 + timedelta(seconds=30))

    assert len(gap_log.events) == 1
    assert gap_log.events[0].reason == "circuit_breaker"


def test_auth_error_marks_cycle_failed_without_raising():
    scripted = ScriptedGateway()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, headers={"WWW-Authenticate": "irrelevant"})

    scripted.client._client = httpx.Client(transport=httpx.MockTransport(handler))
    loop, _store = _new_loop(scripted)

    result = loop.run_cycle(T0)

    assert result.ok is False
    assert loop.breaker.backoff_active is True


def test_next_interval_uses_breaker_ladder_during_backoff():
    scripted = ScriptedGateway()
    loop, _store = _new_loop(scripted)
    loop.breaker.record_failure(T0)

    interval = loop.next_interval(T0)

    assert interval > 15  # into the 30s rung, well above the ~10s base cadence


def test_next_interval_uses_service_hours_schedule_when_healthy():
    scripted = ScriptedGateway()
    loop, _store = _new_loop(scripted)

    interval = loop.next_interval(T0)  # T0 is daytime AEST

    assert 5 <= interval <= 15
