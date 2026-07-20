# Q7 — Endpoint behaviour probes

One-off manual checks, run by hand (not part of the `capture.py` polling
loop). Do **not** run these in a loop or script them into CI — the point is
a handful of deliberate requests, well under the 20-27 calls/min quota.

Requires `VIC_TRANSPORT_API_KEY` exported in the shell.

Base URL: `https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/metro`
Auth header: `KeyId: $VIC_TRANSPORT_API_KEY`

## 1. Baseline fetch — payload size, latency, headers

```sh
curl -s -D - -o /tmp/vp.pb -w '\ntotal_time: %{time_total}s\nsize: %{size_download} bytes\n' \
  -H "KeyId: $VIC_TRANSPORT_API_KEY" \
  "https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/metro/vehicle-positions"
```

Repeat for `/trip-updates` and `/service-alerts`. Record for each:
- Payload size (bytes)
- `total_time` (latency from wherever you're running this — note the
  origin; results from a home machine won't match the OCI Melbourne figure)
- Any of: `ETag`, `Last-Modified`, `Cache-Control`, `Age`, rate-limit headers

## 2. Conditional GET support

```sh
ETAG=$(curl -s -D - -o /dev/null \
  -H "KeyId: $VIC_TRANSPORT_API_KEY" \
  "https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/metro/vehicle-positions" \
  | grep -i '^etag:' | tr -d '\r' | cut -d' ' -f2)
echo "ETag: $ETAG"

curl -s -o /dev/null -w '%{http_code}\n' \
  -H "KeyId: $VIC_TRANSPORT_API_KEY" \
  -H "If-None-Match: $ETAG" \
  "https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/metro/vehicle-positions"
```

Expect `304` if conditional GET is honoured, `200` if not. Try the
`Last-Modified` / `If-Modified-Since` pair too if the baseline fetch
returned a `Last-Modified` header.

## 3. Malformed request — error shape

Do **not** probe by exceeding the rate limit. Instead hit a bad path with a
valid key, to see the error body/shape without risking key suspension:

```sh
curl -s -D - \
  -H "KeyId: $VIC_TRANSPORT_API_KEY" \
  "https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/metro/not-a-real-endpoint"
```

Also try an invalid/missing key against a real path, to distinguish
"bad auth" from "bad path" error shapes:

```sh
curl -s -D - \
  -H "KeyId: invalid-key-xyz" \
  "https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/metro/vehicle-positions"
```

## Results

Run 2026-07-20T00:43 UTC from the deployment host (Melbourne LAN).

| Probe | Result |
|---|---|
| VP payload size | 19,087 bytes |
| TU payload size | 80,525 bytes |
| SA payload size | 35,963 bytes |
| Latency (origin: Melbourne LAN) | VP 0.21s, TU 0.20s, SA 0.17s (not representative of the OCI-Melbourne figure originally planned — self-hosted deployment was chosen instead, see M1 doc 2026-07-17 log) |
| ETag present? | No, on any of the three feeds |
| Last-Modified present? | No, on any of the three feeds |
| Cache-Control / Age headers | VP: none. TU: `Cache-Control: no-store, no-cache`. SA: `Cache-Control: public, max-age=90, s-maxage=90` + `Age: 11` (confirms SA is CDN-cached ~90s server-side; VP/TU are not) |
| Rate-limit headers present? | Yes — `x-rate-limit` (VP/TU only, not SA): JSON array of throttle windows, e.g. `[{"window":0,"type":"throttle","remaining":23},{"window":59,"type":"throttle","remaining":959}]`. Two windows tracked (per-second-ish burst + rolling window); machine-readable, could drive adaptive backoff in the real poller instead of the hardcoded ~20-27/min assumption |
| Conditional GET honoured (304)? | Not applicable — no ETag or Last-Modified to condition on. Plain GET + timestamp/header dedupe is the only option (matches the pre-committed Q7 fallback) |
| Bad path -> status + body shape | `404`, `content-type: application/soap+xml`, generic Vordel/Axway SOAP fault body (`fault:MessageBlocked`) with no detail — the legacy gateway wrapper, not a GTFS-specific error shape |
| Bad key -> status + body shape | `401`, same generic SOAP fault body as bad-path, but the real detail is in the `WWW-Authenticate` header: `error="Invalid API-Key", error_description="API Key not authorized: <key>"` — confirms the earlier finding that auth errors echo the key in `error_description`; this probe intentionally used the fake key `invalid-key-xyz`, not the real one |

**Note on auth header:** the portal's published OpenAPI specs (Azure
APIM-based docs) claim the header is `Ocp-Apim-Subscription-Key`. That's
wrong for the live gateway. A 401 against that header returns
`WWW-Authenticate: ApiKey realm="api-realm", error="Invalid API-Key",
error_description="Failed to find key field: KeyId"` — the actual gateway
is still fronted by a legacy Axway/Vordel layer expecting `KeyId`. Verified
live 2026-07-17 with a real key (200 OK, correctly decoded protobuf on all
three feeds). `capture.py` uses `KeyId`.

**Caution:** the 401 error body for a *recognized but unauthorized* key
(`error_description: "API Key not authorized: <key>"`) echoes the key
value back verbatim. Don't paste raw error bodies from a failed auth
attempt into logs, chat, or anywhere else without redacting the key first
— probe #3 above ("bad key" test) deliberately uses a fake key for this
reason; never substitute a real key into that specific probe.
