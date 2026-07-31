import hashlib
from datetime import datetime, timezone

from traintracker.gtfs.gtfstime import service_date_for_instant
from traintracker.gtfs.pinning import PinManifest
from traintracker.gtfs.schedule_cache import PinnedScheduleCache


def _pinned_schedule_cache(tmp_path, sample_static_zip_bytes) -> PinnedScheduleCache:
    digest = hashlib.sha256(sample_static_zip_bytes).hexdigest()
    (tmp_path / f"{digest}.zip").write_bytes(sample_static_zip_bytes)
    manifest = PinManifest(tmp_path / "pin_manifest.json")
    manifest.pin_digest(service_date_for_instant(datetime.now(timezone.utc)), digest)
    return PinnedScheduleCache(tmp_path, manifest)


def test_routes_for_returns_parsed_routes(tmp_path, sample_static_zip_bytes):
    cache = _pinned_schedule_cache(tmp_path, sample_static_zip_bytes)

    routes = cache.routes_for(datetime.now(timezone.utc))

    assert routes["2-PKM"].short_name == "Pakenham"
    assert routes["2-CRB"].short_name == "Craigieburn"
