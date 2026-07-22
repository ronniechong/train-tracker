"""Prometheus metrics (milestone 2f) — wiring, not designing: every metric
here reads from a schema or pure function 2b/2c/2d already built. `EventLog`
stays the seam: `CountingEventLog` composes with whatever `EventLog` it's
given (e.g. 2e's `HistoryStore` facades), incrementing a counter then
delegating — `merge.py`/`ghost.py`/`breaker.py` need no changes at all,
same pattern 2e already established for persistence.

`Metrics` takes an explicit `CollectorRegistry` (defaulting to
prometheus_client's global `REGISTRY`) rather than only ever using module-
level singleton metric objects, so tests can build an isolated instance per
test without tripping prometheus_client's "duplicated timeseries" error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge

from .gateway.client import Feed
from .poller.breaker import CircuitBreaker
from .poller.loop import CycleResult

# Confirmed decision (CLAUDE.md): alert on feed header age, NEVER entity
# count -- this threshold is the only staleness signal built here. The
# "0-entity-but-header-advancing" suppression case from 2f's AC is satisfied
# by construction: this metric is header-timestamp-based from the start, so
# a legitimate zero-entity overnight cycle (header still advancing) was
# never at risk of being read as staleness in the first place.
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
            "TU/VP discrepancies observed (2d's DiscrepancyEvent)",
            registry=registry,
        )
        self.ghost_events_total = Counter(
            "traintracker_ghost_events_total",
            "Ghost episodes resolved (2d's GhostEvent), by whether both "
            "endpoints were City Loop-contained",
            ["loop_contained"],
            registry=registry,
        )
        self.poll_gap_events_total = Counter(
            "traintracker_poll_gap_events_total",
            "Circuit-breaker backoff episodes (2b's PollGapEvent)",
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

    def event_logs(
        self, discrepancy_log: object, ghost_log: object, gap_log: object,
    ) -> tuple[CountingEventLog, CountingEventLog, CountingEventLog]:
        """Wrap the given `EventLog`s (e.g. 2e's `HistoryStore` facades) with
        counting, preserving whatever persistence they already do."""
        return (
            CountingEventLog(discrepancy_log, self.discrepancy_events_total),
            CountingEventLog(ghost_log, self.ghost_events_total, _ghost_labels),
            CountingEventLog(gap_log, self.poll_gap_events_total),
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
