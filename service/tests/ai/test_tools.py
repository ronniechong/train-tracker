import hashlib
from datetime import datetime, timezone

from traintracker.ai.tools import TOOL_FUNCTIONS, ToolContext, get_active_alerts, get_line_status, get_trip
from traintracker.gtfs.gtfstime import service_date_for_instant
from traintracker.gtfs.pinning import PinManifest
from traintracker.gtfs.schedule_cache import PinnedScheduleCache
from traintracker.state.alerts import ActivePeriod, Alert, InformedEntity
from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.merge import StopTimeUpdate, TrainSnapshot
from traintracker.state.store import StateStore


def _schedule_cache(tmp_path, sample_static_zip_bytes) -> PinnedScheduleCache:
    digest = hashlib.sha256(sample_static_zip_bytes).hexdigest()
    (tmp_path / f"{digest}.zip").write_bytes(sample_static_zip_bytes)
    manifest = PinManifest(tmp_path / "pin_manifest.json")
    manifest.pin_digest(service_date_for_instant(datetime.now(timezone.utc)), digest)
    return PinnedScheduleCache(tmp_path, manifest)


def _snapshot(trip_id, route_id="2-PKM", schedule_relationship="SCHEDULED", stop_time_updates=()) -> TrainSnapshot:
    return TrainSnapshot(
        trip_id=trip_id,
        route_id=route_id,
        start_time=None,
        start_date=None,
        schedule_relationship=schedule_relationship,
        stop_time_updates=stop_time_updates,
        schedule_updated_at=datetime.now(timezone.utc),
        latitude=-37.8,
        longitude=144.9,
        bearing=None,
        position_updated_at=None,
    )


def _ctx(tmp_path, sample_static_zip_bytes) -> ToolContext:
    store = StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())
    return ToolContext(store=store, schedule_cache=_schedule_cache(tmp_path, sample_static_zip_bytes))


async def test_get_trip_returns_untracked_error_for_unknown_trip_id(tmp_path, sample_static_zip_bytes):
    ctx = _ctx(tmp_path, sample_static_zip_bytes)

    result = await get_trip(ctx, trip_id="NOT_A_TRIP")

    assert result == {"error": "trip_id 'NOT_A_TRIP' is not currently tracked"}


async def test_get_trip_returns_full_detail_for_a_tracked_trip(tmp_path, sample_static_zip_bytes):
    ctx = _ctx(tmp_path, sample_static_zip_bytes)
    ctx.store.latest_snapshots["T1"] = _snapshot(
        "T1",
        schedule_relationship="CANCELED",
        stop_time_updates=(
            StopTimeUpdate(
                stop_sequence=1, stop_id="PLAT_A1", arrival_delay=None, arrival_time=None,
                departure_delay=90, departure_time=None, schedule_relationship="SCHEDULED",
            ),
        ),
    )

    result = await get_trip(ctx, trip_id="T1")

    assert result["trip_id"] == "T1"
    assert result["route_id"] == "2-PKM"
    assert result["is_cancelled"] is True
    assert result["is_added"] is False
    assert result["stops"] == [
        {"stop_id": "PLAT_A1", "arrival_delay_s": None, "departure_delay_s": 90, "schedule_relationship": "SCHEDULED"}
    ]


async def test_get_active_alerts_no_filter_returns_all_active(tmp_path, sample_static_zip_bytes):
    ctx = _ctx(tmp_path, sample_static_zip_bytes)
    ctx.store.latest_alerts = {
        "a1": Alert(
            id="a1", cause=None, effect=None, header_text="Disruption A", description_text=None,
            url=None, active_periods=(), informed_entities=(),
        ),
    }

    result = await get_active_alerts(ctx)

    assert [a["id"] for a in result["alerts"]] == ["a1"]


