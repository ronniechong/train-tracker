import { Section, Placeholder } from '../Section'
import { formatDateTime } from '../../lib/formatTime'
import type { AlertsState } from '../../hooks/useAlerts'
import type { Alert } from '../../api-types'
import styles from './AlertsPanel.module.css'

// Effect enum -> short human label. Falls back to the raw value for any
// effect not seen in the M1/2g soak captures -- an unrecognised enum
// should still show something, not disappear silently.
const EFFECT_LABEL: Record<string, string> = {
  NO_SERVICE: 'No service',
  REDUCED_SERVICE: 'Reduced service',
  SIGNIFICANT_DELAYS: 'Delays',
  DETOUR: 'Detour',
  ADDITIONAL_SERVICE: 'Additional service',
  MODIFIED_SERVICE: 'Modified service',
  STOP_MOVED: 'Stop moved',
  OTHER_EFFECT: 'Disruption',
  UNKNOWN_EFFECT: 'Disruption',
}

// Distinct resolved line names for an alert, deduped (an alert's
// informed_entity list can repeat the same line across stop/direction
// variants, or pair a real route with its "-R" bus-replacement id -- see
// gtfs/routes.py). `route_name` is only present when a static snapshot
// was pinned when the API resolved it; entities that didn't resolve are
// dropped rather than shown as a raw route_id.
function lineNames(alert: Alert): string[] {
  return [...new Set(alert.informed_entities.map((e) => e.route_name).filter((n): n is string => !!n))]
}

// Earliest active_period start, i.e. "since when" this alert has applied
// -- null (rendered as nothing) when the alert has an open/unbounded
// start, per state/alerts.py's ActivePeriod semantics.
function since(alert: Alert): string | null {
  const starts = alert.active_periods.map((p) => p.start).filter((s): s is string => !!s)
  if (starts.length === 0) return null
  return starts.reduce((earliest, s) => (s < earliest ? s : earliest))
}

/** Network-wide "what's currently disrupted" summary -- deliberately
 * compact (header text + effect only, no per-line filtering yet) since
 * this is the first frontend surface for Service Alerts content at all
 * (05a pass 3): the feed was previously polled but never parsed anywhere
 * in this codebase.
 *
 * Takes `alerts`/`loading`/`error` as props rather than calling `useAlerts`
 * itself (2026-08-09) -- the Announcements modal's tab label needs the
 * alert count before this panel is even mounted (it's only mounted for
 * the active tab), so the hook is called once at the modal level
 * (Sidebar.tsx) and shared, rather than each duplicating the fetch or the
 * count living somewhere the tab label can't see.
 *
 * Shows an explicit "no active alerts" state (as opposed to rendering
 * nothing) now that this lives in its own Announcements-modal tab -- unlike
 * the original stacked-below-the-digest layout, where going quiet here
 * just meant less content in an otherwise non-empty modal, an isolated tab
 * going fully blank on zero alerts is indistinguishable from broken. Still
 * renders nothing on `error` (matches WeeklyDigestPanel's quiet-failure
 * convention for a non-critical panel), and a real loading state for the
 * same "not broken, just not ready" reason.
 *
 * No title on the Section -- the tab label (built in Sidebar.tsx, count
 * included) already says "Service alerts (N)"; a second heading inside
 * the tab body would just repeat it. */
export function AlertsPanel({ alerts, loading, error }: AlertsState) {
  if (error) return null

  return (
    <Section className={styles.section}>
      {loading && <Placeholder>Loading alerts…</Placeholder>}
      {!loading && alerts.length === 0 && <Placeholder>No active service alerts</Placeholder>}
      {!loading && alerts.length > 0 && (
        <ul className={styles.list}>
          {alerts.map((alert) => {
            const lines = lineNames(alert)
            const startedAt = since(alert)
            return (
              <li key={alert.id} className={styles.row}>
                <span className={styles.effect}>
                  {(alert.effect && EFFECT_LABEL[alert.effect]) ?? 'Disruption'}
                </span>
                <span className={styles.headerText}>{alert.header_text ?? 'Service alert'}</span>
                {(lines.length > 0 || startedAt) && (
                  <span className={styles.meta}>
                    {lines.length > 0 && lines.join(', ')}
                    {lines.length > 0 && startedAt && ' — '}
                    {startedAt && `since ${formatDateTime(startedAt)}`}
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </Section>
  )
}
