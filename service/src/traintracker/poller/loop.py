"""Main poll loop: ties the 2a gateway client, protobuf decode, service-hours
cadence, circuit breaker, 2d's state store, and the external dead-man
ping together — the one thing that actually exercises 2a's client against
live traffic for the first time (2b's stated goal).

Header-timestamp dedupe does NOT mean "treat an unchanged feed as empty
this cycle." `StateStore.ingest()` -> `TrainLifecycleTracker.tick()` needs
to run every cycle regardless, using real wall-clock `cycle_time`, so
coasting timers keep advancing between genuine upstream refreshes (VP's
true cadence is ~29-30s even though we now poll every ~10s per this
milestone's acceptance criteria) — an earlier draft skipped `ingest()`
entirely on an unchanged header and made every trip look VP-less on 2 of
every 3 cycles, which would have broken coasting/ghosting. "Dedupe" here
means: cache and reuse the last-decoded content for an unchanged feed
rather than re-deriving anything from a byte-identical payload.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from ..gateway.client import Feed, GatewayAuthError, GatewayClient, GatewayError
from ..state.eventlog import EventLog
from ..state.store import StateStore
from . import healthcheck, schedule
from .breaker import CircuitBreaker
from .decode import decode_feed, header_timestamp

logger = logging.getLogger(__name__)

ALL_FEEDS: tuple[Feed, ...] = (Feed.TRIP_UPDATES, Feed.VEHICLE_POSITIONS, Feed.SERVICE_ALERTS)


@dataclass(frozen=True)
class CycleResult:
    ok: bool
    changed_feeds: frozenset[Feed]
    lowest_remaining: int | None


@dataclass
class _FeedCache:
    last_header_ts: dict[Feed, int] = field(default_factory=dict)
    last_decoded: dict[Feed, dict] = field(default_factory=dict)
    last_changed_at: dict[Feed, datetime] = field(default_factory=dict)


class PollerLoop:
    def __init__(
        self,
        gateway: GatewayClient,
        store: StateStore,
        gap_log: EventLog,
        breaker: CircuitBreaker | None = None,
        healthcheck_client: httpx.Client | None = None,
    ):
        self._gateway = gateway
        self._store = store
        self._gap_log = gap_log
        self._breaker = breaker or CircuitBreaker()
        self._healthcheck_client = healthcheck_client or httpx.Client()
        self._cache = _FeedCache()
        self._stop = False

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def stopped(self) -> bool:
        return self._stop

    def last_changed_at(self, feed: Feed) -> datetime | None:
        """Last time this feed's header timestamp actually advanced --
        2f's staleness alert (CLAUDE.md: alert on header age, never entity
        count) is built on top of this, not built here."""
        return self._cache.last_changed_at.get(feed)

    def stop(self) -> None:
        self._stop = True

    def run_cycle(self, now: datetime | None = None) -> CycleResult:
        now = now or datetime.now(timezone.utc)
        changed: set[Feed] = set()
        lowest_remaining: int | None = None
        ok = True

        for feed in ALL_FEEDS:
            try:
                response = self._gateway.fetch(feed)
            except GatewayAuthError:
                logger.error("auth rejected on %s, aborting cycle", feed.value)
                ok = False
                break
            except (GatewayError, httpx.HTTPError) as exc:
                logger.warning("request failed on %s: %s", feed.value, exc)
                ok = False
                continue

            for window in response.throttle:
                if lowest_remaining is None or window.remaining < lowest_remaining:
                    lowest_remaining = window.remaining

            decoded_feed = decode_feed(response.payload)
            ts = header_timestamp(decoded_feed)
            unchanged = ts is not None and self._cache.last_header_ts.get(feed) == ts
            if unchanged:
                logger.debug("%s header unchanged (%s)", feed.value, ts)
                continue

            if ts is not None:
                self._cache.last_header_ts[feed] = ts
            self._cache.last_decoded[feed] = decoded_feed
            self._cache.last_changed_at[feed] = now
            changed.add(feed)

        if ok:
            gap = self._breaker.record_success(now, lowest_remaining)
            if gap is not None:
                self._gap_log.record(gap)
        else:
            self._breaker.record_failure(now)

        if Feed.TRIP_UPDATES in self._cache.last_decoded or Feed.VEHICLE_POSITIONS in self._cache.last_decoded:
            self._store.ingest(
                tu_feed=self._cache.last_decoded.get(Feed.TRIP_UPDATES, {}),
                vp_feed=self._cache.last_decoded.get(Feed.VEHICLE_POSITIONS, {}),
                cycle_time=now,
                backoff_active=self._breaker.backoff_active,
            )

        if ok:
            healthcheck.ping(self._healthcheck_client)

        return CycleResult(ok=ok, changed_feeds=frozenset(changed), lowest_remaining=lowest_remaining)

    def next_interval(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        if self._breaker.backoff_active:
            return self._breaker.next_interval()
        return schedule.base_interval(now)
