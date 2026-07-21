"""In-process asyncio event hub (CLAUDE.md's settled eventing decision) -
producer side only. The state store (merge + station-state + ghost
tracker) is the natural producer of live update events, but nothing
consumes this yet; M3 owns the SSE consumer and per-connection caps.

Kept behind a small `Protocol` so a future multi-process setup (Redis
pub/sub) can swap in without touching producers - see CLAUDE.md's eventing
decision: "Revisit if: Multi-process consumers appear."
"""

from __future__ import annotations

import asyncio
from typing import Protocol


class EventHub(Protocol):
    def publish(self, event: object) -> None: ...
    def subscribe(self) -> asyncio.Queue: ...
    def unsubscribe(self, queue: asyncio.Queue) -> None: ...


class InProcessEventHub:
    """Unbounded per-subscriber queues and no subscriber limit - fine with
    zero real consumers today. M3 owns bounding these and capping
    subscriber count (security invariant #3, SSE connection caps) once a
    real consumer exists; this producer-side interface shouldn't need to
    change when that lands."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: object) -> None:
        for queue in self._subscribers:
            queue.put_nowait(event)
