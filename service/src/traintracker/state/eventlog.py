"""Append-only event log interface, shared by `DiscrepancyEvent` (merge.py)
and `GhostEvent` (ghost.py).

This module defines the schema and a working in-memory collector, not
persistence -- the history writer owns a SQLite-backed implementation of
this same `Protocol`. Nothing upstream of an `EventLog` should need to
change when that lands.
"""

from __future__ import annotations

from typing import Protocol


class EventLog(Protocol):
    def record(self, event: object) -> None: ...


class InMemoryEventLog:
    """Sufficient for tests and the replay harness. Not for production use
    across restarts - it holds everything in a plain list with no bound."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def record(self, event: object) -> None:
        self.events.append(event)
