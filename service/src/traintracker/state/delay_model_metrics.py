"""Prometheus textfile-collector output for the delay/ETA retrain job.

Same rationale as `archive/metrics.py`: this is a one-shot batch job
(`docker compose run --rm`), never up long enough for Prometheus to scrape
directly, so it writes the node_exporter textfile collector format instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_HELP = {
    "delay_model_last_run_timestamp_seconds": "Unix timestamp of the last completed retrain attempt (runs even if the eval gate failed).",
    "delay_model_eval_gate_result": "1 if the last attempt passed the eval gate and wrote a new model, 0 if it did not.",
    "delay_model_mae_seconds": "Model MAE on the held-out test set from the last attempt.",
    "delay_model_baseline_mae_seconds": "Naive baseline MAE (final delay = current delay) from the last attempt.",
    "delay_model_train_examples": "Labelled training examples used in the last attempt.",
    "delay_model_test_examples": "Labelled test examples used in the last attempt.",
}


@dataclass(frozen=True)
class DelayModelRunResult:
    passed: bool
    model_mae: float
    baseline_mae: float
    train_examples: int
    test_examples: int


def render_textfile(result: DelayModelRunResult, now: datetime) -> str:
    lines: list[str] = []
    for metric, help_text in _HELP.items():
        lines.append(f"# HELP {metric} {help_text}")
        lines.append(f"# TYPE {metric} gauge")

    lines.append(f"delay_model_last_run_timestamp_seconds {now.timestamp()}")
    lines.append(f"delay_model_eval_gate_result {1 if result.passed else 0}")
    lines.append(f"delay_model_mae_seconds {result.model_mae}")
    lines.append(f"delay_model_baseline_mae_seconds {result.baseline_mae}")
    lines.append(f"delay_model_train_examples {result.train_examples}")
    lines.append(f"delay_model_test_examples {result.test_examples}")
    return "\n".join(lines) + "\n"


def write_textfile_metrics(path: Path, result: DelayModelRunResult, now: datetime) -> None:
    """Atomic write: node_exporter's textfile collector can otherwise
    scrape a partially-written file mid-update."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(render_textfile(result, now))
    os.replace(tmp_path, path)
