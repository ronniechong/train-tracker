"""Loads the serialized delay-prediction model (`scripts/train_delay_model.
py`'s output) and runs inference -- math only, no LLM call, so the
public-facing "Am I late?" endpoint (`api/app.py`) has no per-click cost or
new paid-API surface. `FEATURE_NAMES` here is the single source of truth
for feature order; the training script imports it too, so the coefficients
it fits and serializes always line up with how this module reads them back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .delay_observation import DelayFeatures

FEATURE_NAMES = ("current_delay_s", "stops_remaining", "active_alert_flag")


Coefficients = tuple[float, tuple[float, float, float]]  # (intercept, weights)


@dataclass(frozen=True)
class DelayModel:
    intercept: float
    weights: tuple[float, float, float]  # aligned with FEATURE_NAMES
    trained_at: str
    # route_id -> its own coefficients; routes without an entry use the
    # network-wide ones above. Absent from older model files.
    per_route: Mapping[str, Coefficients] = field(default_factory=dict)


def load_delay_model(path: Path) -> DelayModel | None:
    """`None` if no model has been trained yet (file doesn't exist) --
    same "feature not configured" convention `api/app.py` already uses
    for schedule_cache/digest_store/etc, not an error."""
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return DelayModel(
        intercept=data["intercept"],
        weights=tuple(data["weights"]),
        trained_at=data["trained_at"],
        per_route={
            route_id: (c["intercept"], tuple(c["weights"]))
            for route_id, c in data.get("per_route", {}).items()
        },
    )


def predict_delay_seconds(
    model: DelayModel, features: DelayFeatures, route_id: str | None = None
) -> float:
    """Unrounded -- callers that display this to a user (`api/app.py`)
    round it themselves; callers that fit/evaluate a model (`scripts/
    train_delay_model.py`) need the unrounded value for residual/MAE
    precision."""
    x = (
        float(features.current_delay_s),
        float(features.stops_remaining),
        1.0 if features.active_alert_flag else 0.0,
    )
    intercept, weights = model.per_route.get(route_id, (model.intercept, model.weights))
    return intercept + sum(w * xi for w, xi in zip(weights, x))
