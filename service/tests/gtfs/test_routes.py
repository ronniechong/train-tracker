from traintracker.gtfs.routes import find_routes_by_name, replacement_bus_route_id


def test_parse_routes_via_sample_fixture(sample_routes):
    assert sample_routes["2-PKM"].short_name == "Pakenham"
    assert sample_routes["2-PKM"].long_name == "Pakenham - City"
    assert sample_routes["2-CRB"].short_name == "Craigieburn"


def test_find_routes_by_name_exact_match(sample_routes):
    matches = find_routes_by_name(sample_routes, "Pakenham")
    assert [r.route_id for r in matches] == ["2-PKM"]


def test_find_routes_by_name_case_insensitive(sample_routes):
    matches = find_routes_by_name(sample_routes, "pakenham")
    assert [r.route_id for r in matches] == ["2-PKM"]


def test_find_routes_by_name_substring_fallback(sample_routes):
    matches = find_routes_by_name(sample_routes, "cra")
    assert [r.route_id for r in matches] == ["2-CRB"]


def test_find_routes_by_name_never_matches_replacement_bus(sample_routes):
    assert find_routes_by_name(sample_routes, "Replacement Bus") == []


def test_find_routes_by_name_no_match_returns_empty(sample_routes):
    assert find_routes_by_name(sample_routes, "Not A Real Line") == []


def test_find_routes_by_name_blank_returns_empty(sample_routes):
    assert find_routes_by_name(sample_routes, "   ") == []


def test_replacement_bus_route_id():
    # Real route_ids are colon-terminated URNs -- the sample fixture's
    # bare "2-PKM" style ids are a test convenience, not the real shape.
    assert replacement_bus_route_id("aus:vic:vic-02-BEG:") == "aus:vic:vic-02-BEG-R:"
