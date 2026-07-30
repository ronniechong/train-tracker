#!/bin/sh
# M3: brings up tailscaled, authenticates, then publishes the shared
# network namespace's Caddy port via Funnel. Written by hand rather than
# relying on the image's built-in `TS_*` env-var handling because that
# covers `tailscale up` but not `tailscale funnel` — this just runs all
# three steps directly.
#
# Requires Funnel enabled for this node in the tailnet's admin console
# first (a one-time, out-of-band step this script cannot perform) — see
# ops/runbook.md in the private working-docs repo for the exact steps.
set -eu

tailscaled --state=/var/lib/tailscale/tailscaled.state \
	--socket=/var/run/tailscale/tailscaled.sock &

until tailscale status --json >/dev/null 2>&1; do
	sleep 1
done

tailscale up --authkey="${TS_AUTHKEY}" --hostname="${TS_HOSTNAME}" --accept-dns=false

tailscale funnel --bg "${TARGET_PORT}"

wait
