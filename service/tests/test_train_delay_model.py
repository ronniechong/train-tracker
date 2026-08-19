import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

from traintracker.state.completion import TripCompletionEvent
from traintracker.state.delay_observation import DelayObservationEvent

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "train_delay_model.py"
_spec = importlib.util.spec_from_file_location("train_delay_model", _SCRIPT_PATH)
train_delay_model = importlib.util.module_from_spec(_spec)
sys.modules["train_delay_model"] = train_delay_model
_spec.loader.exec_module(train_delay_model)


def _observation(
    trip_id: str, service_date: str, current_delay_s: int, stops_remaining: int, active_alert_flag: bool = False
) -> DelayObservationEvent:
    return DelayObservationEvent(
        trip_id=trip_id,
        route_id="R1",
        service_date=service_date,
        observed_at=datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc),
        current_delay_s=current_delay_s,
        stops_remaining=stops_remaining,
        active_alert_flag=active_alert_flag,
    )


def _completion(
    trip_id: str, service_date: str, delay_seconds: int | None, status: str = "on_time"
) -> TripCompletionEvent:
    return TripCompletionEvent(
        trip_id=trip_id,
        route_id="R1",
        service_date=service_date,
        scheduled_terminus_arrival=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        actual_terminus_arrival=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        delay_seconds=delay_seconds,
        status=status,
    )


def test_join_matches_observation_to_its_trips_eventual_completion():
    observations = [_observation("T1", "2026-08-01", current_delay_s=60, stops_remaining=5)]
    completions = [_completion("T1", "2026-08-01", delay_seconds=120)]

    joined = train_delay_model.join_observations_with_labels(observations, completions)

    assert len(joined) == 1
    assert joined[0].final_delay_s == 120


def test_join_excludes_cancelled_and_undetermined_gap_completions():
    observations = [
        _observation("T1", "2026-08-01", current_delay_s=60, stops_remaining=5),
        _observation("T2", "2026-08-01", current_delay_s=60, stops_remaining=5),
    ]
    completions = [
        _completion("T1", "2026-08-01", delay_seconds=None, status="cancelled"),
        _completion("T2", "2026-08-01", delay_seconds=None, status="undetermined_gap"),
    ]

    joined = train_delay_model.join_observations_with_labels(observations, completions)

    assert joined == []


def test_join_drops_observations_with_no_matching_completion():
    observations = [_observation("T1", "2026-08-01", current_delay_s=60, stops_remaining=5)]

    joined = train_delay_model.join_observations_with_labels(observations, completions=[])

    assert joined == []


def test_split_by_service_date_holds_out_the_most_recent_days():
    examples = [
        train_delay_model.LabelledObservation(
            observation=_observation("T1", day, current_delay_s=0, stops_remaining=1), final_delay_s=0
        )
        for day in ["2026-08-01", "2026-08-02", "2026-08-03"]
    ]

    train, test = train_delay_model.split_by_service_date(examples, test_days=1)

    assert [e.observation.service_date for e in train] == ["2026-08-01", "2026-08-02"]
    assert [e.observation.service_date for e in test] == ["2026-08-03"]


def test_split_by_service_date_keeps_a_single_trips_observations_on_one_side():
    # The whole point of splitting by date rather than by row: multiple
    # observations of the SAME trip on the SAME service_date must never
    # straddle train/test.
    examples = [
        train_delay_model.LabelledObservation(
            observation=_observation("T1", "2026-08-01", current_delay_s=d, stops_remaining=5),
            final_delay_s=120,
        )
        for d in (0, 30, 60)
    ]

    train, test = train_delay_model.split_by_service_date(examples, test_days=1)

    assert len(train) == 0
    assert len(test) == 3


def test_fit_linear_regression_recovers_an_exact_linear_relationship():
    # final_delay = 2 * current_delay_s, noise-free -- a well-specified
    # linear model should recover this near-exactly.
    examples = [
        train_delay_model.LabelledObservation(
            observation=_observation("T1", "2026-08-01", current_delay_s=d, stops_remaining=3),
            final_delay_s=2 * d,
        )
        for d in (0, 30, 60, 90, 120)
    ]

    coefficients = train_delay_model.fit_linear_regression(examples)
    predicted = train_delay_model.predict(coefficients, _observation("T1", "2026-08-01", current_delay_s=50, stops_remaining=3))

    assert abs(predicted - 100) < 1.0


def test_fit_least_absolute_deviation_recovers_an_exact_linear_relationship():
    examples = [
        train_delay_model.LabelledObservation(
            observation=_observation("T1", "2026-08-01", current_delay_s=d, stops_remaining=3),
            final_delay_s=2 * d,
        )
        for d in (0, 30, 60, 90, 120)
    ]

    coefficients = train_delay_model.fit_least_absolute_deviation(examples)
    predicted = train_delay_model.predict(
        coefficients, _observation("T1", "2026-08-01", current_delay_s=50, stops_remaining=3)
    )

    assert abs(predicted - 100) < 1.0


def test_fit_least_absolute_deviation_beats_ols_on_a_right_skewed_outlier():
    # A handful of very late trips (a heavy right tail, like the real
    # production delay distribution) should drag a plain OLS fit toward
    # them since it minimizes SQUARED error -- IRLS, minimizing absolute
    # error, should stay closer to the typical (near-zero-delay) case.
    typical = [
        train_delay_model.LabelledObservation(
            observation=_observation("T1", "2026-08-01", current_delay_s=d, stops_remaining=5),
            final_delay_s=d,
        )
        for d in range(0, 20)
    ]
    outliers = [
        train_delay_model.LabelledObservation(
            observation=_observation("T1", "2026-08-01", current_delay_s=0, stops_remaining=5),
            final_delay_s=5000,
        )
        for _ in range(2)
    ]
    examples = typical + outliers

    ols_coefficients = train_delay_model.fit_linear_regression(examples)
    irls_coefficients = train_delay_model.fit_least_absolute_deviation(examples)

    typical_point = _observation("T1", "2026-08-01", current_delay_s=10, stops_remaining=5)
    ols_prediction = train_delay_model.predict(ols_coefficients, typical_point)
    irls_prediction = train_delay_model.predict(irls_coefficients, typical_point)

    # The true value for a typical (non-outlier) point is ~10.
    assert abs(irls_prediction - 10) < abs(ols_prediction - 10)


def test_mean_absolute_error_is_the_average_absolute_difference():
    assert train_delay_model.mean_absolute_error([10.0, 20.0], [12.0, 15.0]) == 3.5


def test_baseline_prediction_assumes_delay_holds_unchanged():
    observation = _observation("T1", "2026-08-01", current_delay_s=90, stops_remaining=4)

    assert train_delay_model.baseline_prediction(observation) == 90.0


def test_evaluate_reports_model_and_baseline_mae_separately():
    # Model trained to fit exactly; baseline (current delay unchanged)
    # should be worse here since the true relationship is 2x, not 1x.
    train = [
        train_delay_model.LabelledObservation(
            observation=_observation("T1", "2026-08-01", current_delay_s=d, stops_remaining=3),
            final_delay_s=2 * d,
        )
        for d in (0, 30, 60, 90, 120)
    ]
    coefficients = train_delay_model.fit_least_absolute_deviation(train)
    test = [
        train_delay_model.LabelledObservation(
            observation=_observation("T1", "2026-08-02", current_delay_s=50, stops_remaining=3),
            final_delay_s=100,
        )
    ]

    model_mae, baseline_mae = train_delay_model.evaluate(coefficients, test)

    assert model_mae < baseline_mae
