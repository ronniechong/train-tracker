from datetime import datetime, timedelta, timezone

from traintracker.ai.briefing_trigger import BriefingTrigger
from traintracker.state.alerts import Alert
from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.merge import TrainSnapshot
from traintracker.state.store import StateStore

NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)


def _store() -> StateStore:
    return StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())


def _alert(alert_id="A1", effect="MODIFIED_SERVICE", header_text="Some disruption") -> Alert:
    return Alert(
        id=alert_id, cause="MAINTENANCE", effect=effect, header_text=header_text,
        description_text=None, url=None, active_periods=(), informed_entities=(),
    )


def _snapshot(trip_id, schedule_relationship="SCHEDULED") -> TrainSnapshot:
    return TrainSnapshot(
        trip_id=trip_id, route_id="2-BEG", start_time=None, start_date=None,
        schedule_relationship=schedule_relationship, stop_time_updates=(),
        schedule_updated_at=NOW, latitude=None, longitude=None, bearing=None,
        position_updated_at=None,
    )


def test_no_trigger_on_empty_store():
    trigger = BriefingTrigger()
    store = _store()

    assert trigger.evaluate(store, NOW) is None


def test_new_alert_triggers_once_not_on_repeat_cycles():
    trigger = BriefingTrigger()
    store = _store()
    store.latest_alerts = {"A1": _alert()}

    first = trigger.evaluate(store, NOW)
    assert first is not None
    assert first.kind == "new_alert"

    # Same alert, next cycle, no cooldown recorded yet -- already "seen",
    # must not re-trigger just because it's still active.
    second = trigger.evaluate(store, NOW + timedelta(seconds=10))
    assert second is None


def test_alert_effect_escalating_triggers():
    trigger = BriefingTrigger()
    store = _store()
    store.latest_alerts = {"A1": _alert(effect="MODIFIED_SERVICE")}
    trigger.evaluate(store, NOW)  # absorb as "seen" (new_alert already fired once)

    store.latest_alerts = {"A1": _alert(effect="NO_SERVICE")}
    reason = trigger.evaluate(store, NOW + timedelta(minutes=1))

    assert reason is not None
    assert reason.kind == "alert_escalated"
    assert "MODIFIED_SERVICE -> NO_SERVICE" in reason.detail


def test_alert_effect_improving_does_not_trigger():
    trigger = BriefingTrigger()
    store = _store()
    store.latest_alerts = {"A1": _alert(effect="NO_SERVICE")}
    trigger.evaluate(store, NOW)

    store.latest_alerts = {"A1": _alert(effect="MODIFIED_SERVICE")}
    reason = trigger.evaluate(store, NOW + timedelta(minutes=1))

    assert reason is None


def test_cancellation_threshold_not_reached():
    trigger = BriefingTrigger(cancellation_threshold=3)
    store = _store()
    store.latest_snapshots = {"T1": _snapshot("T1", "CANCELED"), "T2": _snapshot("T2", "CANCELED")}

    assert trigger.evaluate(store, NOW) is None


def test_cancellation_threshold_reached_within_window():
    trigger = BriefingTrigger(cancellation_threshold=3, cancellation_window_s=900)
    store = _store()

    store.latest_snapshots = {"T1": _snapshot("T1", "CANCELED")}
    assert trigger.evaluate(store, NOW) is None

    store.latest_snapshots = {
        "T1": _snapshot("T1", "CANCELED"),
        "T2": _snapshot("T2", "CANCELED"),
        "T3": _snapshot("T3", "CANCELED"),
    }
    reason = trigger.evaluate(store, NOW + timedelta(minutes=2))

    assert reason is not None
    assert reason.kind == "cancellation_threshold"


def test_cancellations_outside_the_window_do_not_count():
    trigger = BriefingTrigger(cancellation_threshold=3, cancellation_window_s=60)
    store = _store()

    store.latest_snapshots = {"T1": _snapshot("T1", "CANCELED")}
    trigger.evaluate(store, NOW)

    # T2 cancelled 5 minutes later -- outside a 60s window, T1's
    # cancellation must have already aged out.
    store.latest_snapshots["T2"] = _snapshot("T2", "CANCELED")
    reason = trigger.evaluate(store, NOW + timedelta(minutes=5))

    assert reason is None


def test_cooldown_blocks_a_trigger_right_after_a_briefing_was_sent():
    trigger = BriefingTrigger(cooldown_s=1800)
    store = _store()
    trigger.record_briefed(NOW)

    store.latest_alerts = {"A1": _alert()}
    reason = trigger.evaluate(store, NOW + timedelta(minutes=5))

    assert reason is None


def test_trigger_fires_again_once_cooldown_has_elapsed():
    trigger = BriefingTrigger(cooldown_s=1800)
    store = _store()
    trigger.record_briefed(NOW)

    # A genuinely new alert, evaluated after the cooldown window passes.
    store.latest_alerts = {"A1": _alert()}
    reason = trigger.evaluate(store, NOW + timedelta(minutes=31))

    assert reason is not None
    assert reason.kind == "new_alert"


def test_both_checks_update_state_even_when_one_already_found_a_reason():
    """A regression guard for the short-circuit trap: `evaluate()` must
    call both `_check_alerts` and `_check_cancellations` every time, not
    stop at the first non-None reason -- otherwise whichever check runs
    second would silently stop tracking state on any cycle where the
    first one already triggered."""
    trigger = BriefingTrigger(cancellation_threshold=2, cancellation_window_s=900)
    store = _store()

    # Alerts fire a reason this cycle; cancellations must still be
    # bookkept in the background.
    store.latest_alerts = {"A1": _alert()}
    store.latest_snapshots = {"T1": _snapshot("T1", "CANCELED")}
    reason = trigger.evaluate(store, NOW)
    assert reason.kind == "new_alert"

    # Next cycle: no new alert, but the earlier cancellation plus one more
    # should now cross the threshold -- only possible if T1 was recorded
    # into _recent_cancellations on the previous call.
    store.latest_alerts = {}
    store.latest_snapshots["T2"] = _snapshot("T2", "CANCELED")
    reason = trigger.evaluate(store, NOW + timedelta(minutes=1))

    assert reason is not None
    assert reason.kind == "cancellation_threshold"
