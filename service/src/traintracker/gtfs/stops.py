"""Static GTFS `stops.txt` — stop_id -> platform coordinates.

Kept separate from `StaticSnapshot` (trips + calendar): that class exists to
answer "which trip_ids run on this service_date", a calendar question.
This one answers "where is this stop_id", a geometry question needed only
by station-state derivation. Different lifecycles, no reason to couple them.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass


@dataclass(frozen=True)
class Stop:
    stop_id: str
    name: str
    latitude: float
    longitude: float
    # Platform -> parent station grouping (location_type=1 rows are parent
    # stations themselves and have no parent_station of their own). Needed
    # by gtfs/schedule.py to map a station_id to the set of platform
    # stop_ids that stop_times.txt actually keys its rows by -- mirrors
    # build_web_geometry.py's own platform_to_station logic.
    parent_station: str | None = None
    # Platform-level fields (blank on parent-station rows themselves, per
    # GTFS spec -- location_type=1 rows don't carry these).
    platform_code: str | None = None
    wheelchair_boarding: int | None = None


def parse_stops(stops_txt: str) -> dict[str, Stop]:
    stops = {}
    for row in csv.DictReader(io.StringIO(stops_txt)):
        stop_id = row["stop_id"]
        parent_station = row.get("parent_station") or None
        if row.get("location_type") == "1":
            parent_station = None
        wheelchair_raw = row.get("wheelchair_boarding") or None
        stops[stop_id] = Stop(
            stop_id=stop_id,
            name=row.get("stop_name", ""),
            latitude=float(row["stop_lat"]),
            longitude=float(row["stop_lon"]),
            parent_station=parent_station,
            platform_code=row.get("platform_code") or None,
            wheelchair_boarding=int(wheelchair_raw) if wheelchair_raw is not None else None,
        )
    return stops


def stops_from_zip_bytes(data: bytes) -> dict[str, Stop]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        stops_txt = zf.read("stops.txt").decode("utf-8-sig")
    return parse_stops(stops_txt)
