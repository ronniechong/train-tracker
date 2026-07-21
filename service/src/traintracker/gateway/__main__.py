"""One-shot auth smoke check: `python -m traintracker.gateway`.

Not the poller loop (that's 2b) — this exists only so 2a's acceptance
criterion ("docker compose up authenticates against the real live API at
least once") has something runnable to check against.
"""

from __future__ import annotations

import logging
import os

from ..redaction import configure_logging
from .client import API_KEY_ENV, Feed, GatewayAuthError, GatewayClient, GatewayError

logger = logging.getLogger("traintracker.gateway.smoke")


def main() -> int:
    configure_logging(os.environ.get(API_KEY_ENV, ""), level=logging.INFO)
    try:
        with GatewayClient() as client:
            result = client.fetch(Feed.VEHICLE_POSITIONS)
    except (GatewayAuthError, GatewayError) as exc:
        logger.error("smoke check failed: %s", exc)
        return 1

    logger.info(
        "smoke check OK: feed=%s bytes=%d throttle=%s",
        result.feed.value,
        len(result.payload),
        result.throttle,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
