import io
import os
import zipfile
from datetime import date, datetime, timedelta, timezone

from traintracker.gtfs import repin
from traintracker.gtfs.pinning import PinManifest
from traintracker.gtfs.repin import SnapshotSelector
from traintracker.gtfs.snapshot import StaticSnapshot
from traintracker.state.merge import TrainSnapshot

SERVICE_DATE = date(2026, 10, 2)
# 13:00 Melbourne, well past both the 3am boundary and the unpinned grace.
MIDDAY = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)
CALENDAR = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
    "S,1,1,1,1,1,1,1,20260101,20261231\n"
)


def _store(gtfs_dir, trip_ids, age_s=0):
    trips = "route_id,service_id,trip_id\n" + "".join(f"R,S,{t}\n" for t in trip_ids)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("calendar.txt", CALENDAR)
        zf.writestr("trips.txt", trips)
    data = buf.getvalue()
    digest = StaticSnapshot.from_zip_bytes(data).digest
    path = gtfs_dir / f"{digest}.zip"
    path.write_bytes(data)
    mtime = path.stat().st_mtime - age_s
    os.utime(path, (mtime, mtime))
    return digest


def _live(trip_ids, start_date="20261002", relationship=None):
    return {
        t: TrainSnapshot(
            trip_id=t, route_id="R", start_time="08:00:00", start_date=start_date,
            schedule_relationship=relationship, stop_time_updates=(),
            schedule_updated_at=MIDDAY,
            latitude=None, longitude=None, bearing=None, position_updated_at=None,
        )
        for t in trip_ids
    }


def _ids(prefix, n=30):
    return [f"{prefix}-{i}" for i in range(n)]


def _setup(tmp_path):
    manifest = PinManifest(tmp_path / "pin_manifest.json")
    return manifest, SnapshotSelector(tmp_path, manifest)


def test_healthy_pin_is_left_alone(tmp_path):
    manifest, selector = _setup(tmp_path)
    digest = _store(tmp_path, _ids("a"))
    _store(tmp_path, _ids("b"))
    manifest.pin_digest(SERVICE_DATE, digest)

    check = selector.check(_live(_ids("a")), MIDDAY)

    assert check.join_pct == 100.0
    assert check.repinned_to is None
    assert manifest.get(SERVICE_DATE).digest == digest


def test_mismatched_pin_moves_to_an_older_stored_snapshot_that_matches(tmp_path):
    manifest, selector = _setup(tmp_path)
    older = _store(tmp_path, _ids("live"), age_s=7 * 86400)
    newer = _store(tmp_path, _ids("other"))
    manifest.pin_digest(SERVICE_DATE, newer)

    check = selector.check(_live(_ids("live")), MIDDAY)

    assert check.join_pct == 0.0
    assert check.repinned_to == older
    assert check.repinned_join_pct == 100.0
    pin = manifest.get(SERVICE_DATE)
    assert pin.digest == older
    assert pin.replaced_digest == newer


def test_no_better_candidate_keeps_the_pin(tmp_path):
    manifest, selector = _setup(tmp_path)
    pinned = _store(tmp_path, _ids("x"))
    _store(tmp_path, _ids("y"))
    manifest.pin_digest(SERVICE_DATE, pinned)

    check = selector.check(_live(_ids("live")), MIDDAY)

    assert check.repinned_to is None
    assert manifest.get(SERVICE_DATE).digest == pinned


def test_small_improvement_is_not_enough_to_repin(tmp_path):
    manifest, selector = _setup(tmp_path)
    live = _ids("live", 40)
    pinned = _store(tmp_path, live[:32])  # 80%
    _store(tmp_path, live[:35])  # 87.5%: better, but under the margin
    manifest.pin_digest(SERVICE_DATE, pinned)

    check = selector.check(_live(live), MIDDAY)

    assert check.join_pct == 80.0
    assert check.repinned_to is None


def test_unpinned_day_waits_for_the_nightly_job_then_pins_best_match(tmp_path):
    manifest, selector = _setup(tmp_path)
    match = _store(tmp_path, _ids("live"))
    _store(tmp_path, _ids("other"))
    just_after_boundary = datetime(2026, 10, 1, 17, 30, tzinfo=timezone.utc)  # 03:30 local

    assert selector.check(_live(_ids("live")), just_after_boundary) is None
    assert manifest.get(SERVICE_DATE) is None

    check = selector.check(_live(_ids("live")), MIDDAY)

    assert check.pinned_digest is None
    assert check.repinned_to == match
    assert manifest.get(SERVICE_DATE).replaced_digest is None


def test_too_few_live_trips_skips_the_check(tmp_path):
    manifest, selector = _setup(tmp_path)
    manifest.pin_digest(SERVICE_DATE, _store(tmp_path, _ids("x")))
    _store(tmp_path, _ids("live"))

    few = _ids("live", repin.MIN_LIVE_TRIPS - 1)
    assert selector.check(_live(few), MIDDAY) is None


def test_only_scheduled_trips_for_this_service_date_count(tmp_path):
    manifest, selector = _setup(tmp_path)
    digest = _store(tmp_path, _ids("a"))
    manifest.pin_digest(SERVICE_DATE, digest)
    snapshots = {
        **_live(_ids("a")),
        **_live(_ids("yesterday"), start_date="20261001"),
        **_live(_ids("added"), relationship="ADDED"),
    }

    check = selector.check(snapshots, MIDDAY)

    assert check.join_pct == 100.0


def test_maybe_check_is_rate_limited(tmp_path):
    manifest, selector = _setup(tmp_path)
    manifest.pin_digest(SERVICE_DATE, _store(tmp_path, _ids("a")))
    live = _live(_ids("a"))

    assert selector.maybe_check(live, MIDDAY) is not None
    assert selector.maybe_check(live, MIDDAY + timedelta(seconds=60)) is None
    later = MIDDAY + timedelta(seconds=repin.CHECK_INTERVAL_S)
    assert selector.maybe_check(live, later) is not None
