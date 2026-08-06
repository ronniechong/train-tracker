"""Slack incoming-webhook delivery for disruption briefings -- the first
app-code Slack integration in this repo (ops alerts are Grafana's own
Slack contact point, configured in Grafana, not here).

Reuses `TT_ALERT_WEBHOOK_URL` (the ops-alerts webhook) rather than a
dedicated one -- two config values that must only ever stay in sync isn't
worth the extra Slack setup. Splitting them again later is a one-line env
change, not a code change.

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
