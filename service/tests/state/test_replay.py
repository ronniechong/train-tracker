"""Replay harness — the last item in 2d's acceptance criteria: feed the
real 3.5h capture slice through merge -> station-state -> ghost tracking in
correct temporal order and spot-check against what we independently know
about this slice (found via `spike/loop_gap_estimate.py`'s gap list when
the fixture was cut - see milestone 2d's log, 2026-07-21).

Unlike every other test in this package, this one runs on real captured
data, not hand-built fixtures - that's the whole point: two real bugs
(the stop_time_update rolling-window truncation, and the never-seen-live
trip defaulting to "coasting") only surfaced this way.
"""

import gzip
import json
from datetime import datetime
from pathlib import Path

from traintracker.gtfs.stops import parse_stops
from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.station import derive_station_state
from traintracker.state.store import StateStore

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "replay_20260718"


def _load_records(path: Path) -> list[dict]:
    with gzip.open(path, "rt") as f:
        return [json.loads(line) for line in f]


def test_replay_fixture_reproduces_known_ghost_gaps_and_resolves_station_state():
    stops = parse_stops((FIXTURE_DIR / "stops.txt").read_text())
    tu_records = _load_records(FIXTURE_DIR / "trip_updates.ndjson.gz")
    vp_records = _load_records(FIXTURE_DIR / "vehicle_positions.ndjson.gz")

    events = (
        [("tu", r) for r in tu_records] + [("vp", r) for r in vp_records]
    )
    events.sort(key=lambda e: e[1]["fetch_timestamp"])

    discrepancy_log = InMemoryEventLog()
    ghost_log = InMemoryEventLog()
    store = StateStore(discrepancy_log, ghost_log)

    latest_tu = {"header": {"timestamp": None}, "entity": []}
    latest_vp = {"header": {"timestamp": None}, "entity": []}
    last_cycle_time = None

    station_status_counts = {"at": 0, "between": 0, "unknown": 0}

    for kind, rec in events:
        cycle_time = datetime.fromisoformat(rec["fetch_timestamp"])
        last_cycle_time = cycle_time
        if kind == "tu":
            latest_tu = rec["feed"]
        else:
            latest_vp = rec["feed"]

        snapshots = store.ingest(latest_tu, latest_vp, cycle_time)

        # Station-state is a pure per-snapshot function - spot-check it
        # resolves sensibly across real data, not just that it doesn't crash.
        for snap in snapshots.values():
            if not snap.has_schedule:
                continue
            state = derive_station_state(snap, stops, cycle_time)
            station_status_counts[state.status] += 1

    store.flush(at=last_cycle_time)

    # Sanity: a real 3.5h slice produced a non-trivial number of merged
    # snapshots and station-state resolutions, not an empty/degenerate run.
    assert sum(station_status_counts.values()) > 10_000
    # "unknown" should be rare - it only fires when a trip has no usable
    # stop_time_update anchors at all.
    total = sum(station_status_counts.values())
    assert station_status_counts["unknown"] / total < 0.05

    # The two known ~45min gaps found when this fixture was cut (see
    # milestone 2d's log): both must show up as completed ghost episodes,
    # not still-open (flush would leave reappeared_at=None) or missing.
    ghost_events_by_trip = {}
    for event in ghost_log.events:
        ghost_events_by_trip.setdefault(event.trip_id, []).append(event)

    for trip_id in ("02-CBE--55-T3-C440", "02-BEG--55-T3-3156"):
        matching = [e for e in ghost_events_by_trip.get(trip_id, []) if e.reappeared_at is not None]
        assert matching, f"expected a completed ghost episode for {trip_id}"
        # Duration should land in the same ballpark as the raw VP gap
        # (~2700-2750s) minus the coasting window before ghosting starts.
        event = max(matching, key=lambda e: e.ghost_duration_s or 0)
        assert 2000 < event.ghost_duration_s < 2800
        assert event.loop_contained is False  # matches the 2d pre-analysis: ~0% loop-contained

    # Discrepancy log should be non-empty (feeds do disagree sometimes -
    # confirmed on a single cycle earlier) but bounded, not the majority of
    # observations - a blown-up count would indicate a merge regression.
    assert 0 < len(discrepancy_log.events) < total * 0.05
