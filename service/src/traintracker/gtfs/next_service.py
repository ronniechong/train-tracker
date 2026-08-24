"""Single-transfer next-service combination logic for M13's public API.

Kept separate from `schedule.py` (same-line lookup is pure GTFS query
logic) because this module additionally encodes a POLICY decision: which
stations count as valid interchanges. `transfers.txt` was checked against
the real pinned snapshot (2026-08-25) and found unusable for this purpose
-- populated, but every row is a same-stop, same-platform trip-to-trip
continuation record at Flinders Street only; Southern Cross, Richmond,
Clifton Hill, and North Melbourne (the interchanges this milestone
actually needs) have zero rows. Hence a curated list here instead of
deriving candidates from that file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .schedule import NextServiceLeg, next_service_same_line, platforms_for_station
from .snapshot import TripRecord
from .station_lookup import canonical_stations
from .stop_times import StopTimeRecord
from .stops import Stop

# Real station names in the live GTFS static feed all carry the
# "Railway Station" suffix (confirmed against the pinned snapshot,
# 2026-08-25) -- "Flinders Street Station" alone does not match any
# location_type=1 row.
CURATED_INTERCHANGES: tuple[str, ...] = (
    "Flinders Street Railway Station",
    "Southern Cross Railway Station",
    "Richmond Railway Station",
    "Clifton Hill Railway Station",
    "North Melbourne Railway Station",
)


@dataclass(frozen=True)
class SingleTransferService:
    first_leg: NextServiceLeg
    second_leg: NextServiceLeg
    interchange_station_id: str

    @property
    def arrival_time(self) -> datetime:
        return self.second_leg.arrival_time

    @property
    def interchange_wait(self):
        return self.second_leg.departure_time - self.first_leg.arrival_time


def _interchange_station_ids(stops: dict[str, Stop]) -> list[str]:
    stations = canonical_stations(stops)
    by_name = {name: sid for sid, name in stations.items()}
    return [by_name[name] for name in CURATED_INTERCHANGES if name in by_name]


def find_next_service_single_transfer(
    trips: list[TripRecord],
    stop_times: list[StopTimeRecord],
    stops: dict[str, Stop],
    from_platform_ids: frozenset[str],
    to_platform_ids: frozenset[str],
    service_date: date,
    after: datetime,
) -> SingleTransferService | None:
    """Best two-leg combination via the curated interchange list, ranked
    by soonest overall arrival time, ties broken by shortest interchange
    wait -- the resolved ranking rule from M13's spec-review (Finding 4).
    No minimum interchange dwell time is enforced beyond the strict
    `departure > arrival` that `next_service_same_line` already applies to
    its own `after` bound -- a real passenger's practical minimum
    connection time is a walking-distance question this milestone's scope
    doesn't reach (same-station platform changes only, no cross-station
    interchange geometry modelled).
    """
    best: SingleTransferService | None = None
    for interchange_id in _interchange_station_ids(stops):
        interchange_platform_ids = platforms_for_station(stops, interchange_id)
        if not interchange_platform_ids:
            continue
        # Interchange itself may BE the origin or destination -- skip a
        # degenerate "transfer" that never actually leaves the same
        # station, that's what the same-line path already covers.
        if interchange_platform_ids & from_platform_ids or interchange_platform_ids & to_platform_ids:
            continue

        first_leg = next_service_same_line(
            trips, stop_times, from_platform_ids, interchange_platform_ids, service_date, after
        )
        if first_leg is None:
            continue
        second_leg = next_service_same_line(
            trips,
            stop_times,
            interchange_platform_ids,
            to_platform_ids,
            service_date,
            first_leg.arrival_time,
        )
        if second_leg is None:
            continue

        candidate = SingleTransferService(
            first_leg=first_leg, second_leg=second_leg, interchange_station_id=interchange_id
        )
        if best is None:
            best = candidate
            continue
        if candidate.arrival_time < best.arrival_time:
            best = candidate
        elif candidate.arrival_time == best.arrival_time and candidate.interchange_wait < best.interchange_wait:
            best = candidate
    return best
