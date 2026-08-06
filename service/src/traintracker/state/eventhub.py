"""In-process asyncio event hub.

Kept behind a small `Protocol` so a future multi-process setup (Redis
pub/sub) can swap in without touching producers -- revisit if multi-process
consumers appear.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

logger = logging.getLogger("traintracker.eventhub")


class EventHub(Protocol):
    def publish(self, event: object) -> None: ...
    def subscribe(self, maxsize: int = 0) -> asyncio.Queue: ...
    def unsubscribe(self, queue: asyncio.Queue) -> None: ...


class InProcessEventHub:
    """Subscribers can request a bounded queue (`maxsize`, default
    0 = unbounded). `publish` is a pure "wake up and recompute" signal to
    SSE consumers -- the event's actual
    value is never inspected by anything downstream, so a full queue means
    a slow/stalled consumer has a stale-but-harmless backlog, not lost
    data: dropping one tick for that consumer (and only that one) is
    correct, not lossy, and must never break delivery to every OTHER
    subscriber (a `QueueFull` on one subscriber must not raise inside the
    loop and abort ticking everyone else)."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self, maxsize: int = 0) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: object) -> None:
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("SSE subscriber queue full, dropping this tick for it")
