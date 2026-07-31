"""Loads and caches the pieces `gtfs/schedule.py` needs (stops, trips,
stop_times) from whichever static snapshot is pinned for "today" -- the one
stateful/impure piece of the station-schedule feature. Everything it hands
to `schedule.py` stays pure and testable; this class only owns file I/O and
an in-memory cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..state.merge import TrainSnapshot
from .gtfstime import service_date_for_instant
from .pinning import PinManifest
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
class _ParsedSchedule:
    snapshot: StaticSnapshot
    stops: dict[str, Stop]
    stop_times: list[StopTimeRecord]


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
        parsed = _ParsedSchedule(
            snapshot=StaticSnapshot.from_zip_bytes(data),
            stops=stops_from_zip_bytes(data),
            stop_times=stop_times_from_zip_bytes(data),
        )
        self._by_digest[digest] = parsed
        return parsed

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
        service_date = service_date_for_instant(now)
        pin = self._pin_manifest.get(service_date)
        if pin is None:
            raise NoPinnedSnapshotError(
                f"no static snapshot pinned for service_date {service_date.isoformat()}"
            )

        parsed = self._load(pin.digest)
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
