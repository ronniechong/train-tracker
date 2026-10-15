"""External dead-man's-switch ping — outside confirmation that the poller
is alive at all, for when the on-box stack can't report its own death.

Rung once per FULLY successful cycle (all three feeds fetched OK), not per
feed and not on a partial cycle — a ping that fires even during a
circuit-breaker backoff would defeat the point of having an external
dead-man's switch at all.

The check's own "expected interval" is configured on the monitoring
service's own dashboard, not in code — it must be set wider than the
breaker's 5-minute cap, so a single backoff episode alone can never trip
an alert.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

PING_URL_ENV = "TT_DEADMAN_PING_URL"

# Single failed pings are routine (short uplink blips) and the next
# cycle's ping recovers them long before the monitoring service's grace
# period expires -- only a run of failures is worth a warning.
WARN_AFTER_CONSECUTIVE_FAILURES = 3
REWARN_EVERY = 20

_consecutive_failures: dict[str, int] = {}


async def ping(client: httpx.AsyncClient, url: str | None = None) -> bool:
    """Best-effort: a failed or unconfigured dead-man ping must never crash
    the poller, it's a monitoring signal, not a dependency."""
    resolved_url = url or os.environ.get(PING_URL_ENV)
    if not resolved_url:
        logger.debug("%s not set, skipping dead-man ping", PING_URL_ENV)
        return False
    try:
        response = await client.get(resolved_url, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        failures = _consecutive_failures.get(resolved_url, 0) + 1
        _consecutive_failures[resolved_url] = failures
        # Timeouts stringify to "", so always include the exception type.
        detail = f"{type(exc).__name__}: {exc}"
        if failures == WARN_AFTER_CONSECUTIVE_FAILURES or (
            failures > WARN_AFTER_CONSECUTIVE_FAILURES and failures % REWARN_EVERY == 0
        ):
            logger.warning("dead-man ping failing: %d consecutive failures (%s)", failures, detail)
        else:
            logger.debug("dead-man ping failed (%d consecutive): %s", failures, detail)
        return False
    failures = _consecutive_failures.pop(resolved_url, 0)
    if failures >= WARN_AFTER_CONSECUTIVE_FAILURES:
        logger.info("dead-man ping recovered after %d consecutive failures", failures)
    return True
