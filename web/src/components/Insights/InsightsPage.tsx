import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { useInsights } from '../../hooks/useInsights'
import { routesById } from '../../geometry'
import { LEGEND_ORDER } from '../Legend/Legend'
import type { InsightsLineStat, InsightsRangeName } from '../../api-types'
import { relativeTime } from '../../lib/relativeTime'
import styles from './InsightsPage.module.css'

const RANGE_OPTIONS: { name: InsightsRangeName; label: string }[] = [
  { name: 'today', label: 'Today' },
  { name: 'yesterday', label: 'Yesterday' },
  { name: 'last7', label: 'Last 7 days' },
  { name: 'last30', label: 'Last 30 days' },
]

const SAMPLE_SIZE_GUARD = 20 // trips/week floor, same as the weekly digest's own ranking guard

function lineName(routeId: string): string {
  return routesById.get(routeId)?.name ?? routeId
}

function lineColor(routeId: string): string {
  return routesById.get(routeId)?.color ?? 'var(--color-accent)'
}

function ranThisRange(line: InsightsLineStat): number {
  return line.on_time_count + line.late_count
}

// Same ordering as the map sidebar's Legend (PTV's own color-family
// grouping, City Circle first) -- so a line occupies the same relative
// position whether you're looking at the map or Insights. Falls back to
// alphabetical for anything not in LEGEND_ORDER, matching Legend's own
// fallback.
function legendOrderCompare(a: InsightsLineStat, b: InsightsLineStat): number {
  const indexA = LEGEND_ORDER.indexOf(lineName(a.route_id))
  const indexB = LEGEND_ORDER.indexOf(lineName(b.route_id))
  if (indexA === -1 && indexB === -1) return lineName(a.route_id).localeCompare(lineName(b.route_id))
  if (indexA === -1) return 1
  if (indexB === -1) return -1
  return indexA - indexB
}

