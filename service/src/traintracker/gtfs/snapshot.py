"""A single downloaded static GTFS snapshot (the zip archive)."""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .calendar import GtfsCalendar


@dataclass(frozen=True)
class TripRecord:
    trip_id: str
    service_id: str
    route_id: str


class StaticSnapshot:
    """Parsed contents of one static GTFS zip: trips + the calendar needed
    to resolve which service_ids run on a given service_date."""

    def __init__(self, trips: list[TripRecord], calendar: GtfsCalendar, digest: str):
        self.trips = trips
        self.calendar = calendar
        self.digest = digest
        self._trip_ids = frozenset(t.trip_id for t in trips)

    @property
    def trip_ids(self) -> frozenset[str]:
        return self._trip_ids

    def trip_ids_for_service_date(self, service_date) -> frozenset[str]:
        active_services = self.calendar.active_service_ids(service_date)
        return frozenset(
            t.trip_id for t in self.trips if t.service_id in active_services
        )

    @classmethod
    def from_zip_bytes(cls, data: bytes) -> "StaticSnapshot":
        digest = hashlib.sha256(data).hexdigest()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            calendar_txt = zf.read("calendar.txt").decode("utf-8-sig")
            calendar_dates_txt = (
                zf.read("calendar_dates.txt").decode("utf-8-sig")
                if "calendar_dates.txt" in zf.namelist()
                else "service_id,date,exception_type\n"
            )
            trips_txt = zf.read("trips.txt").decode("utf-8-sig")

        calendar = GtfsCalendar.from_csv(calendar_txt, calendar_dates_txt)
        trips = [
            TripRecord(
                trip_id=row["trip_id"],
                service_id=row["service_id"],
                route_id=row["route_id"],
            )
            for row in csv.DictReader(io.StringIO(trips_txt))
        ]
        return cls(trips=trips, calendar=calendar, digest=digest)

    @classmethod
    def from_zip_path(cls, path: Path | str) -> "StaticSnapshot":
        return cls.from_zip_bytes(Path(path).read_bytes())
