"""Slack incoming-webhook delivery for 05e's disruption briefings -- the
first app-code Slack integration in this repo (2f's ops alerts are
Grafana's own Slack contact point, configured in Grafana, not here).

Deliberately a SEPARATE webhook/env var from `TT_ALERT_WEBHOOK_URL` (2f's
ops-alerts channel): Slack binds an incoming webhook to one channel at
creation time with no per-payload override, so "different audience"
(rider-facing disruption info vs. infra health) means "different webhook",
not a channel field in the POST body (M5 05e kickoff decision).

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

WEBHOOK_URL_ENV = "TT_BRIEFING_WEBHOOK_URL"


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
