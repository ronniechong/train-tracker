"""External dead-man's-switch ping — outside confirmation that the poller
is alive at all, for when the on-box stack can't report its own death
(CLAUDE.md's settled monitoring decision).

Rung once per FULLY successful cycle (all three feeds fetched OK), not per
feed and not on a partial cycle — a ping that fires even during a
circuit-breaker backoff would defeat the point of having an external
dead-man's switch at all.

The check's own "expected interval" is configured on the monitoring
service's own dashboard, not in code — it must be set wider than the
breaker's 5-minute cap (see `ops/README.md`), so a single backoff episode
alone can never trip an alert.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

PING_URL_ENV = "TT_DEADMAN_PING_URL"


def ping(client: httpx.Client, url: str | None = None) -> bool:
    """Best-effort: a failed or unconfigured dead-man ping must never crash
    the poller, it's a monitoring signal, not a dependency."""
    resolved_url = url or os.environ.get(PING_URL_ENV)
    if not resolved_url:
        logger.debug("%s not set, skipping dead-man ping", PING_URL_ENV)
        return False
    try:
        response = client.get(resolved_url, timeout=10.0)
        response.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.warning("dead-man ping failed: %s", exc)
        return False
