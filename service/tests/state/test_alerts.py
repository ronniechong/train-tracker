from datetime import datetime, timezone

from traintracker.state.alerts import alerts_matching, is_active, parse_alerts


def _sa_feed(entities: list[dict]) -> dict:
    return {"header": {"timestamp": "1784500000"}, "entity": entities}


def _alert_entity(
    entity_id="alert-1",
    cause="CONSTRUCTION",
    effect="MODIFIED_SERVICE",
    header_text="Buses replace trains",
    active_period=None,
    informed_entity=None,
):
    return {
        "id": entity_id,
        "alert": {
            "cause": cause,
            "effect": effect,
            "header_text": {"translation": [{"language": "en", "text": header_text}]},
            "description_text": {"translation": [{"language": "en", "text": "details"}]},
            "url": {"translation": [{"language": "en", "text": "https://example.invalid/d/1"}]},
            "active_period": active_period if active_period is not None else [],
            "informed_entity": informed_entity if informed_entity is not None else [],
        },
    }


def test_parse_alerts_keyed_by_entity_id():
    feed = _sa_feed([_alert_entity(entity_id="a1"), _alert_entity(entity_id="a2")])

    alerts = parse_alerts(feed)

    assert set(alerts) == {"a1", "a2"}
    assert alerts["a1"].cause == "CONSTRUCTION"
    assert alerts["a1"].effect == "MODIFIED_SERVICE"
    assert alerts["a1"].header_text == "Buses replace trains"
    assert alerts["a1"].url == "https://example.invalid/d/1"


def test_parse_alerts_skips_entities_without_an_alert_payload():
    feed = _sa_feed([{"id": "not-an-alert", "trip_update": {}}])

    assert parse_alerts(feed) == {}


def test_parse_alerts_extracts_active_periods_and_informed_entities():
    feed = _sa_feed(
        [
            _alert_entity(
                active_period=[{"start": "1784284200", "end": "1784469540"}],
                informed_entity=[{"route_id": "R1", "stop_id": "S1", "direction_id": 0}],
            )
        ]
    )

    alert = parse_alerts(feed)["alert-1"]

    period = alert.active_periods[0]
    assert period.start == datetime.fromtimestamp(1784284200, tz=timezone.utc)
    assert period.end == datetime.fromtimestamp(1784469540, tz=timezone.utc)
    entity = alert.informed_entities[0]
    assert entity.route_id == "R1"
    assert entity.stop_id == "S1"
    assert entity.direction_id == 0


def test_is_active_with_no_active_period_means_always_active():
    alert = parse_alerts(_sa_feed([_alert_entity(active_period=[])]))["alert-1"]

    assert is_active(alert, datetime(2000, 1, 1, tzinfo=timezone.utc)) is True
    assert is_active(alert, datetime(2100, 1, 1, tzinfo=timezone.utc)) is True


def test_is_active_respects_start_and_end_bounds():
    feed = _sa_feed(
        [_alert_entity(active_period=[{"start": "1784284200", "end": "1784469540"}])]
    )
    alert = parse_alerts(feed)["alert-1"]

    before = datetime.fromtimestamp(1784284200 - 10, tz=timezone.utc)
    during = datetime.fromtimestamp(1784284200 + 10, tz=timezone.utc)
    after = datetime.fromtimestamp(1784469540 + 10, tz=timezone.utc)

    assert is_active(alert, before) is False
    assert is_active(alert, during) is True
    assert is_active(alert, after) is False


def test_alerts_matching_with_no_filters_returns_every_active_alert():
    now = datetime.fromtimestamp(1784500000, tz=timezone.utc)
    feed = _sa_feed(
        [_alert_entity(entity_id="a1", informed_entity=[{"route_id": "R1"}])]
    )
    alerts = parse_alerts(feed)

    assert [a.id for a in alerts_matching(alerts, now)] == ["a1"]


