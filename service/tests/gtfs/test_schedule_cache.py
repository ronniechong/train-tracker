import hashlib
import io
import zipfile
from datetime import date, datetime, timezone

from traintracker.gtfs.gtfstime import gtfs_time_to_utc, service_date_for_instant
from traintracker.gtfs.pinning import PinManifest
from traintracker.gtfs.schedule_cache import PinnedScheduleCache


def _pinned_schedule_cache(
    tmp_path, sample_static_zip_bytes, pin_date: date | None = None
) -> PinnedScheduleCache:
    digest = hashlib.sha256(sample_static_zip_bytes).hexdigest()
    (tmp_path / f"{digest}.zip").write_bytes(sample_static_zip_bytes)
    manifest = PinManifest(tmp_path / "pin_manifest.json")
    manifest.pin_digest(pin_date or service_date_for_instant(datetime.now(timezone.utc)), digest)
    return PinnedScheduleCache(tmp_path, manifest)


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _pinned_cache_from_files(tmp_path, files: dict[str, str]) -> PinnedScheduleCache:
    data = _zip_bytes(files)
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / f"{digest}.zip").write_bytes(data)
    manifest = PinManifest(tmp_path / "pin_manifest.json")
    manifest.pin_digest(service_date_for_instant(datetime.now(timezone.utc)), digest)
    return PinnedScheduleCache(tmp_path, manifest)


def test_routes_for_returns_parsed_routes(tmp_path, sample_static_zip_bytes):
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)

    routes = cache.routes_for(datetime.now(timezone.utc))

    assert routes["2-PKM"].short_name == "Pakenham"
    assert routes["2-CRB"].short_name == "Craigieburn"


_MAJORITY_VOTE_FILES = {
    "routes.txt": (
        "route_id,route_short_name,route_long_name\n"
        "ROUTE_A,A,A - City\n"
        "ROUTE_B,B,B - City\n"
    ),
    "trips.txt": (
        "route_id,service_id,trip_id,trip_headsign,direction_id\n"
        "ROUTE_A,WEEKDAY,TRIP_A,City,0\n"
        "ROUTE_B,WEEKDAY,TRIP_B,City,0\n"
    ),
    "stop_times.txt": (
        "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n"
        # ROUTE_A calls at all three -- STOP_1/STOP_2 are "genuinely
        # A-only", STOP_3 is shared (models a real Metro Tunnel station
        # whose static data lags for one line but not another).
        "TRIP_A,1,STOP_1,08:00:00,08:00:00\n"
        "TRIP_A,2,STOP_2,08:05:00,08:05:00\n"
        "TRIP_A,3,STOP_3,08:10:00,\n"
        "TRIP_B,1,STOP_3,08:15:00,08:15:00\n"
    ),
    "stops.txt": (
        "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
        "STATION_1,Station One,-37.8,144.9,1,\n"
        "STOP_1,Platform One,-37.8,144.9,0,STATION_1\n"
        "STOP_2,Platform Two,-37.8,144.9,0,\n"
        "STOP_3,Platform Three,-37.8,144.9,0,\n"
    ),
    "calendar.txt": (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
        "start_date,end_date\n"
        "WEEKDAY,1,1,1,1,1,0,0,20260101,20261231\n"
    ),
    "calendar_dates.txt": "service_id,date,exception_type\n",
}


def test_routes_most_likely_for_stops_resolves_by_majority_despite_one_dissenting_stop(
    tmp_path,
):
    # Real production shape: a 3-stop alert where 2 stops are A-only and 1
    # is shared with B -- a strict
    # "every stop must agree" intersection would return nothing (STOP_1/
    # STOP_2 never see ROUTE_B), but the majority (2 of 3, and ahead of
    # ROUTE_B's 1) correctly resolves to A.
    cache = _pinned_cache_from_files(tmp_path, _MAJORITY_VOTE_FILES)

    matched = cache.routes_most_likely_for_stops(
        datetime.now(timezone.utc), ["STOP_1", "STOP_2", "STOP_3"]
    )

    assert [r.route_id for r in matched] == ["ROUTE_A"]


def test_routes_most_likely_for_stops_resolves_by_parent_station(tmp_path):
    # STOP_1's parent is STATION_1 -- Service Alerts carry the parent
    # station id, not the platform-level id stop_times.txt actually keys
    # on. Proves the parent id
    # participates in the vote at all, not just the raw platform id.
    cache = _pinned_cache_from_files(tmp_path, _MAJORITY_VOTE_FILES)

    matched = cache.routes_most_likely_for_stops(
        datetime.now(timezone.utc), ["STATION_1", "STOP_2", "STOP_3"]
    )

    assert [r.route_id for r in matched] == ["ROUTE_A"]


