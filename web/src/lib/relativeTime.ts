// Computed at the moment each update renders, not live-ticking while a
// tooltip/panel is open -- precise enough given the ~10s poll cadence this
// data is refreshed at anyway. Shared by map/trainMarkers.ts (tooltip) and
// components/StationPanel.tsx.
export function relativeTime(iso: string | null): string {
  if (!iso) return 'unknown'
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  return `${Math.round(seconds / 60)}m ago`
}
