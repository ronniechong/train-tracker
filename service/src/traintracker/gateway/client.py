"""Realtime GTFS-R gateway client.

The ONLY code path allowed to call the upstream Metro Train feeds (security
invariant #1 — exactly one upstream consumer; no per-user passthrough).

Auth is the `KeyId` header, not the `Ocp-Apim-Subscription-Key` the
published OpenAPI docs claim — verified live 2026-07-17 against a legacy
Axway/Vordel gateway fronting the documented API (see spike/probes.md).
A 401 for a *recognized but unauthorized* key echoes the key verbatim in
`WWW-Authenticate`'s `error_description` — this client never logs or
propagates response headers/bodies on the auth-error path, and the
process-wide filter in `traintracker.redaction` is the backstop for
anything that does.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import Enum

import httpx

logger = logging.getLogger(__name__)

API_KEY_ENV = "VIC_TRANSPORT_API_KEY"
BASE_URL_ENV = "TT_API_BASE_URL"

# Confirmed live 2026-07-17 (spike/capture.py, spike/probes.md).
DEFAULT_BASE_URL = (
    "https://api.opendata.transport.vic.gov.au/opendata/public-transport"
    "/gtfs/realtime/v1/metro"
)


class Feed(str, Enum):
    VEHICLE_POSITIONS = "vehicle-positions"
    TRIP_UPDATES = "trip-updates"
    SERVICE_ALERTS = "service-alerts"


class GatewayError(Exception):
    """Base error for gateway failures. Subclasses must never carry a raw
    response body or header value in their message — see module docstring."""


class GatewayAuthError(GatewayError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"gateway rejected credentials (HTTP {status_code})")


@dataclass(frozen=True)
class ThrottleWindow:
    window: int
    type: str
    remaining: int


@dataclass(frozen=True)
class FeedResponse:
    feed: Feed
    payload: bytes
    throttle: tuple[ThrottleWindow, ...]


def _parse_rate_limit(header_value: str | None) -> tuple[ThrottleWindow, ...]:
    """VP/TU send `x-rate-limit` as a JSON array of throttle windows; SA
    sends none (M1 finding). Malformed input degrades to "unknown", not a
    hard failure — this header is an optimization signal, not load-bearing."""
    if not header_value:
        return ()
    try:
        raw = json.loads(header_value)
    except (ValueError, TypeError):
        logger.warning("x-rate-limit header present but not valid JSON")
        return ()
    return tuple(
        ThrottleWindow(window=w["window"], type=w["type"], remaining=w["remaining"])
        for w in raw
    )


def base_url() -> str:
    return os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL)


class GatewayClient:
    """Thin, single-purpose HTTP client for the three Metro Train feeds.

    `httpx.Client` honours standard proxy env vars (`HTTPS_PROXY` etc —
    `trust_env=True` is the default) so the 2a egress-sidecar decision stays
    a compose/env config swap, never a code change, if it's ever revisited.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url_override: str | None = None,
        timeout: float = 15.0,
    ):
        self._api_key = api_key or os.environ.get(API_KEY_ENV)
        if not self._api_key:
            raise GatewayError(f"{API_KEY_ENV} is not set")
        self._base_url = base_url_override or base_url()
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GatewayClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def fetch(self, feed: Feed) -> FeedResponse:
        url = f"{self._base_url}/{feed.value}"
        logger.debug("gateway request: GET %s", url)  # never log headers
        response = self._client.get(url, headers={"KeyId": self._api_key})

        if response.status_code in (401, 403):
            logger.warning(
                "gateway auth rejected (status=%d, feed=%s)",
                response.status_code,
                feed.value,
            )
            raise GatewayAuthError(response.status_code)
        response.raise_for_status()

        return FeedResponse(
            feed=feed,
            payload=response.content,
            throttle=_parse_rate_limit(response.headers.get("x-rate-limit")),
        )