def test_routes_most_likely_for_stops_returns_empty_on_a_tie(tmp_path):
    # A single shared stop -- both routes tied at 1 vote each, no majority.
    cache = _pinned_cache_from_files(tmp_path, _MAJORITY_VOTE_FILES)

    matched = cache.routes_most_likely_for_stops(datetime.now(timezone.utc), ["STOP_3"])

    assert matched == []


def test_routes_most_likely_for_stops_returns_empty_for_unknown_stops(tmp_path):
    cache = _pinned_cache_from_files(tmp_path, _MAJORITY_VOTE_FILES)

    matched = cache.routes_most_likely_for_stops(
        datetime.now(timezone.utc), ["NOT_A_REAL_STOP"]
    )

    assert matched == []


def test_terminus_for_returns_the_highest_stop_sequence_row(tmp_path, sample_static_zip_bytes):
    # 2026-07-20 is a Monday -- WEEKDAY_TRIP_1's WEEKDAY service is active.
    monday = date(2026, 7, 20)
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes, pin_date=monday)

    terminus = cache.terminus_for("WEEKDAY_TRIP_1", monday)

    assert terminus is not None
    assert terminus.stop_id == "PLAT_B1"  # fixture's stop_sequence 2, no departure_time
    assert terminus.stop_sequence == 2
    # gtfs_time_to_utc is the module's own conversion primitive (tested for
    # DST correctness in test_gtfstime.py) -- this test is about
    # terminus_for's wiring (right row picked, right time string used), not
    # re-deriving Melbourne's UTC offset by hand.
    assert terminus.scheduled_arrival == gtfs_time_to_utc(monday, "08:10:00")


def test_terminus_for_returns_none_when_trip_has_no_static_row(tmp_path, sample_static_zip_bytes):
    monday = date(2026, 7, 20)
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes, pin_date=monday)

    assert cache.terminus_for("SOME_ADDED_TRIP_NOT_IN_STATIC_GTFS", monday) is None


def test_terminus_for_returns_none_when_nothing_pinned_for_that_service_date(
    tmp_path, sample_static_zip_bytes
):
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes, pin_date=date(2026, 7, 20))

    assert cache.terminus_for("WEEKDAY_TRIP_1", date(2099, 1, 1)) is None


_TWO_LINES_ONE_WEEKDAY_ONLY_FILES = {
    "routes.txt": (
        "route_id,route_short_name,route_long_name\n"
        "ROUTE_A,A,A - City\n"
        "ROUTE_B,B,B - City\n"
    ),
    "trips.txt": (
        "route_id,service_id,trip_id,trip_headsign,direction_id\n"
        "ROUTE_A,WEEKDAY,TRIP_A,City,0\n"
        "ROUTE_B,SATURDAY,TRIP_B,City,0\n"
    ),
    "stop_times.txt": (
        "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n"
        # Both routes call at STOP_1 -- normal service, just on different
        # days of the week.
        "TRIP_A,1,STOP_1,08:00:00,08:00:00\n"
        "TRIP_B,1,STOP_1,09:00:00,09:00:00\n"
    ),
    "stops.txt": (
        "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
        "STOP_1,Platform One,-37.8,144.9,0,\n"
    ),
    "calendar.txt": (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
        "start_date,end_date\n"
        "WEEKDAY,1,1,1,1,1,0,0,20260101,20261231\n"
        "SATURDAY,0,0,0,0,0,1,0,20260101,20261231\n"
    ),
    "calendar_dates.txt": "service_id,date,exception_type\n",
}


def test_lines_no_service_today_reports_lines_with_zero_active_trips(tmp_path):
    monday = datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc)  # 2026-07-20 is a Monday
    data = _zip_bytes(_TWO_LINES_ONE_WEEKDAY_ONLY_FILES)
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / f"{digest}.zip").write_bytes(data)
    manifest = PinManifest(tmp_path / "pin_manifest.json")
    manifest.pin_digest(date(2026, 7, 20), digest)
    cache = PinnedScheduleCache(tmp_path, manifest)

    no_service = cache.lines_no_service_today("STOP_1", monday)

    assert [r.route_id for r in no_service] == ["ROUTE_B"]  # Saturday-only, not today


def test_lines_no_service_today_flips_with_the_pinned_service_date(tmp_path):
    # Same fixture, a different day of the week -- proves this reads the
    # real calendar per service_date rather than a fixed/cached answer.
    cache = _pinned_cache_from_files(tmp_path, _TWO_LINES_ONE_WEEKDAY_ONLY_FILES)
    saturday = datetime(2026, 7, 25, 1, 0, tzinfo=timezone.utc)  # 2026-07-25 is a Saturday
    manifest = PinManifest(tmp_path / "pin_manifest.json")
    manifest.pin_digest(date(2026, 7, 25), hashlib.sha256(_zip_bytes(_TWO_LINES_ONE_WEEKDAY_ONLY_FILES)).hexdigest())

    no_service = cache.lines_no_service_today("STOP_1", saturday)

    assert [r.route_id for r in no_service] == ["ROUTE_A"]  # now it's Weekday-only that's out


