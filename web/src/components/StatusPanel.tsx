import type { LiveState } from '../hooks/useLiveFeed'
import type { FeedStatus, Train } from '../api-types'
import { Section } from './Section'
import { cx } from '../lib/cx'
import styles from './StatusPanel.module.css'

const STATUS_EXPLANATION: Record<Train['status'], string> = {
  live: 'confirmed by the live feed just now.',
  coasting: 'briefly missing from the live feed; still shown from its last known position.',
  ghost: 'not seen in the live feed for a while — position may be stale or scheduled, not live-confirmed.',
}

const CONNECTION_LABEL: Record<LiveState['connection'], string> = {
  connecting: 'Connecting…',
  live: 'Live',
  reconnecting: 'Reconnecting…',
}

function formatFeedName(kebabName: string): string {
  return kebabName
    .split('-')
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ')
}

// Three real states from two real signals -- not fabricated: a feed can be
// fresh (green), fresh but the poller is currently in backoff against the
// upstream API (yellow -- an early warning, not yet a problem for this
// feed specifically), or genuinely stale (red).
function feedDotClass(status: FeedStatus, backoffActive: boolean): { className: string; label: string } {
  if (status.stale) return { className: styles.dotRed, label: 'stale' }
  if (backoffActive) return { className: styles.dotYellow, label: 'degraded — poller backing off' }
  return { className: styles.dotGreen, label: 'ok' }
}

function trainCounts(trains: Map<string, Train>): Record<Train['status'], number> {
  const counts: Record<Train['status'], number> = { live: 0, coasting: 0, ghost: 0 }
  for (const train of trains.values()) counts[train.status]++
  return counts
}

interface StatusPanelProps {
  liveState: LiveState
}

export function StatusPanel({ liveState }: StatusPanelProps) {
  const counts = trainCounts(liveState.trains)

  return (
    <Section title="Data status">
      <p
        className={cx(
          styles.connection,
          liveState.connection === 'live' ? styles.connectionLive : styles.connectionPending,
        )}
      >
        {CONNECTION_LABEL[liveState.connection]}
      </p>
      {liveState.backoffActive && <p className={styles.warning}>Backoff active — poller throttled</p>}
      <ul className={styles.feedList}>
        {Object.entries(liveState.feeds).map(([name, status]) => {
          const dot = feedDotClass(status, liveState.backoffActive)
          return (
            <li key={name}>
              <span className={cx(styles.dot, dot.className)} title={dot.label} />
              {formatFeedName(name)}
            </li>
          )
        })}
      </ul>
      <div className={styles.trainCounts}>
        <span>{counts.live} live</span>
        <span>{counts.coasting} coasting</span>
        <span>{counts.ghost} ghost</span>
        <span className={styles.info}>
          <span className={styles.infoIcon}>!</span>
          <span className={styles.infoTooltip}>
            <strong>Live</strong> — {STATUS_EXPLANATION.live}
            <br />
            <strong>Coasting</strong> — {STATUS_EXPLANATION.coasting}
            <br />
            <strong>Ghost</strong> — {STATUS_EXPLANATION.ghost}
          </span>
        </span>
      </div>
    </Section>
  )
}
