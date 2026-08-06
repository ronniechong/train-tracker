#!/bin/sh
# Brings up tailscaled, authenticates, then publishes the shared network
# namespace's Caddy port via Funnel. Written by hand rather than relying on
# the image's built-in `TS_*` env-var handling because that covers
# `tailscale up` but not `tailscale funnel`/`tailscale serve` — this just
# runs the steps directly.
#
# Requires Funnel enabled for this node in the tailnet's admin console
# first (a one-time, out-of-band step this script cannot perform).
# `tailscale serve` (below) needs no equivalent admin-console step —
# tailnet-only reachability doesn't require the extra enablement Funnel's
# public exposure does.
set -eu

tailscaled --state=/var/lib/tailscale/tailscaled.state \
	--socket=/var/run/tailscale/tailscaled.sock &

until tailscale status --json >/dev/null 2>&1; do
	sleep 1
done

tailscale up --authkey="${TS_AUTHKEY}" --hostname="${TS_HOSTNAME}" --accept-dns=false

# Caddy's :8081 (POST /briefing/trigger only) is published tailnet-only via
# `serve`, deliberately never `funnel` — a port cannot be both at once
# (Tailscale: the most recent config wins, whole-port), so this must stay a
# separate call on a separate port from the funnel line below, never merged
# into it. Reachable at
# https://${TS_HOSTNAME}.<tailnet>.ts.net:8443/briefing/trigger from
# devices on the tailnet only.
tailscale serve --bg --https=8443 localhost:8081

tailscale funnel --bg "${TARGET_PORT}"

wait
