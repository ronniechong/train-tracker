from datetime import datetime, timezone

from traintracker.state.merge import merge


def _tu_feed(header_ts: str, entities: list[dict]) -> dict:
    return {"header": {"timestamp": header_ts}, "entity": entities}


def _vp_feed(header_ts: str, entities: list[dict]) -> dict:
    return {"header": {"timestamp": header_ts}, "entity": entities}


def _tu_entity(trip_id, route_id="aus:vic:vic-02-CBE:", start_time="19:01:00",
                start_date="20260718", schedule_relationship="SCHEDULED",
                stop_time_update=None):
    return {
        "id": f"{trip_id}|{start_date}",
        "trip_update": {
            "trip": {
                "trip_id": trip_id,
                "start_time": start_time,
                "start_date": start_date,
                "schedule_relationship": schedule_relationship,
                "route_id": route_id,
            },
            "stop_time_update": stop_time_update or [],
        },
    }


def _vp_entity(trip_id, route_id="aus:vic:vic-02-CBE:", lat=-37.81, lon=144.96,
               bearing=90.0, timestamp="1784500000"):
    return {
        "id": trip_id,
        "vehicle": {
            "trip": {"trip_id": trip_id, "route_id": route_id},
            "position": {"latitude": lat, "longitude": lon, "bearing": bearing},
            "timestamp": timestamp,
            "vehicle": {"id": "some-vehicle"},
        },
    }


def test_trip_present_in_both_feeds_merges_cleanly():
    tu = _tu_feed("1784500010", [_tu_entity("trip-1")])
    vp = _vp_feed("1784500005", [_vp_entity("trip-1")])

    snapshots, discrepancies = merge(tu, vp)

    assert discrepancies == []
    snap = snapshots["trip-1"]
    assert snap.has_schedule and snap.has_position
    assert snap.route_id == "aus:vic:vic-02-CBE:"
    assert snap.latitude == -37.81
    assert snap.schedule_updated_at == datetime.fromtimestamp(1784500010, tz=timezone.utc)
    # Position freshness comes from the VP entity's own timestamp, not the
    # feed header - the two feeds are polled at different cadences.
    assert snap.position_updated_at == datetime.fromtimestamp(1784500000, tz=timezone.utc)


def test_trip_known_only_to_tu_has_no_position_and_no_discrepancy():
    tu = _tu_feed("1784500010", [_tu_entity("trip-ghost")])
    vp = _vp_feed("1784500010", [])

    snapshots, discrepancies = merge(tu, vp)

    snap = snapshots["trip-ghost"]
    assert snap.has_schedule is True
    assert snap.has_position is False
    assert snap.latitude is None
    assert snap.position_updated_at is None
    # TU-without-VP is the normal coasting/ghost baseline, not a conflict.
    assert discrepancies == []


def test_trip_known_only_to_vp_logs_vp_without_tu():
    tu = _tu_feed("1784500010", [])
    vp = _vp_feed("1784500010", [_vp_entity("trip-unscheduled")])

    snapshots, discrepancies = merge(tu, vp)

    snap = snapshots["trip-unscheduled"]
    assert snap.has_schedule is False
    assert snap.has_position is True
    assert len(discrepancies) == 1
    d = discrepancies[0]
    assert d.trip_id == "trip-unscheduled"
    assert d.discrepancy_type == "vp_without_tu"
    assert d.tu_value is None
    assert d.vp_value == "aus:vic:vic-02-CBE:"


def test_route_id_mismatch_is_logged_but_tu_value_wins_in_snapshot():
    tu = _tu_feed("1784500010", [_tu_entity("trip-1", route_id="aus:vic:vic-02-CBE:")])
    vp = _vp_feed("1784500010", [_vp_entity("trip-1", route_id="aus:vic:vic-02-BEG:")])

    snapshots, discrepancies = merge(tu, vp)

    assert snapshots["trip-1"].route_id == "aus:vic:vic-02-CBE:"  # TU-primary
    assert len(discrepancies) == 1
    d = discrepancies[0]
    assert d.discrepancy_type == "route_id_mismatch"
    assert d.tu_value == "aus:vic:vic-02-CBE:"
    assert d.vp_value == "aus:vic:vic-02-BEG:"


def test_start_time_and_start_date_mismatches_are_logged_independently():
    tu = _tu_feed("1784500010", [_tu_entity("trip-1", start_time="19:01:00", start_date="20260718")])
    vp_entity = _vp_entity("trip-1")
    vp_entity["vehicle"]["trip"]["start_time"] = "19:05:00"
    vp_entity["vehicle"]["trip"]["start_date"] = "20260719"
    vp = _vp_feed("1784500010", [vp_entity])

    _, discrepancies = merge(tu, vp)

    types = {d.discrepancy_type for d in discrepancies}
    assert types == {"start_time_mismatch", "start_date_mismatch"}


def test_stop_time_updates_are_parsed_in_order():
    stus = [
        {"stop_sequence": 1, "stop_id": "A", "departure": {"delay": 0, "time": "1784500100"},
         "schedule_relationship": "SCHEDULED"},
        {"stop_sequence": 2, "stop_id": "B", "arrival": {"delay": 60, "time": "1784500200"},
         "departure": {"delay": 60, "time": "1784500230"}, "schedule_relationship": "SCHEDULED"},
    ]
    tu = _tu_feed("1784500010", [_tu_entity("trip-1", stop_time_update=stus)])
    vp = _vp_feed("1784500010", [])

    snapshots, _ = merge(tu, vp)

    parsed = snapshots["trip-1"].stop_time_updates
    assert len(parsed) == 2
    assert parsed[0].stop_sequence == 1
    assert parsed[0].stop_id == "A"
    assert parsed[0].arrival_time is None
    assert parsed[0].departure_delay == 0
    assert parsed[1].stop_sequence == 2
    assert parsed[1].arrival_delay == 60
    assert parsed[1].departure_time == "1784500230"


def test_vp_entity_missing_per_entity_timestamp_falls_back_to_header():
    tu = _tu_feed("1784500010", [])
    vp_entity = _vp_entity("trip-1")
    del vp_entity["vehicle"]["timestamp"]
    vp = _vp_feed("1784500099", [vp_entity])

    snapshots, _ = merge(tu, vp)

    assert snapshots["trip-1"].position_updated_at == datetime.fromtimestamp(1784500099, tz=timezone.utc)


def test_empty_feeds_produce_no_snapshots():
    snapshots, discrepancies = merge(_tu_feed("1784500010", []), _vp_feed("1784500010", []))
    assert snapshots == {}
    assert discrepancies == []
