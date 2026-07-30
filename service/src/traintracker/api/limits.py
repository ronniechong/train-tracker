"""SSE connection caps (M3 finding #4 + #9): sized to protect the whole
shared deployment host -- this container isn't the only thing running
there -- not just this project's own availability. First-cut values, not
tuned against real traffic; revisit once this is actually deployed and
used.
"""

from __future__ import annotations

MAX_GLOBAL_SSE_CONNECTIONS = 50
MAX_PER_IP_SSE_CONNECTIONS = 5


class ConnectionLimitExceeded(Exception):
    pass


class ConnectionTracker:
    def __init__(
        self,
        max_global: int = MAX_GLOBAL_SSE_CONNECTIONS,
        max_per_ip: int = MAX_PER_IP_SSE_CONNECTIONS,
    ):
        self._max_global = max_global
        self._max_per_ip = max_per_ip
        self._global_count = 0
        self._per_ip: dict[str, int] = {}

    def acquire(self, client_ip: str) -> None:
        if self._global_count >= self._max_global:
            raise ConnectionLimitExceeded("global SSE connection cap reached")
        if self._per_ip.get(client_ip, 0) >= self._max_per_ip:
            raise ConnectionLimitExceeded("per-IP SSE connection cap reached")
        self._global_count += 1
        self._per_ip[client_ip] = self._per_ip.get(client_ip, 0) + 1

    def release(self, client_ip: str) -> None:
        self._global_count = max(0, self._global_count - 1)
        remaining = self._per_ip.get(client_ip, 0) - 1
        if remaining <= 0:
            self._per_ip.pop(client_ip, None)
        else:
            self._per_ip[client_ip] = remaining
