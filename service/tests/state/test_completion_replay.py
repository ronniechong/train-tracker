"""Real-data replay harness for `TripCompletionTracker` -- hand-built
`StopTimeUpdate` fixtures using a real int previously hid an `arrival_time`
string-coercion bug. This validates end-to-end against a genuine capture
(a few hours of TU/VP polls, 100% success), fed through the real `merge` ->
`TripCompletionTracker` pipeline with a real (trimmed) static schedule for
terminus resolution -- mirrors `test_replay.py`'s precedent for
`station.py`/`ghost.py`: real data catches what hand-built fixtures miss.

`schedule/` is NOT the full static snapshot paired with this capture --
`stop_times.txt`/`trips.txt` are filtered to just the trip_ids actually
seen in this capture's Trip Updates (`calendar.txt`/`calendar_dates.txt`/
`routes.txt`/`stops.txt` kept whole; all small). Real rows for real trips,
not synthesized, just scoped down. Committed as loose `.txt` files, zipped
in-memory below, same as `conftest.py`'s `sample_static_zip_bytes` --
`*.zip` is gitignored repo-wide, so a committed fixture can't be a real
`.zip` file without carving out a gitignore exception this avoids needing.
"""

import gzip
import hashlib
import io
import json
import zipfile
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from traintracker.gtfs.pinning import PinManifest
from traintracker.gtfs.schedule_cache import PinnedScheduleCache
from traintracker.state.completion import TripCompletionTracker
from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.store import StateStore

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "replay_20260801"
SCHEDULE_DIR = FIXTURE_DIR / "schedule"
SERVICE_DATE = date(2026, 8, 1)

# The 2 real trip_ids seen in this capture's TU feed with no static
# stop_times row at all -- confirmed real-time-only ADDED trips (their
# trip_ids follow the `vic:<ROUTE>:_:...` real-time-generated shape, not the
# static portal's `NN-XXX--NN-TN-NNNN` shape), not a fixture-building bug.
KNOWN_ADDED_TRIP_IDS = {
    "vic:02FKN:_:H:vpt._Frankston_7481_20260801",
    "vic:02WER:_:R:vpt._Werribee_7070_20260801",
}


def _load_records(path: Path) -> list[dict]:
    with gzip.open(path, "rt") as f:
        return [json.loads(line) for line in f]


def _zip_schedule_dir(directory: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for txt_file in directory.glob("*.txt"):
            zf.write(txt_file, arcname=txt_file.name)
    return buf.getvalue()


def _pinned_schedule_cache(tmp_path: Path) -> PinnedScheduleCache:
    schedule_bytes = _zip_schedule_dir(SCHEDULE_DIR)
    digest = hashlib.sha256(schedule_bytes).hexdigest()
    (tmp_path / f"{digest}.zip").write_bytes(schedule_bytes)
    manifest = PinManifest(tmp_path / "pin_manifest.json")
    manifest.pin_digest(SERVICE_DATE, digest)
    return PinnedScheduleCache(tmp_path, manifest)


def test_replay_fixture_produces_real_completion_outcomes(tmp_path):
    cache = _pinned_schedule_cache(tmp_path)

    tu_records = _load_records(FIXTURE_DIR / "trip_updates.ndjson.gz")
    vp_records = _load_records(FIXTURE_DIR / "vehicle_positions.ndjson.gz")

    events = (
        [("tu", r) for r in tu_records] + [("vp", r) for r in vp_records]
    )
    events.sort(key=lambda e: e[1]["fetch_timestamp"])

    discrepancy_log = InMemoryEventLog()
    ghost_log = InMemoryEventLog()
    completion_log = InMemoryEventLog()
    store = StateStore(discrepancy_log, ghost_log)
    tracker = TripCompletionTracker(completion_log, cache.terminus_for)

    latest_tu = {"header": {"timestamp": None}, "entity": []}
    latest_vp = {"header": {"timestamp": None}, "entity": []}
    last_cycle_time = None

    for kind, rec in events:
        cycle_time = datetime.fromisoformat(rec["fetch_timestamp"])
        last_cycle_time = cycle_time
        if kind == "tu":
            latest_tu = rec["feed"]
        else:
            latest_vp = rec["feed"]

        snapshots = store.ingest(latest_tu, latest_vp, cycle_time)
        tracker.tick(snapshots, cycle_time)

    store.flush(at=last_cycle_time)
    tracker.flush(at=last_cycle_time)

    counts = Counter(e.status for e in completion_log.events)

    # 484 distinct trip_ids appear in this capture's TU feed; 2 are genuine
    # ADDED trips with no static row (asserted below) and are never
    # registered at all -- 482 tracked trips, each finalized exactly once.
    assert len(completion_log.events) == 482
    trip_ids_seen = {e.trip_id for e in completion_log.events}
    assert len(trip_ids_seen) == 482  # no double-emission across the whole replay

    # The real breakdown for this slice, hand-verified against the raw
    # capture before writing this assertion: 305 on_time, 6 late, 0 cancelled,
    # 171 undetermined_gap. Asserted exactly, not as a loose range --
    # this is a fixed, real recording, so the pipeline should reproduce it
    # deterministically every run; a changed count here means a real
    # regression in merge/terminus-resolution/completion logic, not noise.
    assert counts == Counter(on_time=305, late=6, undetermined_gap=171)

    # Every one of the 171 undetermined_gap events is a pure capture-window
    # truncation artifact (trip scheduled to terminate AFTER the capture's
    # last cycle_time), not a genuine mid-window coverage gap -- confirmed
    # by cross-checking each gap's scheduled_terminus_arrival against
    # last_cycle_time. A real mid-window gap appearing here in a future rerun
    # would mean a genuine coverage or tracking regression, not a fixture
    # artifact, and is worth investigating rather than re-widening this
    # assertion.
    gaps = [e for e in completion_log.events if e.status == "undetermined_gap"]
    tz = last_cycle_time.tzinfo
    assert all(e.scheduled_terminus_arrival >= last_cycle_time.replace(tzinfo=tz) for e in gaps)

    # This 3h07m slice happened to contain zero cancellations -- a real,
    # honest property of this specific recording, not an assumption the
    # tracker enforces. `cancelled_trip_
    # finalizes_immediately_not_as_undetermined_gap` in test_completion.py
    # is what actually exercises that path.
    assert counts["cancelled"] == 0

    # ADDED trips (no static schedule) are never registered for completion
    # tracking at all -- ai/tools.py's own get_trip already special-cases
    # ADDED trips at the API layer; this confirms the completion tracker's
    # independent "no terminus, no tracking" rule (completion.py's own
    # documented, deliberate scope limit) holds against real ADDED trip_ids,
    # not just a hand-built one in test_completion.py.
    assert trip_ids_seen.isdisjoint(KNOWN_ADDED_TRIP_IDS)

    # Spot-check two concrete, real outcomes end to end -- exact values
    # independently read off the raw capture, not derived from the code
    # under test.
    by_trip = {e.trip_id: e for e in completion_log.events}

    exactly_on_time = by_trip["02-SUY--57-T2-Z036"]
    assert exactly_on_time.status == "on_time"
    assert exactly_on_time.delay_seconds == 0
    assert exactly_on_time.route_id == "aus:vic:vic-02-SUY:"

    genuinely_late = by_trip["02-WER--57-T2-6413"]
    assert genuinely_late.status == "late"
    assert genuinely_late.delay_seconds == 2280  # 38 minutes late, a real disruption-sized delay
    assert genuinely_late.route_id == "aus:vic:vic-02-WER:"
