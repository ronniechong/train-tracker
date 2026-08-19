"""One-off training script for M5's delay/ETA prediction feature -- NOT
part of the automated pytest suite (reads real production history data,
writes a real model file). Fits a plain linear regression, zero new
dependencies (no numpy/scikit-learn -- `service/pyproject.toml` has none
today; the milestone's own design note says ship the simplest thing that
could work, only escalate if this model's real eval error is unacceptably
high against the naive baseline below).

Target: a trip's final delay at its terminus (`TripCompletionEvent.
delay_seconds`), predicted from a mid-journey `DelayObservationEvent`
(current delay, stops remaining, whether an active alert covers the
trip). Joined by (trip_id, service_date) -- `cancelled`/
`undetermined_gap` completions are excluded (can't label a trip that
never really completed, same reliability/punctuality exclusion applied
elsewhere in this milestone).

Split by service_date, NOT a random row split -- a random split would
leak multiple observations of the SAME trip across train and test,
inflating apparent accuracy. The last `--test-days` calendar days
(chronologically) become the test set.

Eval gate: the model's MAE on the test set must beat the naive baseline
("final delay = current delay, unchanged") or no model file is written --
this feature is not trusted until it demonstrably beats doing nothing.

Run: `uv run python scripts/train_delay_model.py --history-dir <dir>
--days 19` from `service/`. `--history-dir` is the day-partitioned
SQLite directory the poller's `HistoryStore` writes to in production,
or a local `tmp_path`-style copy for a dry run.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from traintracker.history.store import HistoryStore  # noqa: E402
from traintracker.state.completion import TripCompletionEvent  # noqa: E402
from traintracker.state.delay_model import (  # noqa: E402
    FEATURE_NAMES,
    DelayModel,
    predict_delay_seconds,
)
from traintracker.state.delay_observation import DelayFeatures, DelayObservationEvent
from traintracker.state.delay_model_metrics import (  # noqa: E402
    DelayModelRunResult,
    write_textfile_metrics,
)  # noqa: E402

EXCLUDED_STATUSES = {"cancelled", "undetermined_gap"}
# Tiny ridge term on the normal-equations diagonal -- guards against a
# singular/near-singular matrix (e.g. a training window with almost no
# variation in one feature) without meaningfully biasing a well-
# conditioned fit.
RIDGE_EPSILON = 1e-6


@dataclass(frozen=True)
class LabelledObservation:
    observation: DelayObservationEvent
    final_delay_s: int


def join_observations_with_labels(
    observations: list[DelayObservationEvent],
    completions: list[TripCompletionEvent],
) -> list[LabelledObservation]:
    labels: dict[tuple[str, str], int] = {}
    for completion in completions:
        if completion.status in EXCLUDED_STATUSES or completion.delay_seconds is None:
            continue
        labels[(completion.trip_id, completion.service_date)] = completion.delay_seconds

    joined = []
    for observation in observations:
        key = (observation.trip_id, observation.service_date)
        final_delay_s = labels.get(key)
        if final_delay_s is None:
            continue
        joined.append(LabelledObservation(observation=observation, final_delay_s=final_delay_s))
    return joined


def split_by_service_date(
    examples: list[LabelledObservation], test_days: int
) -> tuple[list[LabelledObservation], list[LabelledObservation]]:
    distinct_dates = sorted({e.observation.service_date for e in examples})
    test_dates = set(distinct_dates[-test_days:]) if test_days > 0 else set()
    train = [e for e in examples if e.observation.service_date not in test_dates]
    test = [e for e in examples if e.observation.service_date in test_dates]
    return train, test


def _as_features(observation: DelayObservationEvent) -> DelayFeatures:
    return DelayFeatures(
        current_delay_s=observation.current_delay_s,
        stops_remaining=observation.stops_remaining,
        active_alert_flag=observation.active_alert_flag,
    )


def _features(observation: DelayObservationEvent) -> tuple[float, float, float]:
    return (
        float(observation.current_delay_s),
        float(observation.stops_remaining),
        1.0 if observation.active_alert_flag else 0.0,
    )


def fit_linear_regression(
    examples: list[LabelledObservation], weights: list[float] | None = None
) -> list[float]:
    """Weighted least squares via the normal equations (X^T W X) beta =
    X^T W y, solved by plain Gaussian elimination -- no numpy. `beta[0]`
    is the intercept (a constant `1.0` feature is prepended), `beta[1:]`
    line up with `FEATURE_NAMES`. `weights` defaults to uniform (plain
    OLS) -- `fit_least_absolute_deviation` below re-calls this with
    IRLS weights instead."""
    rows = [(1.0, *_features(e.observation)) for e in examples]
    targets = [float(e.final_delay_s) for e in examples]
    row_weights = weights if weights is not None else [1.0] * len(examples)
    n_features = len(rows[0])

    xtx = [[0.0] * n_features for _ in range(n_features)]
    xty = [0.0] * n_features
    for row, target, weight in zip(rows, targets, row_weights):
        for i in range(n_features):
            xty[i] += weight * row[i] * target
            for j in range(n_features):
                xtx[i][j] += weight * row[i] * row[j]
    for i in range(n_features):
        xtx[i][i] += RIDGE_EPSILON

    return _solve_linear_system(xtx, xty)


def fit_least_absolute_deviation(
    examples: list[LabelledObservation], iterations: int = 15, min_residual: float = 1.0
) -> list[float]:
    """Iteratively reweighted least squares (IRLS), converging toward a
    least-absolute-deviation (median-seeking) fit instead of ordinary
    least squares' mean-seeking one -- matters when the delay
    distribution is heavily right-skewed (a long tail of very late
    trips), which drags a plain OLS fit toward a near-constant, high
    prediction that MAE punishes badly. Each round re-weights every row
    by `1/|residual|` (from the previous round's fit) and re-solves the
    weighted normal equations -- rows the current fit already predicts
    well matter less next round, rows it's badly wrong on matter more,
    converging toward minimizing absolute error, the same metric
    `evaluate()` reports. `min_residual` floors the weight denominator
    so a near-perfectly-fit row's weight doesn't blow up."""
    weights = [1.0] * len(examples)
    coefficients = fit_linear_regression(examples, weights)
    for _ in range(iterations):
        weights = [
            1.0 / max(abs(e.final_delay_s - predict(coefficients, e.observation)), min_residual)
            for e in examples
        ]
        coefficients = fit_linear_regression(examples, weights)
    return coefficients


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    augmented = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(augmented[r][col]))
        augmented[col], augmented[pivot_row] = augmented[pivot_row], augmented[col]
        pivot = augmented[col][col]
        for j in range(col, n + 1):
            augmented[col][j] /= pivot
        for r in range(n):
            if r == col:
                continue
            factor = augmented[r][col]
            for j in range(col, n + 1):
                augmented[r][j] -= factor * augmented[col][j]
    return [augmented[i][n] for i in range(n)]


def predict(coefficients: list[float], observation: DelayObservationEvent) -> float:
    """Delegates to the same `predict_delay_seconds` the serving endpoint
    (`api/app.py`) calls -- guarantees the eval score reported here is
    exactly what a real prediction request would return, not a
    parallel implementation that could silently drift from it."""
    intercept, *weights = coefficients
    model = DelayModel(intercept=intercept, weights=tuple(weights), trained_at="")
    return predict_delay_seconds(model, _as_features(observation))


def mean_absolute_error(actuals: list[float], predictions: list[float]) -> float:
    return sum(abs(a - p) for a, p in zip(actuals, predictions)) / len(actuals)


def baseline_prediction(observation: DelayObservationEvent) -> float:
    """Naive baseline: assume the current delay holds unchanged to the
    terminus -- the model must beat this to be trusted at all."""
    return float(observation.current_delay_s)


def evaluate(
    coefficients: list[float], test: list[LabelledObservation]
) -> tuple[float, float]:
    actuals = [float(e.final_delay_s) for e in test]
    model_predictions = [predict(coefficients, e.observation) for e in test]
    baseline_predictions = [baseline_prediction(e.observation) for e in test]
    return mean_absolute_error(actuals, model_predictions), mean_absolute_error(
        actuals, baseline_predictions
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-dir", required=True, type=Path)
    parser.add_argument("--days", type=int, default=19, help="Trailing days to read, ending yesterday")
    parser.add_argument("--test-days", type=int, default=4, help="Most recent N days held out for eval")
    parser.add_argument("--model-out", type=Path, default=Path("/data/ai/delay_model.json"))
    parser.add_argument("--metrics-out", type=Path, default=Path("/archive-state/metrics/delay_model.prom"))
    args = parser.parse_args()

    # Excludes today: today's partition is still open for writes by the
    # live poller, so a mid-day read would see a partial, misleading day.
    yesterday = date.today() - timedelta(days=1)
    service_dates = [yesterday - timedelta(days=i) for i in range(args.days)]

    store = HistoryStore(args.history_dir)
    observations_window = store.read_delay_observations(service_dates)
    completions_window = store.read_completion_events(service_dates)

    print(
        f"Observation days covered: {len(observations_window.days_covered)}/{args.days} "
        f"(missing: {[d.isoformat() for d in observations_window.days_missing]})"
    )
    print(f"Raw observations: {len(observations_window.events)}")
    print(f"Raw completions: {len(completions_window.events)}")

    examples = join_observations_with_labels(
        list(observations_window.events), list(completions_window.events)
    )
    print(f"Labelled examples (joined, excluded cancelled/undetermined_gap): {len(examples)}")

    train, test = split_by_service_date(examples, args.test_days)
    print(f"Train: {len(train)} examples, Test: {len(test)} examples")
    if not train or not test:
        print("[FAIL] not enough data to train/evaluate -- no model written")
        return 1

    coefficients = fit_least_absolute_deviation(train)
    model_mae, baseline_mae = evaluate(coefficients, test)
    passed = model_mae < baseline_mae
    print(
        f"[{'PASS' if passed else 'FAIL'}] model MAE={model_mae:.1f}s "
        f"baseline MAE={baseline_mae:.1f}s"
    )
    write_textfile_metrics(
        args.metrics_out,
        DelayModelRunResult(
            passed=passed,
            model_mae=model_mae,
            baseline_mae=baseline_mae,
            train_examples=len(train),
            test_examples=len(test),
        ),
        datetime.now(timezone.utc),
    )
    if not passed:
        print("Model does not beat the naive baseline -- no model file written")
        return 1

    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    args.model_out.write_text(
        json.dumps(
            {
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "feature_names": list(FEATURE_NAMES),
                "intercept": coefficients[0],
                "weights": coefficients[1:],
                "train_examples": len(train),
                "test_examples": len(test),
                "test_mae_seconds": model_mae,
                "baseline_mae_seconds": baseline_mae,
            },
            indent=2,
        )
    )
    print(f"Wrote {args.model_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
