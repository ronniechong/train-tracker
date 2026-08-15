import { useState } from 'react'
import { Section, Placeholder } from '../Section'
import { useWeeklyDigests } from '../../hooks/useWeeklyDigests'
import { routesById } from '../../geometry'
import { LEGEND_ORDER } from '../Legend'
import { formatWeekRange, formatPercent, parseNarrative } from '../../lib/formatDigest'
import { trackEvent } from '../../lib/analytics'
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

function LineCell({ stat }: { stat: WeeklyLineStat }) {
  const route = routesById.get(stat.route_id)
  return (
    <div className={styles.lineCell}>
      <span className={styles.lineSwatch} style={{ background: route?.color ?? 'var(--color-text-dim)' }} />
      <span className={styles.lineName}>{route?.name ?? stat.route_id}</span>
      <span className={styles.linePct}>{formatPercent(stat.on_time_pct)}</span>
    </div>
  )
}

// Same ordering as the sidebar Legend (LEGEND_ORDER, grouped by PTV color
// family, not alphabetical) so a line sits in the same relative position
// in both places. Unlisted routes fall back to alphabetical at the end,
// mirroring Legend.tsx's sortedForLegend.
function sortLineStats(stats: WeeklyLineStat[]): WeeklyLineStat[] {
  return [...stats].sort((a, b) => {
    const nameA = routesById.get(a.route_id)?.name ?? a.route_id
    const nameB = routesById.get(b.route_id)?.name ?? b.route_id
    const indexA = LEGEND_ORDER.indexOf(nameA)
    const indexB = LEGEND_ORDER.indexOf(nameB)
    if (indexA === -1 && indexB === -1) return nameA.localeCompare(nameB)
    if (indexA === -1) return 1
    if (indexB === -1) return -1
    return indexA - indexB
  })
}

// Two-column grid read top-to-bottom (left column first, then right) --
// CSS grid's default row-major flow would instead read left-to-right,
// splitting adjacent same-color-family lines across the two columns. Since
// stats are pre-sorted, splitting the array in half and rendering it as
// two independent columns keeps each column internally in order.
function LineGrid({ stats }: { stats: WeeklyLineStat[] }) {
  const sorted = sortLineStats(stats)
  const splitAt = Math.ceil(sorted.length / 2)
  const columns = [sorted.slice(0, splitAt), sorted.slice(splitAt)]
  return (
    <div className={styles.lineGrid}>
      {columns.map((column, index) => (
        <div className={styles.lineColumn} key={index}>
          {column.map((stat) => (
            <LineCell key={stat.route_id} stat={stat} />
          ))}
        </div>
      ))}
    </div>
  )
}

function DigestBody({ digest }: { digest: WeeklyDigest }) {
  return (
    <>
      {digest.days_covered < 7 && (
        <p className={styles.partialNote}>{digest.days_covered} of 7 days available this week</p>
      )}
      <p className={styles.narrative}>
        {parseNarrative(digest.narrative).map((span, index) =>
          span.bold ? <strong key={index}>{span.text}</strong> : <span key={index}>{span.text}</span>,
        )}
      </p>
      {digest.line_stats.length > 0 && <LineGrid stats={digest.line_stats} />}
    </>
  )
}

function LatestDigest({ digest }: { digest: WeeklyDigest }) {
  return (
    <li className={styles.weekBlock}>
      <div className={styles.headline}>
        <span className={styles.range}>{formatWeekRange(digest.week_start, digest.week_end)}</span>
        {hasData(digest) ? (
          <span className={styles.pct}>{formatPercent(digest.on_time_pct)} on time</span>
        ) : (
          <span className={styles.pctUnavailable}>No data available</span>
        )}
      </div>
      <DigestBody digest={digest} />
    </li>
  )
}

function PastDigestRow({ digest }: { digest: WeeklyDigest }) {
  const [expanded, setExpanded] = useState(false)
  const canExpand = hasData(digest)

  return (
    <li className={styles.weekBlock}>
      <button
        type="button"
        className={styles.pastToggle}
        onClick={() => {
          if (!canExpand) return
          if (!expanded) trackEvent('click-expand-past-digest', digest.week_start)
          setExpanded((prev) => !prev)
        }}
        aria-expanded={expanded}
        disabled={!canExpand}
      >
        <span className={styles.pastToggleLeft}>
          {canExpand && (
            <span className={styles.chevron} data-expanded={expanded} aria-hidden="true">
              ▸
            </span>
          )}
          <span className={styles.range}>{formatWeekRange(digest.week_start, digest.week_end)}</span>
        </span>
        {hasData(digest) ? (
          <span className={styles.pct}>{formatPercent(digest.on_time_pct)}</span>
        ) : (
          <span className={styles.pctUnavailable}>No data</span>
        )}
      </button>
      {expanded && (
        <div className={styles.pastBody}>
          <DigestBody digest={digest} />
        </div>
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
    // No title -- the Announcements modal's "Weekly performance" tab label
    // (Sidebar.tsx) already says this; a second heading inside the tab
    // body would just repeat it (2026-08-09).
    <Section>
      {loading && <Placeholder>Loading weekly digest…</Placeholder>}
      {!loading && !latest && (
        <Placeholder>No weekly digest yet — the first one posts Monday 8am</Placeholder>
      )}
      {latest && (
        <ul className={styles.weekList}>
          <LatestDigest digest={latest} />
          {past.map((digest) => (
            <PastDigestRow key={digest.week_start} digest={digest} />
          ))}
        </ul>
      )}
    </Section>
  )
}
