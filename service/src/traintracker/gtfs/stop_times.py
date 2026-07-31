"""Static GTFS `stop_times.txt` -- per-trip, per-platform scheduled times.

Kept separate from `StaticSnapshot` (trips + calendar) and `stops.py`
(platform coordinates + station grouping) for the same reason those two are
already split: this file answers one question -- "what time does trip_id
call at platform stop_id" -- and stays a pure parsing module. Query logic
(resolving a station's next departures) lives in `schedule.py`, not here.

Times are kept as raw GTFS `HH:MM:SS` strings (which can exceed 24:00:00 for
past-midnight service) rather than parsed here: converting to an absolute
UTC instant needs a resolved `service_date`, which this module has no
knowledge of -- `gtfstime.gtfs_time_to_utc` does that conversion in the
query layer instead.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass


@dataclass(frozen=True)
class StopTimeRecord:
    trip_id: str
    stop_id: str
    stop_sequence: int
    arrival_time: str | None
    departure_time: str | None


def parse_stop_times(stop_times_txt: str) -> list[StopTimeRecord]:
    records = []
    for row in csv.DictReader(io.StringIO(stop_times_txt)):
        records.append(
            StopTimeRecord(
                trip_id=row["trip_id"],
                stop_id=row["stop_id"],
                stop_sequence=int(row["stop_sequence"]),
                arrival_time=row.get("arrival_time") or None,
                departure_time=row.get("departure_time") or None,
            )
        )
    return records


def stop_times_from_zip_bytes(data: bytes) -> list[StopTimeRecord]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        stop_times_txt = zf.read("stop_times.txt").decode("utf-8-sig")
    return parse_stop_times(stop_times_txt)
