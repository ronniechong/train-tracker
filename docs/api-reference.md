# Public API reference — next-service lookup

Covers `GET /api/next-service` and `GET /api/stations`, added for M13 (the
train-tracker-query-web dependency). Both are GET-only, unauthenticated,
rate-limited, and CORS-scoped to the configured origin list, same as every
other route on this service.

This is an informal reference kept in sync with the implementation, not a
generated OpenAPI spec — the app deliberately disables the live schema
explorer (see `api/app.py`'s module docstring). Once shipped, this
response shape is a versioned external contract per CLAUDE.md's "External
consumers" section — it can no longer change silently.

## `GET /api/next-service`

Finds the soonest train from one station to another, either directly
(same line) or via one transfer at a known interchange.

### Query parameters

| Param | Required | Description |
|---|---|---|
| `from` | yes | Origin station name (e.g. `"Richmond"`). Matched exactly first, then by substring. |
| `to` | yes | Destination station name, same matching rules. |
| `route` | no | Line name (e.g. `"Pakenham"`), used only as a disambiguation signal when `from`/`to` matches more than one station. Ignored (falls through to the ambiguous result) if it doesn't resolve to a real line. |

### Response — `200 OK`

```json
{
  "from_station": {"station_id": "...", "name": "Richmond Railway Station"},
  "to_station": {"station_id": "...", "name": "Flinders Street Railway Station"},
  "generated_at": "2026-08-25T08:00:00Z",
  "reason": null,
  "legs": [
    {
      "trip_id": "...",
      "route_id": "...",
      "headsign": "City",
      "from_station": {"station_id": "...", "name": "Richmond Railway Station"},
      "from_platform_code": "2",
      "departure_time": "2026-08-25T08:05:00Z",
      "to_station": {"station_id": "...", "name": "Flinders Street Railway Station"},
      "to_platform_code": "5",
      "arrival_time": "2026-08-25T08:15:00Z"
    }
  ]
}
```

`legs` has:
- **1 entry** for a direct (same-line) service.
- **2 entries** for a single-transfer service — the second leg's
  `from_station` is the interchange.
- **0 entries** when `reason` is set (see below) — `from_station`/
  `to_station` at the top level are still populated so a caller can show
  "next trains from X" context even when nothing was found.

`from_platform_code`/`to_platform_code` are `null` whenever the static
feed doesn't carry a `platform_code` for that stop — not every station's
GTFS data has one, so callers must handle the null case rather than
assuming every leg names a platform.

### Failure contract

Three distinct, machine-readable cases — react to each differently, don't
treat this as a generic 404/200 split:

| `reason` | HTTP status | Meaning | Suggested caller behaviour |
|---|---|---|---|
| `unknown_station` | `404`, body `{"detail": {"reason": "unknown_station", "message": "..."}}` | `from` or `to` didn't resolve to exactly one station — not found, or still ambiguous even after `route` narrowing. | Ask the user to clarify/re-enter the station name. |
| `no_service_today` | `200`, `reason` field | `route` was given, resolved to a real line, and that line has zero calendar-active trips anywhere today. | State the real schedule gap (e.g. "the Pakenham line isn't running today"). |
| `no_route_found` | `200`, `reason` field | Both stations are valid, but no same-line or single-transfer path exists between them today (either genuinely, or outside this milestone's scope — see below). | Fall back to the PTV journey-planner link. |

### Scope limits (by design, not a bug)

- Same-line and **single-transfer only** — 2+ transfer journeys are out of
  scope (train-tracker-query-web's own documented boundary).
- Single-transfer routing only considers a curated interchange list
  (Flinders Street, Southern Cross, Richmond, Clifton Hill, North
  Melbourne) — `transfers.txt` was checked against the real pinned
  snapshot and found to not cover these interchanges at all.
- No tram/bus/V-Line legs, no walking directions.
- No minimum interchange dwell time is enforced beyond "the second leg
  departs after the first leg arrives" — a real walking-distance minimum
  connection time isn't modelled.

## `GET /api/stations`

The canonical station list, for reference/display use (e.g. autocomplete,
validating a name before calling `/api/next-service`). Not required for
train-tracker-query-web's own resolution — this service already resolves
names itself.

### Response — `200 OK`

```json
{
  "generated_at": "2026-08-25T08:00:00Z",
  "stations": [
    {
      "station_id": "...",
      "name": "Flinders Street Railway Station",
      "routes": [
        {"route_id": "...", "short_name": "Pakenham", "long_name": "Pakenham - City"}
      ]
    }
  ]
}
```

`routes` lists every line that calls at the station per the static
schedule, regardless of whether any of its trips run today.

### Failure

Both endpoints return `503` with a plain-text `detail` when the schedule
feature isn't configured yet (no static snapshot pinned), or when no
snapshot is pinned for today's service_date specifically — a distinct,
transient condition from any of the three query-outcome cases above, not
folded into `reason`.