def test_lines_no_service_today_returns_none_for_unknown_station(tmp_path):
    cache = _pinned_cache_from_files(tmp_path, _TWO_LINES_ONE_WEEKDAY_ONLY_FILES)

    assert cache.lines_no_service_today("NOT_A_REAL_STOP", datetime.now(timezone.utc)) is None


_SKIP_STOP_FILES = {
    "routes.txt": (
        "route_id,route_short_name,route_long_name\n"
        "ROUTE_A,A,A - City\n"
    ),
    "trips.txt": (
        "route_id,service_id,trip_id,trip_headsign,direction_id\n"
        "ROUTE_A,WEEKDAY,ALL_STOPS,City,0\n"
        "ROUTE_A,WEEKDAY,EXPRESS,City,0\n"
    ),
    "stop_times.txt": (
        "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n"
        "ALL_STOPS,1,STOP_1,08:00:00,08:00:00\n"
        "ALL_STOPS,2,STOP_2,08:05:00,08:05:00\n"
        "ALL_STOPS,3,STOP_3,08:10:00,08:10:00\n"
        "EXPRESS,1,STOP_1,08:00:00,08:00:00\n"
        "EXPRESS,2,STOP_3,08:08:00,08:08:00\n"
    ),
    "stops.txt": (
        "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
        "STOP_1,Platform One,-37.8,144.9,0,\n"
        "STOP_2,Platform Two,-37.8,144.9,0,\n"
        "STOP_3,Platform Three,-37.8,144.9,0,\n"
    ),
    "calendar.txt": (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
        "start_date,end_date\n"
        "WEEKDAY,1,1,1,1,1,0,0,20260101,20261231\n"
    ),
    "calendar_dates.txt": "service_id,date,exception_type\n",
}


def test_skip_stop_count_for_flags_the_shorter_pattern(tmp_path):
    cache = _pinned_cache_from_files(tmp_path, _SKIP_STOP_FILES)
    today = service_date_for_instant(datetime.now(timezone.utc))

    assert cache.skip_stop_count_for("ALL_STOPS", today) == 0
    assert cache.skip_stop_count_for("EXPRESS", today) == 1


def test_skip_stop_count_for_returns_none_for_a_real_time_only_trip(tmp_path):
    cache = _pinned_cache_from_files(tmp_path, _SKIP_STOP_FILES)
    today = service_date_for_instant(datetime.now(timezone.utc))

    assert cache.skip_stop_count_for("ADDED_TRIP_NO_STATIC_ROW", today) is None


def test_skip_stop_count_for_returns_none_when_no_snapshot_pinned(tmp_path):
    cache = _pinned_cache_from_files(tmp_path, _SKIP_STOP_FILES)

    assert cache.skip_stop_count_for("ALL_STOPS", date(2099, 1, 1)) is None


def test_find_next_service_same_line(tmp_path, sample_static_zip_bytes):
    weekday = date(2026, 7, 20)  # Monday, matches the WEEKDAY calendar
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes, pin_date=weekday)
    now = gtfs_time_to_utc(weekday, "07:00:00")

    result = cache.find_next_service("A Station", "B Station", now)

    assert result is not None
    assert result.reason is None
    assert result.from_station.station_id == "STATION_A"
    assert result.to_station.station_id == "STATION_B"
    assert len(result.legs) == 1
    assert result.legs[0].trip_id == "WEEKDAY_TRIP_1"


def test_find_next_service_unknown_station_returns_none(tmp_path, sample_static_zip_bytes):
    weekday = date(2026, 7, 20)
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes, pin_date=weekday)
    now = gtfs_time_to_utc(weekday, "07:00:00")

    assert cache.find_next_service("Not A Real Station", "B Station", now) is None


def test_find_next_service_no_route_found_when_no_connecting_trip(tmp_path, sample_static_zip_bytes):
    # Saturday: only WEEKEND service is active, but WEEKEND trips run the
    # same A<->B pattern as WEEKDAY -- instead prove the no_route_found
    # path by asking well after every scheduled trip today.
    weekday = date(2026, 7, 20)
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes, pin_date=weekday)
    now = gtfs_time_to_utc(weekday, "23:00:00")

    result = cache.find_next_service("A Station", "B Station", now)

    assert result is not None
    assert result.reason == "no_route_found"
    assert result.legs == []


def test_stations_for_lists_every_station_with_its_routes(tmp_path, sample_static_zip_bytes):
    weekday = date(2026, 7, 20)
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes, pin_date=weekday)
    now = gtfs_time_to_utc(weekday, "07:00:00")

    stations = cache.stations_for(now)

    assert {s.station_id for s in stations} == {"STATION_A", "STATION_B"}
    station_a = next(s for s in stations if s.station_id == "STATION_A")
    assert "2-PKM" in {r.route_id for r in station_a.routes}
