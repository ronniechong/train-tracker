import json

from traintracker.state.delay_model import DelayModel, load_delay_model, predict_delay_seconds
from traintracker.state.delay_observation import DelayFeatures


def test_load_delay_model_returns_none_when_no_file_exists(tmp_path):
    assert load_delay_model(tmp_path / "delay_model.json") is None


def test_load_delay_model_round_trips_a_serialized_model(tmp_path):
    path = tmp_path / "delay_model.json"
    path.write_text(json.dumps({
        "trained_at": "2026-08-18T00:00:00+00:00",
        "feature_names": ["current_delay_s", "stops_remaining", "active_alert_flag"],
        "intercept": 10.0,
        "weights": [0.5, 1.0, -5.0],
        "train_examples": 100,
        "test_examples": 20,
        "test_mae_seconds": 88.0,
        "baseline_mae_seconds": 92.0,
    }))

    model = load_delay_model(path)

    assert model == DelayModel(intercept=10.0, weights=(0.5, 1.0, -5.0), trained_at="2026-08-18T00:00:00+00:00")


def test_predict_delay_seconds_applies_intercept_and_weights():
    model = DelayModel(intercept=10.0, weights=(0.5, 1.0, -5.0), trained_at="")
    features = DelayFeatures(current_delay_s=60, stops_remaining=4, active_alert_flag=False)

    predicted = predict_delay_seconds(model, features)

    assert predicted == 10.0 + 0.5 * 60 + 1.0 * 4 + (-5.0) * 0.0


def test_predict_delay_seconds_applies_the_active_alert_weight_when_flagged():
    model = DelayModel(intercept=10.0, weights=(0.5, 1.0, -5.0), trained_at="")
    features = DelayFeatures(current_delay_s=60, stops_remaining=4, active_alert_flag=True)

    predicted = predict_delay_seconds(model, features)

    assert predicted == 10.0 + 0.5 * 60 + 1.0 * 4 + (-5.0) * 1.0
