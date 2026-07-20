"""GTFS `HH:MM:SS` time parsing, converted to absolute UTC instants.

GTFS times can exceed 24:00:00 to represent service that runs past midnight
(e.g. "25:30:00" = 1:30am the next calendar day, still attributed to the
service_date it started on). The spec measures these times as an elapsed
duration from "noon minus 12h" of the service date rather than literal
midnight, specifically so trips that run through a DST transition are
unambiguous: noon is never itself in a DST-transition window in Melbourne, so
anchoring there and then applying the HH:MM:SS offset as a pure elapsed-time
duration (not local wall-clock arithmetic) sidesteps the skipped/repeated
local hour entirely.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_TIME_RE = re.compile(r"^(\d{1,3}):([0-5]\d):([0-5]\d)$")

MELBOURNE_TZ = "Australia/Melbourne"


def parse_gtfs_time(time_str: str) -> timedelta:
    """Parse an "HH:MM:SS" GTFS time string (HH may be >= 24) into an
    elapsed-time duration since the start of the service day."""
    match = _TIME_RE.match(time_str.strip())
    if not match:
        raise ValueError(f"not a valid GTFS time string: {time_str!r}")
    hours, minutes, seconds = (int(g) for g in match.groups())
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def service_day_start_utc(service_date: date, tz_name: str = MELBOURNE_TZ) -> datetime:
    """The unambiguous UTC instant of "noon minus 12h" for a service date.

    Computed via noon (never itself ambiguous under Melbourne DST rules)
    rather than literal midnight, per GTFS spec guidance.
    """
    noon_local = datetime(
        service_date.year,
        service_date.month,
        service_date.day,
        12,
        0,
        0,
        tzinfo=ZoneInfo(tz_name),
    )
    return noon_local.astimezone(timezone.utc) - timedelta(hours=12)


def gtfs_time_to_utc(
    service_date: date, time_str: str, tz_name: str = MELBOURNE_TZ
) -> datetime:
    """Convert a GTFS static time string + its service_date into an absolute
    UTC instant, correct across DST transitions."""
    return service_day_start_utc(service_date, tz_name) + parse_gtfs_time(time_str)


def service_date_for_instant(
    instant_utc: datetime,
    tz_name: str = MELBOURNE_TZ,
    day_boundary_hour: int = 3,
) -> date:
    """Attribute an observed instant (e.g. a realtime feed record) to the
    service_date it belongs to, using a fixed local day-boundary hour rather
    than literal midnight — post-midnight trips (GTFS 24:xx times) still
    belong to the previous service day until this boundary passes.

    `day_boundary_hour` is a config knob, not a settled decision — the
    default (3am) matches common transit convention but should be revisited
    if overnight service patterns suggest otherwise.
    """
    if instant_utc.tzinfo is None:
        raise ValueError("instant_utc must be timezone-aware")
    local = instant_utc.astimezone(ZoneInfo(tz_name))
    service_date = local.date()
    if local.hour < day_boundary_hour:
        service_date -= timedelta(days=1)
    return service_date
