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


def test_routes_serving_all_stops_resolves_by_parent_station(
    tmp_path, sample_static_zip_bytes
):
    # stop_times.txt only keys on the platform-level stop_id (PLAT_A1), but
    # Service Alerts carry the PARENT STATION id instead (STATION_A, real
    # live shape verified 2026-08-04) -- proves the parent->platform
    # indexing resolves anything at all rather than an unindexed id
    # silently returning []. Both fixture routes call at PLAT_A1, so the
    # match set is non-empty but still shared, same as the platform-level
    # ambiguous case below.
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)

    matched = cache.routes_serving_all_stops(datetime.now(timezone.utc), ["STATION_A"])

    assert {r.route_id for r in matched} == {"2-PKM", "2-CRB"}


def test_routes_serving_all_stops_resolves_a_line_uniquely_served_by_one_route(tmp_path):
    # Minimal, self-contained schedule where ONLY_ROUTE's platforms are
    # never shared with any other route -- the real production case this
    # feature exists for (a single cancelled trip's stop list should
    # resolve to exactly one line).
    cache = _pinned_cache_from_files(
        tmp_path,
        {
            "routes.txt": (
                "route_id,route_short_name,route_long_name\n"
                "ONLY_ROUTE,Only,Only - City\n"
            ),
            "trips.txt": (
                "route_id,service_id,trip_id,trip_headsign,direction_id\n"
                "ONLY_ROUTE,WEEKDAY,TRIP_1,City,0\n"
            ),
            "stop_times.txt": (
                "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n"
                "TRIP_1,1,PLATFORM_ONLY,08:00:00,08:00:00\n"
                "TRIP_1,2,PLATFORM_TWO,08:10:00,\n"
            ),
            "stops.txt": (
                "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
                "STATION_ONLY,Only Station,-37.8,144.9,1,\n"
                "PLATFORM_ONLY,Only Station Platform,-37.8,144.9,0,STATION_ONLY\n"
                "PLATFORM_TWO,Two Station Platform,-37.9,144.9,0,\n"
            ),
            "calendar.txt": (
                "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
                "start_date,end_date\n"
                "WEEKDAY,1,1,1,1,1,0,0,20260101,20261231\n"
            ),
            "calendar_dates.txt": "service_id,date,exception_type\n",
        },
    )

    matched = cache.routes_serving_all_stops(
        datetime.now(timezone.utc), ["STATION_ONLY", "PLATFORM_TWO"]
    )

    assert {r.route_id for r in matched} == {"ONLY_ROUTE"}


def test_routes_serving_all_stops_is_ambiguous_when_stops_are_shared(
    tmp_path, sample_static_zip_bytes
):
    # PLAT_A1/PLAT_B1 both get trips from 2-PKM AND 2-CRB in the fixture --
    # matches the real live case (informed_entity lists a single trip's
    # stops, no service_date filtering here), so intersecting on them alone
    # is genuinely ambiguous.
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)

    matched = cache.routes_serving_all_stops(
        datetime.now(timezone.utc), ["PLAT_A1", "PLAT_B1"]
    )

    assert {r.route_id for r in matched} == {"2-PKM", "2-CRB"}


def test_routes_serving_all_stops_returns_empty_for_unknown_stop(
    tmp_path, sample_static_zip_bytes
):
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)

    matched = cache.routes_serving_all_stops(
        datetime.now(timezone.utc), ["PLAT_A1", "NOT_A_REAL_STOP"]
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
