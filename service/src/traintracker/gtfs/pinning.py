"""Per-service-day snapshot pinning.

Pin exactly one static snapshot to each service_date, once, and never
silently repin it — this is what makes the nightly refresh job safe against
publish-timing races (the portal republishing before/after/twice around the
nightly run). The manifest here is a simple JSON sidecar; milestone 2e may
persist this differently (e.g. in the SQLite history store) but the pinning
*logic* — idempotent, first-write-wins per service_date — stays the same.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from pathlib import Path

from .snapshot import StaticSnapshot


@dataclass(frozen=True)
class Pin:
    service_date: str  # ISO date, JSON-friendly
    digest: str
    pinned_at: str  # ISO datetime, JSON-friendly


@dataclass(frozen=True)
class PinResult:
    pin: Pin
    was_new: bool  # False means an existing pin was left untouched (idempotent no-op)


class PinManifest:
    """JSON-backed record of which snapshot digest is pinned to which
    service_date. Loads/saves the whole file on each call — fine at this
    scale (one row per service day, retained 60 days per CLAUDE.md)."""

    def __init__(self, manifest_path: Path):
        self._path = manifest_path

    def _load(self) -> dict[str, Pin]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text())
        return {k: Pin(**v) for k, v in raw.items()}

    def _save(self, pins: dict[str, Pin]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({k: asdict(v) for k, v in pins.items()}, indent=2, sort_keys=True)
        )

    def get(self, service_date: date) -> Pin | None:
        return self._load().get(service_date.isoformat())

    def pin(self, service_date: date, snapshot: StaticSnapshot) -> PinResult:
        pins = self._load()
        key = service_date.isoformat()
        existing = pins.get(key)
        if existing is not None:
            return PinResult(pin=existing, was_new=False)

        new_pin = Pin(
            service_date=key,
            digest=snapshot.digest,
            pinned_at=datetime.now(timezone.utc).isoformat(),
        )
        pins[key] = new_pin
        self._save(pins)
        return PinResult(pin=new_pin, was_new=True)


@dataclass(frozen=True)
class ChurnResult:
    total_old: int
    total_new: int
    stable: int
    churned_from_old: int
    stable_pct: float

    @property
    def churn_pct(self) -> float:
        return 100.0 - self.stable_pct


def compare_trip_ids(
    old_ids: frozenset[str], new_ids: frozenset[str]
) -> ChurnResult:
    """Compare two snapshots' trip_id sets. Mirrors the M1 finding: expect
    high churn on future-dated trips (portal regenerates ids each publish)
    but near-100% stability for trip_ids scoped to an elapsed/current
    service_date — callers should pass `trip_ids_for_service_date(...)`
    results, not the raw whole-snapshot `trip_ids`, to get that comparison.
    """
    stable = old_ids & new_ids
    total_old = len(old_ids)
    stable_pct = (len(stable) / total_old * 100.0) if total_old else 100.0
    return ChurnResult(
        total_old=total_old,
        total_new=len(new_ids),
        stable=len(stable),
        churned_from_old=total_old - len(stable),
        stable_pct=stable_pct,
    )
