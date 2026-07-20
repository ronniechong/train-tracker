import hashlib
from datetime import date

from traintracker.gtfs.snapshot import StaticSnapshot


def test_trip_ids_are_loaded(sample_snapshot):
    assert sample_snapshot.trip_ids == frozenset(
        {"WEEKDAY_TRIP_1", "WEEKDAY_TRIP_2", "WEEKEND_TRIP_1", "WEEKEND_TRIP_2"}
    )


def test_digest_matches_content(sample_static_zip_bytes, sample_snapshot):
    assert sample_snapshot.digest == hashlib.sha256(sample_static_zip_bytes).hexdigest()


def test_trip_ids_for_service_date_filters_by_active_calendar(sample_snapshot):
    weekday_ids = sample_snapshot.trip_ids_for_service_date(date(2026, 7, 20))  # Monday
    assert weekday_ids == frozenset({"WEEKDAY_TRIP_1", "WEEKDAY_TRIP_2"})

    weekend_ids = sample_snapshot.trip_ids_for_service_date(date(2026, 7, 25))  # Saturday
    assert weekend_ids == frozenset({"WEEKEND_TRIP_1", "WEEKEND_TRIP_2"})


def test_from_zip_path(tmp_path, sample_static_zip_bytes):
    zip_path = tmp_path / "snapshot.zip"
    zip_path.write_bytes(sample_static_zip_bytes)
    snapshot = StaticSnapshot.from_zip_path(zip_path)
    assert snapshot.trip_ids == frozenset(
        {"WEEKDAY_TRIP_1", "WEEKDAY_TRIP_2", "WEEKEND_TRIP_1", "WEEKEND_TRIP_2"}
    )