def test_alerts_matching_filters_by_route_id():
    now = datetime.fromtimestamp(1784500000, tz=timezone.utc)
    feed = _sa_feed(
        [
            _alert_entity(entity_id="on-r1", informed_entity=[{"route_id": "R1"}]),
            _alert_entity(entity_id="on-r2", informed_entity=[{"route_id": "R2"}]),
        ]
    )
    alerts = parse_alerts(feed)

    matched = alerts_matching(alerts, now, route_id="R1")

    assert [a.id for a in matched] == ["on-r1"]


def test_alerts_matching_treats_missing_field_on_either_side_as_wildcard():
    now = datetime.fromtimestamp(1784500000, tz=timezone.utc)
    # informed_entity has no stop_id at all -- applies to every stop on R1.
    feed = _sa_feed(
        [_alert_entity(entity_id="a1", informed_entity=[{"route_id": "R1"}])]
    )
    alerts = parse_alerts(feed)

    matched = alerts_matching(alerts, now, route_id="R1", stop_id="ANY_STOP")

    assert [a.id for a in matched] == ["a1"]


def test_alerts_matching_excludes_alerts_outside_their_active_period():
    feed = _sa_feed(
        [_alert_entity(active_period=[{"start": "1784284200", "end": "1784469540"}])]
    )
    alerts = parse_alerts(feed)
    after_it_ended = datetime.fromtimestamp(1784469540 + 100, tz=timezone.utc)

    assert alerts_matching(alerts, after_it_ended) == []


def test_alerts_matching_network_wide_alert_matches_any_filter():
    now = datetime.fromtimestamp(1784500000, tz=timezone.utc)
    feed = _sa_feed([_alert_entity(informed_entity=[])])
    alerts = parse_alerts(feed)

    assert [a.id for a in alerts_matching(alerts, now, route_id="ANYTHING")] == ["alert-1"]


def test_alerts_matching_orders_newest_active_period_first():
    now = datetime.fromtimestamp(1784500000, tz=timezone.utc)
    feed = _sa_feed(
        [
            _alert_entity(
                entity_id="oldest", active_period=[{"start": "1784100000", "end": "1784600000"}],
            ),
            _alert_entity(
                entity_id="newest", active_period=[{"start": "1784400000", "end": "1784600000"}],
            ),
            _alert_entity(
                entity_id="middle", active_period=[{"start": "1784200000", "end": "1784600000"}],
            ),
        ]
    )
    alerts = parse_alerts(feed)

    matched = alerts_matching(alerts, now)

    assert [a.id for a in matched] == ["newest", "middle", "oldest"]


def test_alerts_matching_uses_latest_period_start_for_recurring_alerts():
    # A recurring alert (e.g. weekend trackwork) with several active
    # periods ranks by its most recent activation, not its first one.
    now = datetime.fromtimestamp(1784500000, tz=timezone.utc)
    feed = _sa_feed(
        [
            _alert_entity(
                entity_id="single-recent",
                active_period=[{"start": "1784300000", "end": "1784600000"}],
            ),
            _alert_entity(
                entity_id="recurring",
                active_period=[
                    {"start": "1784000000", "end": "1784050000"},
                    {"start": "1784450000", "end": "1784600000"},
                ],
            ),
        ]
    )
    alerts = parse_alerts(feed)

    matched = alerts_matching(alerts, now)

    assert [a.id for a in matched] == ["recurring", "single-recent"]


def test_alerts_matching_sorts_alerts_with_no_known_start_last():
    # No active_period at all ("always active" per GTFS-RT spec) carries no
    # recency signal -- it must not be treated as "just now" and jump to
    # the top ahead of alerts that DO have a real, known start.
    now = datetime.fromtimestamp(1784500000, tz=timezone.utc)
    feed = _sa_feed(
        [
            _alert_entity(entity_id="no-known-start", active_period=[]),
            _alert_entity(
                entity_id="has-a-start",
                active_period=[{"start": "1784100000", "end": "1784600000"}],
            ),
        ]
    )
    alerts = parse_alerts(feed)

    matched = alerts_matching(alerts, now)

    assert [a.id for a in matched] == ["has-a-start", "no-known-start"]
