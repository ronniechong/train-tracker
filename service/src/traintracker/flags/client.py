"""Feature flags via Flagsmith (remote evaluation -- free tier has no local eval).

`FLAGSMITH_SERVER_ENV_KEY` is the server-side environment key from the
Flagsmith dashboard (Environment Settings). It is a secret: never commit it,
log it, or let it reach the public repo -- same tier as the upstream API key
(see redaction.py).

Flag checks default OFF on any failure (missing key, network error, unknown
flag name) so an outage or a typo never silently turns a feature on.
"""

from __future__ import annotations

import logging
import os
import time

from flagsmith import Flagsmith
from flagsmith.models import Flags

logger = logging.getLogger(__name__)

_CACHE_SECONDS = 30

_client: Flagsmith | None = None
_cached_flags: Flags | None = None
_cached_at: float = 0.0


def _get_client() -> Flagsmith | None:
    global _client
    if _client is not None:
        return _client
    env_key = os.environ.get("FLAGSMITH_SERVER_ENV_KEY")
    if not env_key:
        logger.warning("FLAGSMITH_SERVER_ENV_KEY not set -- all flags default off")
        return None
    _client = Flagsmith(environment_key=env_key, enable_local_evaluation=False)
    return _client


def is_enabled(flag_name: str, default: bool = False) -> bool:
    """Check a feature flag, caching the environment document for
    `_CACHE_SECONDS` so a hot code path doesn't hit the Flagsmith API on
    every call."""
    global _cached_flags, _cached_at

    client = _get_client()
    if client is None:
        return default

    now = time.monotonic()
    if _cached_flags is None or now - _cached_at > _CACHE_SECONDS:
        try:
            _cached_flags = client.get_environment_flags()
            _cached_at = now
        except Exception:
            logger.exception("Flagsmith fetch failed for %r -- using default", flag_name)
            return default

    try:
        return _cached_flags.is_feature_enabled(flag_name)
    except Exception:
        logger.exception("Flag %r not resolvable -- using default", flag_name)
        return default
