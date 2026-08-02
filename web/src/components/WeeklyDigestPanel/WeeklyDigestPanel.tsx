import { Section, Placeholder } from '../Section'
import { useWeeklyDigests } from '../../hooks/useWeeklyDigests'
import { routesById } from '../../geometry'
import { formatWeekRange, formatPercent, parseNarrative } from '../../lib/formatDigest'
import type { WeeklyDigest, WeeklyLineStat } from '../../api-types'
import styles from './WeeklyDigestPanel.module.css'

// A week with no completed trips at all (cold start, or every partition in
// the window missing) reports on_time_pct as a plain 0.0, not null -- see
// api-types.ts's WeeklyDigest docstring. Rendering that 0.0 directly would
// read as "0% on time" rather than "no data", the exact misleading-label
// trap this project's gap-honesty convention exists to avoid.
function hasData(digest: WeeklyDigest): boolean {
  return digest.on_time_count + digest.late_count > 0
}

function LineRow({ stat }: { stat: WeeklyLineStat }) {
  const route = routesById.get(stat.route_id)
  return (
    <li className={styles.lineRow}>
      <span className={styles.lineSwatch} style={{ background: route?.color ?? 'var(--color-text-dim)' }} />
      <span className={styles.lineName}>{route?.name ?? stat.route_id}</span>
      <span className={styles.linePct}>{formatPercent(stat.on_time_pct)}</span>
    </li>
  )
}

function LatestDigest({ digest }: { digest: WeeklyDigest }) {
  return (
    <div className={styles.latest}>
      <div className={styles.headline}>
        <span className={styles.range}>{formatWeekRange(digest.week_start, digest.week_end)}</span>
        {hasData(digest) ? (
          <span className={styles.pct}>{formatPercent(digest.on_time_pct)} on time</span>
        ) : (
          <span className={styles.pctUnavailable}>No data available</span>
        )}
      </div>
      {digest.days_covered < 7 && (
        <p className={styles.partialNote}>{digest.days_covered} of 7 days available this week</p>
      )}
      <p className={styles.narrative}>
        {parseNarrative(digest.narrative).map((span, index) =>
          span.bold ? <strong key={index}>{span.text}</strong> : <span key={index}>{span.text}</span>,
        )}
      </p>
      {digest.line_stats.length > 0 && (
        <ul className={styles.lineList}>
          {digest.line_stats.map((stat) => (
            <LineRow key={stat.route_id} stat={stat} />
          ))}
        </ul>
      )}
    </div>
  )
}

function PastDigestRow({ digest }: { digest: WeeklyDigest }) {
  return (
    <li className={styles.pastRow}>
      <span className={styles.range}>{formatWeekRange(digest.week_start, digest.week_end)}</span>
      {hasData(digest) ? (
        <span className={styles.pct}>{formatPercent(digest.on_time_pct)}</span>
      ) : (
        <span className={styles.pctUnavailable}>No data</span>
      )}
    </li>
  )
}

/** The "weekly announcement + log" surface for 05-ai-layer's weekly digest
 * (backend/API done 2026-08-01; this was the explicitly-deferred frontend
 * piece). Renders nothing while loading fails or the feature isn't
 * configured server-side (matches AlertsPanel's quiet-failure convention --
 * a non-critical historical panel shouldn't alarm on a transient error),
 * but shows an explicit empty state (as opposed to also going quiet) when
 * the feature IS configured and simply hasn't fired yet -- distinguishing
 * "broken" from "nothing to show yet" matters here since the very first
 * Monday 8am firing hasn't happened for a fresh deploy. */
export function WeeklyDigestPanel() {
  const { digests, loading, error } = useWeeklyDigests()

  if (error) return null

  const [latest, ...past] = digests

  return (
    <Section title="Weekly performance">
      {loading && <Placeholder>Loading weekly digest…</Placeholder>}
      {!loading && !latest && (
        <Placeholder>No weekly digest yet — the first one posts Monday 8am</Placeholder>
      )}
      {latest && <LatestDigest digest={latest} />}
      {past.length > 0 && (
        <ul className={styles.pastList}>
          {past.map((digest) => (
            <PastDigestRow key={digest.week_start} digest={digest} />
          ))}
        </ul>
      )}
    </Section>
  )
}
