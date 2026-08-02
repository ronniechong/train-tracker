interface GoatCounter {
  count: (vars: { path: string; title?: string; event?: boolean }) => void
}

declare global {
  interface Window {
    goatcounter?: GoatCounter
  }
}

/** Fires a GoatCounter custom event. Safe to call before the async
 * count.js script has loaded (or if it's blocked/ad-blocked) -- silently
 * a no-op rather than throwing, since analytics must never break a real
 * user interaction. */
export function trackEvent(path: string, title?: string): void {
  window.goatcounter?.count({ path, title, event: true })
}

/** GoatCounter buckets events by exact `path` string, not `title` -- so a
 * per-station breakdown needs the station baked into the path itself, not
 * just passed as a label. One path per station regardless of how it was
 * selected (search vs. map click); the entry method goes in `title`
 * instead, keeping the events list to ~220 rows instead of ~440. */
export function stationSlug(stationName: string): string {
  return stationName
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

export function trackStationSelect(stationName: string, source: 'search' | 'map'): void {
  trackEvent(`select-station/${stationSlug(stationName)}`, `via ${source}`)
}
