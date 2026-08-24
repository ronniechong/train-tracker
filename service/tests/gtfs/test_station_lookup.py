from traintracker.gtfs.station_lookup import (
    canonical_stations,
    find_stations_by_name,
    narrow_by_route,
)


def test_canonical_stations_excludes_platforms(sample_stops):
    stations = canonical_stations(sample_stops)
    assert stations == {"STATION_A": "A Station", "STATION_B": "B Station"}


def test_find_stations_by_name_exact_match(sample_stops):
    matches = find_stations_by_name(sample_stops, "A Station")
    assert [m.station_id for m in matches] == ["STATION_A"]


def test_find_stations_by_name_case_insensitive(sample_stops):
    matches = find_stations_by_name(sample_stops, "a station")
    assert [m.station_id for m in matches] == ["STATION_A"]


def test_find_stations_by_name_substring_fallback_is_ambiguous(sample_stops):
    # Both sample stations share "Station" as a substring -- exact tier
    # finds nothing, so this falls back to the substring tier and returns
    # both, same two-tier shape as find_routes_by_name.
    matches = find_stations_by_name(sample_stops, "Station")
    assert {m.station_id for m in matches} == {"STATION_A", "STATION_B"}


def test_find_stations_by_name_no_match_returns_empty(sample_stops):
    assert find_stations_by_name(sample_stops, "Not A Real Station") == []


def test_find_stations_by_name_blank_returns_empty(sample_stops):
    assert find_stations_by_name(sample_stops, "   ") == []


def test_narrow_by_route_filters_to_serving_stations(sample_stops):
    matches = find_stations_by_name(sample_stops, "Station")
    stop_routes = {"STATION_A": frozenset({"R1"}), "STATION_B": frozenset({"R2"})}
    narrowed = narrow_by_route(matches, {"R1"}, stop_routes)
    assert [m.station_id for m in narrowed] == ["STATION_A"]


def test_narrow_by_route_falls_back_to_original_when_nothing_matches(sample_stops):
    matches = find_stations_by_name(sample_stops, "Station")
    stop_routes = {"STATION_A": frozenset({"R1"}), "STATION_B": frozenset({"R2"})}
    narrowed = narrow_by_route(matches, {"NONEXISTENT"}, stop_routes)
    assert {m.station_id for m in narrowed} == {"STATION_A", "STATION_B"}
