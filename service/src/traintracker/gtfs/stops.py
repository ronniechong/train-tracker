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


def parse_stops(stops_txt: str) -> dict[str, Stop]:
    stops = {}
    for row in csv.DictReader(io.StringIO(stops_txt)):
        stop_id = row["stop_id"]
        stops[stop_id] = Stop(
            stop_id=stop_id,
            name=row.get("stop_name", ""),
            latitude=float(row["stop_lat"]),
            longitude=float(row["stop_lon"]),
        )
    return stops


def stops_from_zip_bytes(data: bytes) -> dict[str, Stop]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        stops_txt = zf.read("stops.txt").decode("utf-8-sig")
    return parse_stops(stops_txt)
