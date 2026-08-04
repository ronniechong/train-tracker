import { Section } from '../Section'
import { useAlerts } from '../../hooks/useAlerts'
import { formatTime } from '../../lib/formatTime'
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
 * in this codebase. Renders nothing when there are no active alerts,
 * matching StatusPanel's "only show a warning row when there's something
 * to warn about" convention -- an empty "No alerts" card would be sidebar
 * clutter for the common case. */
export function AlertsPanel() {
  const { alerts, loading, error } = useAlerts()

  if (loading || error || alerts.length === 0) return null

  return (
    <Section title={`Service alerts (${alerts.length})`}>
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
                  {startedAt && `since ${formatTime(startedAt)}`}
                </span>
              )}
            </li>
          )
        })}
      </ul>
    </Section>
  )
}
