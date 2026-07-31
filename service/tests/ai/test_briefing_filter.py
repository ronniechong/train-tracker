from datetime import datetime, timezone

from traintracker.ai.briefing_filter import has_briefable_alerts
from traintracker.state.alerts import Alert, InformedEntity
from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.store import StateStore

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _store() -> StateStore:
    return StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())


def _alert(alert_id="A1", informed_entities=(), active_periods=()) -> Alert:
    return Alert(
        id=alert_id, cause="OTHER_CAUSE", effect="SIGNIFICANT_DELAYS",
        header_text="Major Delay", description_text=None, url=None,
        active_periods=active_periods, informed_entities=informed_entities,
    )


def test_no_alerts_at_all_is_not_briefable():
    store = _store()
    assert has_briefable_alerts(store, NOW) is False


def test_alert_with_no_informed_entities_is_not_briefable():
    # The real "Major Delay... cannot determine which specific line(s)"
    # failure this check exists to prevent.
    store = _store()
    store.latest_alerts = {"A1": _alert(informed_entities=())}
    assert has_briefable_alerts(store, NOW) is False


def test_alert_with_informed_entity_but_no_route_id_is_not_briefable():
    # A stop-only or direction-only entity still leaves nothing to name.
    store = _store()
    store.latest_alerts = {
        "A1": _alert(informed_entities=(InformedEntity(route_id=None, stop_id="S1", direction_id=None),))
    }
    assert has_briefable_alerts(store, NOW) is False


def test_alert_with_a_real_route_id_is_briefable():
    store = _store()
    store.latest_alerts = {
        "A1": _alert(informed_entities=(InformedEntity(route_id="2-BEG", stop_id=None, direction_id=None),))
    }
    assert has_briefable_alerts(store, NOW) is True


def test_inactive_alert_is_not_briefable_even_with_a_route_id():
    from traintracker.state.alerts import ActivePeriod
    store = _store()
    expired = ActivePeriod(start=None, end=NOW.replace(year=2020))
    store.latest_alerts = {
        "A1": _alert(
            informed_entities=(InformedEntity(route_id="2-BEG", stop_id=None, direction_id=None),),
            active_periods=(expired,),
        )
    }
    assert has_briefable_alerts(store, NOW) is False


def test_one_briefable_alert_among_several_unbriefable_ones_is_enough():
    store = _store()
    store.latest_alerts = {
        "A1": _alert(alert_id="A1", informed_entities=()),
        "A2": _alert(alert_id="A2", informed_entities=(InformedEntity(route_id="2-CRB", stop_id=None, direction_id=None),)),
    }
    assert has_briefable_alerts(store, NOW) is True
