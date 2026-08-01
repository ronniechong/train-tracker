"""Loads and caches the pieces `gtfs/schedule.py` needs (stops, trips,
stop_times) from whichever static snapshot is pinned for "today" -- the one
stateful/impure piece of the station-schedule feature. Everything it hands
to `schedule.py` stays pure and testable; this class only owns file I/O and
an in-memory cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ..state.merge import TrainSnapshot
from .gtfstime import gtfs_time_to_utc, service_date_for_instant
from .pinning import PinManifest
from .routes import Route, routes_from_zip_bytes
from .schedule import (
    ScheduledDeparture,
    added_departures,
    next_departures,
    platforms_for_station,
)
from .snapshot import StaticSnapshot
from .stop_times import StopTimeRecord, stop_times_from_zip_bytes
from .stops import Stop, stops_from_zip_bytes


class NoPinnedSnapshotError(Exception):
    """No static snapshot is pinned yet for today's resolved service_date --
    e.g. before the nightly fetch job's first run has ever completed. A
    distinct failure mode from "unknown station_id", not conflated with it
    (project convention: honest, distinguishable failures)."""


@dataclass(frozen=True)
class TripTerminus:
    """A trip's scheduled final stop -- the 05-ai-layer trip-completion
    tracker's anchor for "did this trip arrive on time", per the official
    Victorian definition (arrival AT THE TERMINUS, not mid-journey delay).

    `stop_sequence` added 2026-08-01 for the delay/ETA-prediction
    observation logger's `stops_remaining` feature (terminus's
    stop_sequence minus the current stop's) -- purely additive,
    `state/completion.py`'s own separate `TripTerminus` (duck-typed
    against this one via `.stop_id`/`.scheduled_arrival` only, see
    `poller/__main__.py`'s wiring) is unaffected by the new field."""

    stop_id: str
    scheduled_arrival: datetime  # absolute UTC
    stop_sequence: int


@dataclass(frozen=True)
class _ParsedSchedule:
    snapshot: StaticSnapshot
    stops: dict[str, Stop]
    stop_times: list[StopTimeRecord]
    routes: dict[str, Route]
    # Lazily built, memoized alongside the rest of this digest's parse --
    # trip_id -> its highest-stop_sequence StopTimeRecord. Built once per
    # digest (content is immutable per digest, same reasoning `_by_digest`
    # already relies on) rather than scanning the full stop_times list on
    # every terminus_for() call.
    termini_by_trip: dict[str, StopTimeRecord]


