import { Section } from './Section'
import { useAlerts } from '../hooks/useAlerts'
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
        {alerts.map((alert) => (
          <li key={alert.id} className={styles.row}>
            <span className={styles.effect}>
              {(alert.effect && EFFECT_LABEL[alert.effect]) ?? 'Disruption'}
            </span>
            <span className={styles.headerText}>{alert.header_text ?? 'Service alert'}</span>
            {alert.url && (
              <a className={styles.link} href={alert.url} target="_blank" rel="noopener noreferrer">
                Details
              </a>
            )}
          </li>
        ))}
      </ul>
    </Section>
  )
}
