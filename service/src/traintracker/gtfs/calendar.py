"""GTFS `calendar.txt` / `calendar_dates.txt` parsing and service-day resolution."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

_WEEKDAY_COLUMNS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

ADDED = 1
REMOVED = 2


def _parse_gtfs_date(value: str) -> date:
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


@dataclass(frozen=True)
class CalendarRule:
    service_id: str
    weekdays: frozenset[int]  # 0=Monday .. 6=Sunday, per date.weekday()
    start_date: date
    end_date: date

    def runs_on(self, day: date) -> bool:
        return (
            self.start_date <= day <= self.end_date
            and day.weekday() in self.weekdays
        )


@dataclass(frozen=True)
class CalendarException:
    service_id: str
    date: date
    exception_type: int  # ADDED or REMOVED


class GtfsCalendar:
    """Resolves which service_ids are active on a given service_date.

    Built from `calendar.txt` (weekly recurrence) and `calendar_dates.txt`
    (explicit add/remove exceptions, which always override the weekly rule
    for that specific date).
    """

    def __init__(
        self,
        rules: list[CalendarRule],
        exceptions: list[CalendarException],
    ) -> None:
        self._rules = rules
        self._exceptions_by_date: dict[date, dict[str, int]] = {}
        for exc in exceptions:
            self._exceptions_by_date.setdefault(exc.date, {})[exc.service_id] = (
                exc.exception_type
            )

    @classmethod
    def from_csv(cls, calendar_txt: str, calendar_dates_txt: str) -> "GtfsCalendar":
        return cls(
            rules=list(parse_calendar(calendar_txt)),
            exceptions=list(parse_calendar_dates(calendar_dates_txt)),
        )

    def active_service_ids(self, service_date: date) -> frozenset[str]:
        active = {
            rule.service_id for rule in self._rules if rule.runs_on(service_date)
        }
        for service_id, exception_type in self._exceptions_by_date.get(
            service_date, {}
        ).items():
            if exception_type == ADDED:
                active.add(service_id)
            elif exception_type == REMOVED:
                active.discard(service_id)
        return frozenset(active)


def parse_calendar(calendar_txt: str) -> list[CalendarRule]:
    rules = []
    for row in csv.DictReader(io.StringIO(calendar_txt)):
        weekdays = frozenset(
            i for i, col in enumerate(_WEEKDAY_COLUMNS) if row[col].strip() == "1"
        )
        rules.append(
            CalendarRule(
                service_id=row["service_id"],
                weekdays=weekdays,
                start_date=_parse_gtfs_date(row["start_date"]),
                end_date=_parse_gtfs_date(row["end_date"]),
            )
        )
    return rules


def parse_calendar_dates(calendar_dates_txt: str) -> list[CalendarException]:
    exceptions = []
    for row in csv.DictReader(io.StringIO(calendar_dates_txt)):
        exceptions.append(
            CalendarException(
                service_id=row["service_id"],
                date=_parse_gtfs_date(row["date"]),
                exception_type=int(row["exception_type"]),
            )
        )
    return exceptions