class PinnedScheduleCache:
    def __init__(self, gtfs_dir: Path, pin_manifest: PinManifest):
        self._gtfs_dir = gtfs_dir
        self._pin_manifest = pin_manifest
        # Keyed by snapshot digest, not service_date -- content is
        # immutable per digest (the portal only republishes ~weekly, and
        # 2c's fetch job reuses the same digest across unchanged days), so
        # entries never need invalidating. 60-day history retention bounds
        # how many distinct digests can ever accumulate here.
        self._by_digest: dict[str, _ParsedSchedule] = {}

    def _load(self, digest: str) -> _ParsedSchedule:
        cached = self._by_digest.get(digest)
        if cached is not None:
            return cached
        data = (self._gtfs_dir / f"{digest}.zip").read_bytes()
        stop_times = stop_times_from_zip_bytes(data)
        termini_by_trip: dict[str, StopTimeRecord] = {}
        for record in stop_times:
            current = termini_by_trip.get(record.trip_id)
            if current is None or record.stop_sequence > current.stop_sequence:
                termini_by_trip[record.trip_id] = record
        parsed = _ParsedSchedule(
            snapshot=StaticSnapshot.from_zip_bytes(data),
            stops=stops_from_zip_bytes(data),
            stop_times=stop_times,
            routes=routes_from_zip_bytes(data),
            termini_by_trip=termini_by_trip,
        )
        self._by_digest[digest] = parsed
        return parsed

    def _load_for(self, now: datetime) -> _ParsedSchedule:
        """Resolves "today"'s pin and loads its parsed snapshot -- the
        pin-resolution step `next_departures_for` and `routes_for` both
        need, factored out once routes.py gave this class a second
        caller for it."""
        service_date = service_date_for_instant(now)
        pin = self._pin_manifest.get(service_date)
        if pin is None:
            raise NoPinnedSnapshotError(
                f"no static snapshot pinned for service_date {service_date.isoformat()}"
            )
        return self._load(pin.digest)

    def terminus_for(self, trip_id: str, service_date: date) -> TripTerminus | None:
        """The trip's scheduled final stop + absolute UTC arrival, resolved
        against whichever static snapshot is pinned to `service_date` --
        the trip's OWN service_date (Trip Updates' `trip.start_date`), not
        "today", since a post-midnight trip's schedule can span the day
        boundary this cache resolves "now" against.

        Returns `None`, not an error, when: no snapshot is pinned for that
        service_date yet, the trip_id has no static stop_times row at all
        (a real-time-only ADDED trip, per CLAUDE.md's trip_id-join
        convention -- has no static schedule to compare against by
        construction), or its terminus row carries neither an arrival nor
        departure time (malformed row; state/completion.py treats an
        unresolvable terminus as "can't track this trip's completion",
        not a crash)."""
        pin = self._pin_manifest.get(service_date)
        if pin is None:
            return None
        parsed = self._load(pin.digest)
        terminus = parsed.termini_by_trip.get(trip_id)
        if terminus is None:
            return None
        time_str = terminus.arrival_time or terminus.departure_time
        if time_str is None:
            return None
        return TripTerminus(
            stop_id=terminus.stop_id,
            scheduled_arrival=gtfs_time_to_utc(service_date, time_str),
            stop_sequence=terminus.stop_sequence,
        )

    def routes_for(self, now: datetime) -> dict[str, Route]:
        """route_id -> Route (short/long name) for whichever static
        snapshot is pinned for "today" -- the AI layer's tools (get_line_
        status, get_active_alerts) use this to resolve a line NAME a user
        or the LLM typed ("Belgrave") into the route_id(s) the realtime
        feeds actually key on. Raises `NoPinnedSnapshotError` under the
        same condition `next_departures_for` does."""
        return self._load_for(now).routes

    def next_departures_for(
        self,
        station_id: str,
        now: datetime,
        limit_per_direction: int = 3,
        live_snapshots: dict[str, TrainSnapshot] | None = None,
    ) -> list[ScheduledDeparture] | None:
        """Returns `None` if `station_id` has no known platforms (caller
        should treat as 404). Raises `NoPinnedSnapshotError` if no snapshot
        is pinned yet for today (caller should treat as a distinct
        service-not-ready condition, not a client error).

        `live_snapshots` (05a pass 3, optional) is the caller's
        `StateStore.latest_snapshots` -- when given, ADDED (real-time-only,
        no static row) trips calling at this station are folded in and the
        combined list re-sorted by time. Omitting it (the default) keeps
        this method's original static-only behaviour, e.g. for tests that
        don't care about the live overlay."""
        parsed = self._load_for(now)
        service_date = service_date_for_instant(now)
        platform_ids = platforms_for_station(parsed.stops, station_id)
        if not platform_ids:
            return None

        active_trip_ids = parsed.snapshot.trip_ids_for_service_date(service_date)
        active_trips = [t for t in parsed.snapshot.trips if t.trip_id in active_trip_ids]
        departures = next_departures(
            active_trips,
            parsed.stop_times,
            platform_ids,
            service_date,
            now,
            limit_per_direction=limit_per_direction,
        )
        if live_snapshots:
            extra = added_departures(
                live_snapshots, parsed.stops, platform_ids, now, limit_per_direction=limit_per_direction
            )
            departures = sorted(departures + extra, key=lambda d: d.scheduled_time)
        return departures
