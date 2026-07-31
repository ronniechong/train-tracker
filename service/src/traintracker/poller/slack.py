"""Slack incoming-webhook delivery for 05e's disruption briefings -- the
first app-code Slack integration in this repo (2f's ops alerts are
Grafana's own Slack contact point, configured in Grafana, not here).

Reuses `TT_ALERT_WEBHOOK_URL` (2f's ops-alerts webhook) rather than a
dedicated one -- 05e's kickoff considered a separate webhook/channel
(different audience: rider-facing disruption info vs. infra health) but
Ronnie decided against the extra Slack setup for two config values that
would only ever need to stay in sync; briefings and ops alerts share one
channel by choice (revisited 2026-07-31, reversing that kickoff's
tentative call). Splitting them again later is a one-line env change,
not a code change.

Mirrors `healthcheck.ping()`'s exact shape: best-effort, resolves its URL
from an env var if not passed explicitly, logs and returns `False` rather
than raising on any failure -- a Slack outage or an unset webhook must
never affect the poll loop this runs inside.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

WEBHOOK_URL_ENV = "TT_ALERT_WEBHOOK_URL"


async def post_message(client: httpx.AsyncClient, text: str, webhook_url: str | None = None) -> bool:
    resolved_url = webhook_url or os.environ.get(WEBHOOK_URL_ENV)
    if not resolved_url:
        logger.debug("%s not set, skipping briefing delivery", WEBHOOK_URL_ENV)
        return False
    try:
        response = await client.post(resolved_url, json={"text": text}, timeout=10.0)
        response.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.warning("briefing Slack post failed: %s", exc)
        return False
