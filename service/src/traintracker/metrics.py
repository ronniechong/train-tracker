"""Prometheus metrics — wiring, not designing: every metric here reads from
a schema or pure function built elsewhere. `EventLog` stays the seam:
`CountingEventLog` composes with whatever `EventLog` it's given (e.g.
`HistoryStore` facades), incrementing a counter then delegating —
`merge.py`/`ghost.py`/`breaker.py` need no changes at all.

`Metrics` takes an explicit `CollectorRegistry` (defaulting to
prometheus_client's global `REGISTRY`) rather than only ever using module-
level singleton metric objects, so tests can build an isolated instance per
test without tripping prometheus_client's "duplicated timeseries" error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

from .gateway.client import Feed
from .poller.breaker import CircuitBreaker
from .poller.loop import CycleResult
from .state.ghost import Status, TrackedTrainView

# Alert on feed header age, NEVER entity count -- this threshold is the
# only staleness signal built here. A legitimate zero-entity overnight
# cycle (header still advancing) is never read as staleness.
STALENESS_THRESHOLD_S = 300


class CountingEventLog:
    """`EventLog`-Protocol facade: increments `counter` then delegates to
    `inner`. `label_fn`, when given, maps the event to the label kwargs for
    this increment (e.g. ghost episodes labelled by `loop_contained`)."""

    def __init__(
        self,
        inner: object,
        counter: Counter,
        label_fn: Callable[[object], dict[str, str]] | None = None,
    ):
        self._inner = inner
        self._counter = counter
        self._label_fn = label_fn

    def record(self, event: object) -> None:
        if self._label_fn is not None:
            self._counter.labels(**self._label_fn(event)).inc()
        else:
            self._counter.inc()
        self._inner.record(event)


def _ghost_labels(event: object) -> dict[str, str]:
    return {"loop_contained": str(event.loop_contained).lower()}


def _completion_labels(event: object) -> dict[str, str]:
    return {"status": event.status}


class Metrics:
    def __init__(self, registry: CollectorRegistry = REGISTRY):
        self.poll_cycles_total = Counter(
            "traintracker_poll_cycles_total",
            "Poll cycles by result",
            ["result"],
            registry=registry,
        )
        self.discrepancy_events_total = Counter(
            "traintracker_discrepancy_events_total",
            "TU/VP discrepancies observed",
            registry=registry,
        )
        self.ghost_events_total = Counter(
            "traintracker_ghost_events_total",
            "Ghost episodes resolved, by whether both endpoints were "
            "City Loop-contained",
            ["loop_contained"],
            registry=registry,
        )
        self.poll_gap_events_total = Counter(
            "traintracker_poll_gap_events_total",
            "Circuit-breaker backoff episodes",
            registry=registry,
        )
        self.feed_last_changed_timestamp = Gauge(
            "traintracker_feed_last_changed_timestamp_seconds",
            "Wall-clock time (unix epoch) this feed's header timestamp last "
            "advanced -- staleness alerts compare time() against this, "
            "never against entity count (settled decision)",
            ["feed"],
            registry=registry,
        )
        self.rate_limit_remaining = Gauge(
            "traintracker_rate_limit_remaining",
            "Lowest x-rate-limit 'remaining' count seen across this cycle's "
            "throttle windows",
            registry=registry,
        )
        self.backoff_active = Gauge(
            "traintracker_backoff_active",
            "1 while the circuit breaker is backing off, else 0 -- alert "
            "rules AND against this being 0 so a legitimate backoff "
            "episode is never read as an outage",
            registry=registry,
        )
        self.tracked_trips = Gauge(
            "traintracker_tracked_trips",
            "Trips currently held in TrainLifecycleTracker._trains, by "
            "status -- watch this stays bounded rather than climbing "
            "(TU-only trips that never get a last_seen_at can otherwise "
            "never age out)",
            ["status"],
            registry=registry,
        )
        self.briefings_sent_total = Counter(
            "traintracker_briefings_sent_total",
            "On-demand disruption briefings (POST /briefing/trigger) "
            "actually composed AND delivered -- every briefing is a "
            "deliberate request, not a heuristic-driven page",
            registry=registry,
        )
        self.trip_completions_total = Counter(
            "traintracker_trip_completions_total",
            "Trip-completion classifications by status -- on_time/late "
            "reuse PTV's public 4:59 threshold (punctuality); cancelled "
            "is a separate reliability outcome, never scored as a "
            "punctuality miss; undetermined_gap is a trip lost to a "
            "coverage gap before reaching its terminus, recorded "
            "honestly rather than silently excluded",
            ["status"],
            registry=registry,
        )
        self.delay_observations_total = Counter(
            "traintracker_delay_observations_total",
            "Mid-journey delay observations logged -- the delay/ETA-"
            "prediction feature's training-data collection step, distinct "
            "from trip_completions_total's once-per-trip terminus outcome",
            registry=registry,
        )
        self.http_requests_total = Counter(
            "traintracker_http_requests_total",
            "HTTP requests by route template, method, and status code -- "
            "'route' is the FastAPI route template (e.g. "
            "/stations/{station_id}/schedule), not the raw path, so "
            "per-station hits don't create unbounded label cardinality",
            ["route", "method", "status"],
            registry=registry,
        )
        self.http_request_duration_seconds = Histogram(
            "traintracker_http_request_duration_seconds",
            "Time to first byte (response start, not full completion -- "
            "deliberate: /api/stream is a long-lived SSE connection that "
            "would otherwise show up as an hours-long outlier) by route "
            "template and method",
            ["route", "method"],
            registry=registry,
        )

    def record_http_request(self, route: str, method: str, status: int, duration_s: float) -> None:
        self.http_requests_total.labels(route=route, method=method, status=str(status)).inc()
        self.http_request_duration_seconds.labels(route=route, method=method).observe(duration_s)

    def event_logs(
        self,
        discrepancy_log: object,
        ghost_log: object,
        gap_log: object,
        completion_log: object,
        delay_observation_log: object,
    ) -> tuple[
        CountingEventLog, CountingEventLog, CountingEventLog, CountingEventLog, CountingEventLog,
    ]:
        """Wrap the given `EventLog`s (e.g. `HistoryStore` facades) with
        counting, preserving whatever persistence they already do."""
        return (
            CountingEventLog(discrepancy_log, self.discrepancy_events_total),
            CountingEventLog(ghost_log, self.ghost_events_total, _ghost_labels),
            CountingEventLog(gap_log, self.poll_gap_events_total),
            CountingEventLog(completion_log, self.trip_completions_total, _completion_labels),
            CountingEventLog(delay_observation_log, self.delay_observations_total),
        )

    def record_cycle(self, result: CycleResult, breaker: CircuitBreaker) -> None:
        self.poll_cycles_total.labels(result="ok" if result.ok else "error").inc()
        if result.lowest_remaining is not None:
            self.rate_limit_remaining.set(result.lowest_remaining)
        self.backoff_active.set(1 if breaker.backoff_active else 0)

    def record_feed_ages(self, feeds: tuple[Feed, ...], last_changed_at: Callable[[Feed], datetime | None]) -> None:
        for feed in feeds:
            changed_at = last_changed_at(feed)
            if changed_at is not None:
                self.feed_last_changed_timestamp.labels(feed=feed.value).set(changed_at.timestamp())

    def record_briefing_sent(self) -> None:
        self.briefings_sent_total.inc()

    def record_tracked_trips(self, tracked: tuple[TrackedTrainView, ...]) -> None:
        counts: dict[Status, int] = {"live": 0, "coasting": 0, "ghost": 0}
        for trip in tracked:
            counts[trip.status] += 1
        # Set all three labels every call, not just non-zero ones -- an
        # always-zero status should read as a real 0 on the dashboard, not
        # a missing series.
        for status, count in counts.items():
            self.tracked_trips.labels(status=status).set(count)
