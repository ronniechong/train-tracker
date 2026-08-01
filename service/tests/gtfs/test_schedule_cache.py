import hashlib
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


def test_routes_for_returns_parsed_routes(tmp_path, sample_static_zip_bytes):
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)

    routes = cache.routes_for(datetime.now(timezone.utc))

    assert routes["2-PKM"].short_name == "Pakenham"
    assert routes["2-CRB"].short_name == "Craigieburn"


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
