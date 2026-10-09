"""Join-rate check + repin for the current service_date.

The pinned snapshot can stop matching the live feed: the operator
sometimes rolls the realtime feed onto a new timetable (new trip_ids) a
day or more before the portal republishes the matching static zip, and a
failed nightly fetch can leave a service_date with no pin at all. Either
way every schedule lookup misses, so completions and delay observations
silently stop being recorded.

Trip_ids don't move monotonically between publishes -- an older stored
snapshot can match today's live trips better than the newest one -- so
this scores every recently stored snapshot against the live trip_ids
rather than assuming the latest download is right.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Mapping

from ..state.merge import TrainSnapshot
from .gtfstime import service_date_boundary_utc, service_date_for_instant
from .joinrate import RealtimeTripRef, compute_join_rate
from .pinning import PinManifest
from .snapshot import StaticSnapshot

logger = logging.getLogger(__name__)

CHECK_INTERVAL_S = 600.0
MIN_LIVE_TRIPS = 20
REPIN_BELOW_PCT = 90.0
MIN_IMPROVEMENT_PCT = 10.0
MAX_CANDIDATES = 8
# An unpinned service_date is left alone this long after it starts, so the
# nightly fetch job pins it first; claiming it earlier with an old stored
# snapshot would turn that job's pin into a no-op.
UNPINNED_GRACE_S = 3 * 3600


@dataclass(frozen=True)
class JoinCheck:
    service_date: date
    live_trips: int
    pinned_digest: str | None
    join_pct: float
    repinned_to: str | None = None
    repinned_join_pct: float | None = None


def _live_trip_refs(
    snapshots: Mapping[str, TrainSnapshot], service_date: date
) -> list[RealtimeTripRef]:
    wanted = service_date.strftime("%Y%m%d")
    return [
        # MessageToDict omits enum defaults, so SCHEDULED arrives as None.
        RealtimeTripRef(s.trip_id, s.schedule_relationship or "SCHEDULED")
        for s in snapshots.values()
        if s.has_schedule and s.start_date == wanted
    ]


class SnapshotSelector:
    def __init__(self, gtfs_dir: Path, pin_manifest: PinManifest):
        self._gtfs_dir = gtfs_dir
        self._pin_manifest = pin_manifest
        self._trip_ids_by_digest: dict[str, frozenset[str]] = {}
        self._last_check_at: datetime | None = None

    def _trip_ids(self, digest: str) -> frozenset[str] | None:
        cached = self._trip_ids_by_digest.get(digest)
        if cached is not None:
            return cached
        path = self._gtfs_dir / f"{digest}.zip"
        if not path.exists():
            return None
        trip_ids = StaticSnapshot.from_zip_path(path).trip_ids
        self._trip_ids_by_digest[digest] = trip_ids
        return trip_ids

    def _candidate_digests(self) -> list[str]:
        zips = sorted(
            self._gtfs_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        digests = [p.stem for p in zips[:MAX_CANDIDATES]]
        # Bound the cache to what can still be a candidate.
        for stale in set(self._trip_ids_by_digest) - set(digests):
            del self._trip_ids_by_digest[stale]
        return digests

    def maybe_check(
        self, snapshots: Mapping[str, TrainSnapshot], now: datetime
    ) -> JoinCheck | None:
        if (
            self._last_check_at is not None
            and (now - self._last_check_at).total_seconds() < CHECK_INTERVAL_S
        ):
            return None
        self._last_check_at = now
        return self.check(snapshots, now)

    def check(self, snapshots: Mapping[str, TrainSnapshot], now: datetime) -> JoinCheck | None:
        service_date = service_date_for_instant(now)
        refs = _live_trip_refs(snapshots, service_date)
        if len(refs) < MIN_LIVE_TRIPS:
            return None

        def score(digest: str) -> float | None:
            trip_ids = self._trip_ids(digest)
            if trip_ids is None:
                return None
            return compute_join_rate(refs, trip_ids).join_pct_scheduled_only

        pin = self._pin_manifest.get(service_date)
        pinned_digest = pin.digest if pin is not None else None
        if pinned_digest is None:
            day_age_s = (now - service_date_boundary_utc(service_date)).total_seconds()
            if day_age_s < UNPINNED_GRACE_S:
                return None
        current = (score(pinned_digest) if pinned_digest else None) or 0.0
        result = JoinCheck(service_date, len(refs), pinned_digest, current)
        if pinned_digest is not None and current >= REPIN_BELOW_PCT:
            return result

        best_digest, best_pct = None, current
        for digest in self._candidate_digests():
            if digest == pinned_digest:
                continue
            pct = score(digest)
            if pct is not None and pct > best_pct:
                best_digest, best_pct = digest, pct

        if best_digest is None or best_pct - current < MIN_IMPROVEMENT_PCT:
            logger.warning(
                "static join %.1f%% for service_date=%s (%d live trips), no better stored snapshot",
                current, service_date, len(refs),
            )
            return result

        self._pin_manifest.repin(service_date, best_digest)
        logger.warning(
            "repinned service_date=%s from %s (%.1f%%) to %s (%.1f%%), %d live trips",
            service_date, pinned_digest, current, best_digest, best_pct, len(refs),
        )
        return JoinCheck(
            service_date, len(refs), pinned_digest, current,
            repinned_to=best_digest, repinned_join_pct=best_pct,
        )