async def test_get_active_alerts_filtered_by_unknown_line_returns_error(tmp_path, sample_static_zip_bytes):
    ctx = _ctx(tmp_path, sample_static_zip_bytes)

    result = await get_active_alerts(ctx, line_name="Not A Real Line")

    assert "error" in result


async def test_get_active_alerts_filtered_by_line_matches_replacement_bus_id_too(
    tmp_path, sample_static_zip_bytes
):
    ctx = _ctx(tmp_path, sample_static_zip_bytes)
    ctx.store.latest_alerts = {
        "bus-alert": Alert(
            id="bus-alert", cause="CONSTRUCTION", effect="REDUCED_SERVICE",
            header_text="Buses replace trains", description_text=None, url=None,
            active_periods=(),
            informed_entities=(InformedEntity(route_id="2-PKM-R:", stop_id=None, direction_id=None),),
        ),
    }

    result = await get_active_alerts(ctx, line_name="Pakenham")

    assert [a["id"] for a in result["alerts"]] == ["bus-alert"]


async def test_get_active_alerts_excludes_expired_alerts(tmp_path, sample_static_zip_bytes):
    ctx = _ctx(tmp_path, sample_static_zip_bytes)
    now = datetime.now(timezone.utc)
    ctx.store.latest_alerts = {
        "expired": Alert(
            id="expired", cause=None, effect=None, header_text="Old", description_text=None, url=None,
            active_periods=(ActivePeriod(start=None, end=now.replace(year=now.year - 1)),),
            informed_entities=(),
        ),
    }

    result = await get_active_alerts(ctx)

    assert result["alerts"] == []


async def test_get_line_status_unknown_line_returns_error(tmp_path, sample_static_zip_bytes):
    ctx = _ctx(tmp_path, sample_static_zip_bytes)

    result = await get_line_status(ctx, line_name="Not A Real Line")

    assert "error" in result


def _tu_entity(trip_id, route_id, schedule_relationship="SCHEDULED"):
    return {
        "id": trip_id,
        "trip_update": {
            "trip": {"trip_id": trip_id, "route_id": route_id, "schedule_relationship": schedule_relationship},
            "stop_time_update": [],
        },
    }


def _vp_entity(trip_id, route_id):
    return {
        "id": trip_id,
        "vehicle": {
            "trip": {"trip_id": trip_id, "route_id": route_id},
            "position": {"latitude": -37.8, "longitude": 144.9},
            "timestamp": "1784500000",
        },
    }


async def test_get_line_status_counts_tracked_trips_and_cancellations(tmp_path, sample_static_zip_bytes):
    ctx = _ctx(tmp_path, sample_static_zip_bytes)
    now = datetime.now(timezone.utc)
    tu_feed = {
        "header": {"timestamp": "1784500000"},
        "entity": [
            _tu_entity("T1", "2-PKM"),
            _tu_entity("T2", "2-PKM", schedule_relationship="CANCELED"),
            _tu_entity("OTHER_LINE", "2-CRB"),
        ],
    }
    vp_feed = {"header": {"timestamp": "1784500000"}, "entity": [_vp_entity("T1", "2-PKM")]}
    ctx.store.ingest(tu_feed, vp_feed, cycle_time=now)

    result = await get_line_status(ctx, line_name="Pakenham")

    assert result["line_name"] == "Pakenham"
    assert set(result["route_ids"]) == {"2-PKM", "2-PKM-R:"}
    assert result["cancelled_trip_ids"] == ["T2"]
    # T1 has both TU+VP this cycle -> live; T2 has TU only -> not yet
    # coasting/ghost (fresh), so it doesn't land in any of the 3 buckets
    # this same cycle -- only T1 should count.
    assert result["tracked_trip_counts"]["live"] == 1


async def test_tool_functions_registry_matches_tools_list():
    from traintracker.ai.tools import TOOLS

    tool_names = {t["name"] for t in TOOLS}
    assert set(TOOL_FUNCTIONS.keys()) == tool_names