export function InsightsPage() {
  const [range, setRange] = useState<InsightsRangeName>('today')
  const { data, loading, error } = useInsights({ range })

  const perLineSorted = useMemo(() => (data ? [...data.line_stats].sort(legendOrderCompare) : []), [data])
  const busiestSorted = useMemo(
    () =>
      data
        ? [...data.line_stats].sort((a, b) => ranThisRange(b) - ranThisRange(a) || legendOrderCompare(a, b))
        : [],
    [data],
  )
  const maxLineVolume = Math.max(1, ...perLineSorted.map(ranThisRange))

  const networkOnTime = useMemo(() => {
    if (!data) return null
    const onTime = data.line_stats.reduce((sum, l) => sum + l.on_time_count, 0)
    const late = data.line_stats.reduce((sum, l) => sum + l.late_count, 0)
    const cancelled = data.line_stats.reduce((sum, l) => sum + l.cancelled_count, 0)
    const gap = data.line_stats.reduce((sum, l) => sum + l.gap_count, 0)
    const ran = onTime + late
    return {
      onTimePct: ran > 0 ? (onTime / ran) * 100 : 0,
      total: onTime + late + cancelled + gap,
      cancelled,
      gap,
    }
  }, [data])

  // Chart 2: cancellations/delays over time -- one point per day in
  // requested_dates (the FULL selected range), not just whichever dates
  // happen to have a rollup in daily_line_stats. A day with no rollup
  // yet renders as an explicit zero/gap bar, same honesty pattern as
  // the per-line zero rows -- it should never just silently vanish from
  // the x-axis and compress "Last 7 days" down to however few days
  // actually have data.
  const dailySeries = useMemo(() => {
    if (!data) return []
    return data.requested_dates.map((day) => {
      const lines = data.daily_line_stats[day] ?? []
      const onTime = lines.reduce((s, l) => s + l.on_time_count, 0)
      const late = lines.reduce((s, l) => s + l.late_count, 0)
      const cancelled = lines.reduce((s, l) => s + l.cancelled_count, 0)
      const gap = lines.reduce((s, l) => s + l.gap_count, 0)
      return { day, onTime, late, cancelled, gap, hasData: lines.length > 0 }
    })
  }, [data])

  // Chart 5: weekday vs weekend -- split the same per-day series by day type.
  const weekdayVsWeekend = useMemo(() => {
    const buckets = { weekday: { onTime: 0, ran: 0 }, weekend: { onTime: 0, ran: 0 } }
    for (const day of dailySeries) {
      const dow = new Date(`${day.day}T00:00:00`).getDay() // 0=Sun..6=Sat
      const bucket = dow === 0 || dow === 6 ? buckets.weekend : buckets.weekday
      bucket.onTime += day.onTime
      bucket.ran += day.onTime + day.late
    }
    return buckets
  }, [dailySeries])

  // "Today" means the server's Melbourne-service-date "today"
  // (service_date_for_instant's 3am boundary), not a client-computed
  // UTC date -- resolve_range guarantees a range never extends past
  // "today", so for every range except "yesterday" the LAST (most
  // recent) entry in days_covered is today's date, if it's covered yet.
  const todayIsoDate = range !== 'yesterday' && data && data.days_covered.length > 0
    ? data.days_covered[data.days_covered.length - 1]
    : null
  const todayGeneratedAt = todayIsoDate ? data?.generated_at_by_date[todayIsoDate] : undefined
  const isTodayIncluded = todayGeneratedAt !== undefined

  const networkHourly = useMemo(() => {
    if (!data) return []
    const byHour = new Map<number, number>()
    for (const h of data.hourly_stats) {
      if (h.route_id !== null) continue // route_id=null rows are already the network-wide sum
      byHour.set(h.hour_local, h.completion_count)
    }
    return Array.from(byHour.entries()).sort((a, b) => a[0] - b[0])
  }, [data])
  const maxHourly = Math.max(1, ...networkHourly.map(([, count]) => count))

  return (
    <div className={styles.page}>
      <header className={styles.topbar}>
        <div>
          <Link to="/" className={styles.backLink}>
            ← Back to live map
          </Link>
          <h1 className={styles.title}>Insights</h1>
          <p className={styles.subline}>
            Punctuality and completion trends across the metro network.{' '}
            {data && data.expected_days > data.days_covered.length && (
              <span className={styles.partialNote}>
                ({data.days_covered.length} of {data.expected_days} days available for this period)
              </span>
            )}
          </p>
        </div>
        {isTodayIncluded && todayGeneratedAt && (
          <div className={styles.stalenessBadge} title={`Refreshed periodically, not live -- last updated ${new Date(todayGeneratedAt).toLocaleString()}`}>
            <span className={styles.stalenessDot} />
            Today's data fresh as of {relativeTime(todayGeneratedAt)}
          </div>
        )}
      </header>

      <nav className={styles.filterBar} aria-label="Date range">
        {RANGE_OPTIONS.map((option) => (
          <button
            key={option.name}
            type="button"
            className={styles.filterChip}
            aria-pressed={range === option.name}
            onClick={() => setRange(option.name)}
          >
            {option.label}
          </button>
        ))}
      </nav>

      {loading && <p className={styles.status}>Loading…</p>}
      {error && <p className={styles.status}>Couldn't load Insights data. Try again shortly.</p>}

      {data && !loading && !error && (
        <>
          <section className={styles.kpiRow} aria-label="Network summary">
            <div className={styles.kpi}>
              <span className={styles.kpiLabel}>Network on-time</span>
              <span className={styles.kpiValue}>{networkOnTime ? networkOnTime.onTimePct.toFixed(1) : '0.0'}%</span>
            </div>
            <div className={styles.kpi}>
              <span className={styles.kpiLabel}>Trips completed</span>
              <span className={styles.kpiValue}>{networkOnTime?.total.toLocaleString() ?? 0}</span>
            </div>
            <div className={styles.kpi}>
              <span className={styles.kpiLabel}>Cancelled</span>
              <span className={styles.kpiValue} style={{ color: 'var(--color-danger)' }}>
                {networkOnTime?.cancelled ?? 0}
              </span>
            </div>
            <div className={styles.kpi}>
              <span className={styles.kpiLabel}>Undetermined gap</span>
              <span className={styles.kpiValue} style={{ color: 'var(--color-text-dim)' }}>
                {networkOnTime?.gap ?? 0}
              </span>
            </div>
          </section>

          <section className={styles.chartGrid}>
            <article className={styles.card}>
              <h2 className={styles.cardTitle}>Trips completed per line</h2>
              <div className={styles.barList}>
                {perLineSorted.map((line) => (
                  <div className={styles.barRow} key={line.route_id}>
                    <span className={styles.barName}>{lineName(line.route_id)}</span>
                    <span className={styles.barTrack}>
                      <span
                        className={styles.barSeg}
                        style={{
                          width: `${(ranThisRange(line) / maxLineVolume) * 100}%`,
                          background: lineColor(line.route_id),
                        }}
                      />
                    </span>
                    <span className={styles.barValue}>{ranThisRange(line)}</span>
                  </div>
                ))}
                {perLineSorted.length === 0 && <p className={styles.status}>No data yet for this range.</p>}
              </div>
            </article>

            <article className={styles.card}>
              <h2 className={styles.cardTitle}>Busiest lines by volume</h2>
              {networkOnTime && networkOnTime.total > 0 && networkOnTime.total < SAMPLE_SIZE_GUARD * busiestSorted.length && (
                <p className={styles.guard}>
                  ⚠ Below the ≥{SAMPLE_SIZE_GUARD} trips/week reliability guard — ranking will stabilise as history
                  deepens.
                </p>
              )}
              <div className={styles.barList}>
                {busiestSorted.map((line) => (
                  <div className={styles.barRow} key={line.route_id}>
                    <span className={styles.barName}>{lineName(line.route_id)}</span>
                    <span className={styles.barTrack}>
                      <span
                        className={styles.barSeg}
                        style={{
                          width: `${(ranThisRange(line) / maxLineVolume) * 100}%`,
                          background: lineColor(line.route_id),
                        }}
                      />
                    </span>
                    <span className={styles.barValue}>{ranThisRange(line)}</span>
                  </div>
                ))}
              </div>
            </article>

            <article className={`${styles.card} ${styles.cardWide}`}>
              <h2 className={styles.cardTitle}>Cancellations &amp; delays over time</h2>
              <p className={styles.cardCaption}>
                Daily status breakdown, network-wide. Undetermined gap is always its own segment.
              </p>
              {dailySeries.length === 1 && (
                <p className={styles.guard}>
                  ⚠ A single day can't show a trend — select Last 7 days or Last 30 days to see one.
                </p>
              )}
              <div className={styles.dailyChart}>
                {dailySeries.map((day) => {
                  const total = day.onTime + day.late + day.cancelled + day.gap || 1
                  return (
                    <div className={styles.dailyBar} key={day.day} title={day.hasData ? day.day : `${day.day} — no data yet`}>
                      {day.hasData ? (
                        <div className={styles.dailyStack}>
                          <span
                            className={styles.dailySeg}
                            style={{ height: `${(day.onTime / total) * 100}%`, background: 'var(--color-success)' }}
                          />
                          <span
                            className={styles.dailySeg}
                            style={{ height: `${(day.late / total) * 100}%`, background: 'var(--color-warning)' }}
                          />
                          <span
                            className={styles.dailySeg}
                            style={{ height: `${(day.cancelled / total) * 100}%`, background: 'var(--color-danger)' }}
                          />
                          <span
                            className={styles.dailySeg}
                            style={{ height: `${(day.gap / total) * 100}%`, background: 'var(--color-text-dim)' }}
                          />
                        </div>
                      ) : (
                        <div className={`${styles.dailyStack} ${styles.dailyStackEmpty}`} />
                      )}
                      <span className={styles.dailyLabel}>{day.day.slice(5)}</span>
                    </div>
                  )
                })}
              </div>
            </article>

            <article className={styles.card}>
              <h2 className={styles.cardTitle}>Completions by hour of day</h2>
              <p className={styles.cardCaption}>
                Terminus-arrival times, Melbourne local time — an arrival proxy, not a departure-frequency count.
              </p>
              <div className={styles.hourlyChart}>
                {networkHourly.map(([hour, count]) => (
                  <div className={styles.hourlyBar} key={hour} title={`${hour}:00`}>
                    <span
                      className={styles.hourlyFill}
                      style={{ height: `${(count / maxHourly) * 100}%` }}
                    />
                  </div>
                ))}
                {networkHourly.length === 0 && <p className={styles.status}>No data yet for this range.</p>}
              </div>
            </article>

            <article className={styles.card}>
              <h2 className={styles.cardTitle}>Weekday vs. weekend</h2>
              <div className={styles.barList}>
                {(['weekday', 'weekend'] as const).map((key) => {
                  const bucket = weekdayVsWeekend[key]
                  const hasData = bucket.ran > 0
                  const pct = hasData ? (bucket.onTime / bucket.ran) * 100 : 0
                  return (
                    <div className={styles.barRow} key={key}>
                      <span className={styles.barName}>{key === 'weekday' ? 'Weekday' : 'Weekend'}</span>
                      <span className={styles.barTrack}>
                        <span className={styles.barSeg} style={{ width: `${pct}%`, background: 'var(--color-accent)' }} />
                      </span>
                      {/* "No data" for an empty bucket, not "0%" -- an empty
                          weekend bucket hasn't been measured at 0%, it just
                          isn't in the selected range yet. */}
                      <span className={styles.barValue}>{hasData ? `${pct.toFixed(0)}%` : 'No data'}</span>
                    </div>
                  )
                })}
              </div>
              {dailySeries.filter((d) => d.hasData).length < 2 && (
                <p className={styles.guard}>⚠ Only a few days observed so far — read as directional.</p>
              )}
            </article>
          </section>
        </>
      )}
    </div>
  )
}
