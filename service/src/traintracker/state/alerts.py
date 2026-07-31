"""Parse Service Alerts feed entities into structured, queryable local state.

Unlike TU/VP, SA carries no trip_id in `informed_entity` -- only route_id,
stop_id, and direction_id, each independently optional. Matching an alert
to a specific train is therefore always a coarse route/stop/direction join,
never a confirmed per-trip fact -- `alerts_matching()` makes that wildcard
semantics explicit (a field absent on the alert side OR the query side
means "don't filter on this axis") rather than letting a caller assume a
match is more precise than the feed actually supports.

This module is a pure, single-cycle parse, same shape as `state/merge.py`:
no memory of previous polls. The SA feed is a full snapshot of currently-
known alerts each time it's fetched (not a delta), so a caller just
replaces its stored alert set with each fresh parse -- no need to carry
anything forward here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def _epoch_to_utc(epoch_str: str | None) -> datetime | None:
    if epoch_str is None:
        return None
    return datetime.fromtimestamp(int(epoch_str), tz=timezone.utc)


def _translated_text(field: dict | None) -> str | None:
    if not field:
        return None
    translations = field.get("translation", [])
    if not translations:
        return None
    for translation in translations:
        if translation.get("language") == "en":
            return translation.get("text")
    return translations[0].get("text")


@dataclass(frozen=True)
class InformedEntity:
    route_id: str | None
    stop_id: str | None
    direction_id: int | None


@dataclass(frozen=True)
class ActivePeriod:
    start: datetime | None  # None => open start ("always was active")
    end: datetime | None  # None => open end ("still active, no known end")


@dataclass(frozen=True)
class Alert:
    id: str
    cause: str | None
    effect: str | None
    header_text: str | None
    description_text: str | None
    url: str | None
    active_periods: tuple[ActivePeriod, ...]
    informed_entities: tuple[InformedEntity, ...]


def _active_periods(raw: list[dict]) -> tuple[ActivePeriod, ...]:
    return tuple(
        ActivePeriod(start=_epoch_to_utc(p.get("start")), end=_epoch_to_utc(p.get("end")))
        for p in raw
    )


def _informed_entities(raw: list[dict]) -> tuple[InformedEntity, ...]:
    return tuple(
        InformedEntity(
            route_id=e.get("route_id"),
            stop_id=e.get("stop_id"),
            direction_id=e.get("direction_id"),
        )
        for e in raw
    )


def parse_alerts(sa_feed: dict) -> dict[str, Alert]:
    """One entry per SA feed entity, keyed by the feed's own entity id
    (stable across polls per the live fixture -- verified against the
    2026-07-18 replay capture). Entities with no `alert` payload or no id
    are skipped rather than raising -- an SA feed decode producing a
    malformed entity should not take down the whole poll cycle."""
    alerts: dict[str, Alert] = {}
    for entity in sa_feed.get("entity", []):
        alert = entity.get("alert")
        entity_id = entity.get("id")
        if not alert or entity_id is None:
            continue
        alerts[entity_id] = Alert(
            id=entity_id,
            cause=alert.get("cause"),
            effect=alert.get("effect"),
            header_text=_translated_text(alert.get("header_text")),
            description_text=_translated_text(alert.get("description_text")),
            url=_translated_text(alert.get("url")),
            active_periods=_active_periods(alert.get("active_period", [])),
            informed_entities=_informed_entities(alert.get("informed_entity", [])),
        )
    return alerts


def is_active(alert: Alert, now: datetime) -> bool:
    """No active_period at all means "always active" per the GTFS-RT spec
    (an alert with unbounded scope) -- distinct from an empty list meaning
    "never active", which the spec does not define and this feed has never
    been observed to send."""
    if not alert.active_periods:
        return True
    for period in alert.active_periods:
        if period.start is not None and now < period.start:
            continue
        if period.end is not None and now > period.end:
            continue
        return True
    return False


def _entity_matches(
    entity: InformedEntity, *, route_id: str | None, stop_id: str | None, direction_id: int | None
) -> bool:
    if entity.route_id is not None and route_id is not None and entity.route_id != route_id:
        return False
    if entity.stop_id is not None and stop_id is not None and entity.stop_id != stop_id:
        return False
    if (
        entity.direction_id is not None
        and direction_id is not None
        and entity.direction_id != direction_id
    ):
        return False
    return True


def alerts_matching(
    alerts: dict[str, Alert],
    now: datetime,
    *,
    route_id: str | None = None,
    stop_id: str | None = None,
    direction_id: int | None = None,
) -> list[Alert]:
    """Active alerts whose informed_entity set is compatible with the given
    filters. A filter left as `None` matches anything on that axis; an
    alert with no informed_entity at all is treated as network-wide and
    always matches. Calling with no filters returns every active alert.

    This is a coarse route/stop/direction join, not trip-level confirmation
    -- see module docstring. Callers (05b's `get_active_alerts` tool, the
    station-schedule overlay) must not present a match here as certainty
    about a specific train."""
    matched = []
    for alert in alerts.values():
        if not is_active(alert, now):
            continue
        if not alert.informed_entities:
            matched.append(alert)
            continue
        if any(
            _entity_matches(e, route_id=route_id, stop_id=stop_id, direction_id=direction_id)
            for e in alert.informed_entities
        ):
            matched.append(alert)
    return matched
