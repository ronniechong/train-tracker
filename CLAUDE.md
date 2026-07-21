# CLAUDE.md — train-tracker

> Instructions for working in this repository. Read fully before changing code.

## What this project is

**train-tracker** — a close-to-real-time Melbourne metro train tracker built on
Victoria's open GTFS-Realtime feeds: a polling/state service, a JSON API + SSE
stream, a live map frontend, and an AI layer (disruption briefings, natural-
language queries, clearly-labelled inferences).

Design priorities, in order: be a polite consumer of the upstream public API,
be secure by construction, be observable to the bone. Data honesty is a feature:
gaps are recorded, staleness is displayed, inferences are labelled as inferences.

## Repository layout

- `service/` — Python service (poller, state store, API). The Docker build context.
- `web/` — static frontend (MapLibre GL). Deploys to GitHub Pages via Actions.
  Never enters Docker; contains no secrets by construction.
- `deploy/` — compose file, Caddyfile, Prometheus config, Grafana dashboard JSON.
- `spike/` — the M1 data-spike scripts and findings. Raw captures are gitignored.

## Data source facts (verified against the live API — do not rediscover)

- Feeds: Metro Train GTFS-R (Vehicle Positions, Trip Updates, Service Alerts),
  protobuf over plain HTTP GET. No push mechanism; no conditional GET support
  (no ETag/Last-Modified).
- Auth header is `KeyId` (the published OpenAPI docs claim
  `Ocp-Apim-Subscription-Key` — they are wrong; legacy Axway gateway).
- 401 responses echo the API key in `WWW-Authenticate`. The log redaction
  filter exists because of this; never weaken it, never paste raw auth errors.
- `x-rate-limit` response header (VP/TU): JSON throttle windows with `remaining`
  counts — drives adaptive backoff.
- Measured (66h spike): VP header changes ~29–30s; TU ~10s or faster; 100%
  rollover correlation. VP populates ONLY lat/lon/bearing, vehicle.id, trip_id,
  route_id — station state must be derived. Coverage vs schedule dips to
  33–81% by time band. Static portal regenerates trip_ids on each publish.
- Trip Updates' `stop_time_update` list is a rolling window, not a trip's
  full schedule — entries get trimmed off both ends as a trip progresses
  (single-entry lists are common). Telling "genuine trip origin/terminus"
  from "list got trimmed to this stop" requires checking whether the raw
  entry's `arrival`/`departure` field is genuinely absent, not its position
  in the current list.
- Static GTFS: `opendata.transport.vic.gov.au/dataset/gtfs-schedule`, a
  single ~270MB zip-of-zips across all transport modes, unauthenticated.
  Metro Train is mode `2` (`2/google_transit.zip` inside the outer zip).
  Unlike the realtime feeds, this endpoint sends real `ETag`/`Last-Modified`
  headers — check before downloading, don't pull 270MB on every run.

## Settled technical decisions (do not re-litigate silently — flag first)

| Decision | Choice | Revisit if |
|---|---|---|
| Poll cadence | 10s + jitter; adaptive slowdown when network idle overnight | Measured TU cadence changes |
| Feed roles | TU-primary (schedule state), VP-secondary (coordinates), per-field freshness merge | Discrepancy metric ≥2% of merges |
| Station state | Derived: geofence vs stops.txt + TU cross-check | Feed starts populating stop fields |
| Missing trains | live → coasting → ghost state machine, reason codes attached | Loop-gap metric ≥99% → dedicated in-loop state |
| City Loop | Freeze-in-place + max-freeze timeout + teleport smoothing | Long-tail anomalies become common |
| trip_id join | Direct join against per-service-day pinned static snapshot | Join rate <98% |
| Staleness | Feed header age (>3–5min), NEVER entity count | — |
| Storage | Day-partitioned SQLite keyed by SERVICE day; retention by file deletion | — |
| Time | UTC stored everywhere; `service_date` from GTFS calendar; 24:xx parsed per spec; DST fixtures required | — |
| Eventing | In-process asyncio hub behind a swappable interface | Multi-process consumers appear |
| Metrics | prometheus-client `/metrics`; every design gate has a metric | — |

## Security invariants (standing rules — a violation is never a refactor)

1. **Exactly one upstream consumer.** No code path — including any AI/agent
   path — may trigger a request to the upstream API from a user action.
   The poller is the only client. No refresh-on-demand, no passthrough.
2. API key via environment only. Never in this repo, logs, client code, or
   docs. Redaction filter active from day one.
3. Public surface is GET-only derived state: strict CORS from an env origin
   list, SSE connection caps, rate limiting at the ingress.
4. The API binds to localhost; the reverse proxy is the only ingress.
5. Containers: non-root, read-only root FS, `/data` the only writable mount,
   egress-restricted poller, `restart: unless-stopped`.
6. This repo is public: gitleaks runs pre-commit and in CI; `.env`, `data/`,
   `*.db`, and raw captures are gitignored. Scrub headers/keys from anything
   pasted into docs or commit messages.
7. AI layer: upstream feed text is untrusted data, never instructions; hard
   budget caps and per-run token limits; every inference labelled with its
   evidence.

## Conventions

- Python 3.12, `uv`. Multi-stage Dockerfile (uv builder → non-root runtime).
- `service/.dockerignore` excludes tests, venvs, caches, and any `.env`.
- All stored timestamps UTC; `Australia/Melbourne` only at parse/display edges.
- Host-specific values (bind-mount paths, origins, webhooks) come from the
  environment or a gitignored `docker-compose.override.yml` — never hardcoded.
- Data lineage: every derived fact traceable to (feed header timestamp,
  static snapshot version).

## Behavioural rules for Claude in this repo

1. Before implementing any task, raise at least one risk, gap, or alternative;
   if the task is genuinely fine, say so in one sentence with the reason.
2. Never silently undo a settled decision above — flag and wait.
3. Check every change against the security invariants, especially: does this
   create a new path to the upstream API, widen the public surface, or touch
   the key?
4. Additional project context may be provided via `CLAUDE.local.md`
   (gitignored). If present, read it first and follow its instructions.
