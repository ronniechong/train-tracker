import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
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

const GRID_STROKE = 'var(--color-border)'
const AXIS_TICK = { fill: 'var(--color-text-dim)', fontSize: 12 }
const TOOLTIP_STYLE = {
  background: 'var(--color-panel-bg)',
  border: '1px solid var(--color-border)',
  borderRadius: 8,
  fontSize: 12,
  color: 'var(--color-text)',
}

function lineName(routeId: string): string {
  return routesById.get(routeId)?.name ?? routeId
}

function lineColor(routeId: string): string {
  return routesById.get(routeId)?.color ?? 'var(--color-accent)'
}

function ranThisRange(line: InsightsLineStat): number {
  return line.on_time_count + line.late_count
}

// 24-hour local hour -> "12am"/"1am".../"12pm"/"1pm"..., not a bare "0"-"23"
// -- those read ambiguously (is "8" 8am or 8pm?) at a glance on the chart axis.
function formatHour12(hour: number): string {
  const period = hour < 12 ? 'am' : 'pm'
  const hour12 = hour % 12 === 0 ? 12 : hour % 12
  return `${hour12}${period}`
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

  const perLineChartData = useMemo(
    () => perLineSorted.map((l) => ({ name: lineName(l.route_id), value: ranThisRange(l), color: lineColor(l.route_id) })),
    [perLineSorted],
  )
  const busiestChartData = useMemo(
    () => busiestSorted.map((l) => ({ name: lineName(l.route_id), value: ranThisRange(l), color: lineColor(l.route_id) })),
    [busiestSorted],
  )

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
      return { day: day.slice(5), fullDate: day, onTime, late, cancelled, gap, hasData: lines.length > 0 }
    })
  }, [data])

  // Chart 5: weekday vs weekend -- split the same per-day series by day type.
  const weekdayVsWeekend = useMemo(() => {
    const buckets = { weekday: { onTime: 0, ran: 0 }, weekend: { onTime: 0, ran: 0 } }
    for (const day of dailySeries) {
      const dow = new Date(`${day.fullDate}T00:00:00`).getDay() // 0=Sun..6=Sat
      const bucket = dow === 0 || dow === 6 ? buckets.weekend : buckets.weekday
      bucket.onTime += day.onTime
      bucket.ran += day.onTime + day.late
    }
    return buckets
  }, [dailySeries])

  const weekdayVsWeekendChartData = useMemo(
    () =>
      (['weekday', 'weekend'] as const).map((key) => {
        const bucket = weekdayVsWeekend[key]
        const hasData = bucket.ran > 0
        return {
          name: key === 'weekday' ? 'Weekday' : 'Weekend',
          pct: hasData ? (bucket.onTime / bucket.ran) * 100 : 0,
          onTime: bucket.onTime,
          ran: bucket.ran,
          hasData,
        }
      }),
    [weekdayVsWeekend],
  )

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

  const histogramRows = useMemo(() => {
    if (!data) return []
    const h = data.histogram_stats
    return [
      { label: 'On time', value: h.on_time_count, color: 'var(--color-success)' },
      { label: 'Late 5–10 min', value: h.late_5_10_count, color: 'var(--color-warning)' },
      { label: 'Late 10+ min', value: h.late_10_plus_count, color: 'var(--color-danger)' },
      { label: 'Cancelled', value: h.cancelled_count, color: 'var(--color-danger)' },
      { label: 'Undetermined gap', value: h.gap_count, color: 'var(--color-text-dim)' },
    ]
  }, [data])

  // All 24 hours always shown, even ones with zero completions so far
  // (e.g. hours not yet reached in "Today") -- an hour with no data
  // isn't the same as an hour that hasn't happened yet, but both should
  // render as an explicit 0 bar rather than silently missing from the
  // x-axis, same honesty pattern used for zero-completion lines elsewhere
  // on this page.
  const networkHourly = useMemo(() => {
    const byHour = new Map<number, number>()
    if (data) {
      for (const h of data.hourly_stats) {
        if (h.route_id !== null) continue // route_id=null rows are already the network-wide sum
        byHour.set(h.hour_local, h.completion_count)
      }
    }
    return Array.from({ length: 24 }, (_, hour) => ({ hour: formatHour12(hour), count: byHour.get(hour) ?? 0 }))
  }, [data])

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

      {loading && (
        <div className={styles.loading} role="status" aria-live="polite">
          <svg className={styles.spinner} viewBox="0 0 50 50" width="32" height="32" aria-hidden="true">
            <circle className={styles.spinnerTrack} cx="25" cy="25" r="20" fill="none" strokeWidth="4" />
            <circle className={styles.spinnerArc} cx="25" cy="25" r="20" fill="none" strokeWidth="4" />
          </svg>
          <span>Loading Insights…</span>
        </div>
      )}
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
              {perLineChartData.length === 0 ? (
                <p className={styles.status}>No data yet for this range.</p>
              ) : (
                <ResponsiveContainer width="100%" height={Math.max(180, perLineChartData.length * 28)}>
                  <BarChart data={perLineChartData} layout="vertical" margin={{ left: 8, right: 16 }}>
                    <CartesianGrid stroke={GRID_STROKE} horizontal={false} />
                    <XAxis type="number" allowDecimals={false} tick={AXIS_TICK} />
                    <YAxis type="category" dataKey="name" width={100} tick={AXIS_TICK} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [v as number, 'Trips completed']} />
                    <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                      {perLineChartData.map((entry) => (
                        <Cell key={entry.name} fill={entry.color} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </article>

            <article className={styles.card}>
              <h2 className={styles.cardTitle}>Busiest lines by volume</h2>
              {networkOnTime && networkOnTime.total > 0 && networkOnTime.total < SAMPLE_SIZE_GUARD * busiestSorted.length && (
                <p className={styles.guard}>
                  ⚠ Below the ≥{SAMPLE_SIZE_GUARD} trips/week reliability guard — ranking will stabilise as history
                  deepens.
                </p>
              )}
              {busiestChartData.length === 0 ? (
                <p className={styles.status}>No data yet for this range.</p>
              ) : (
                <ResponsiveContainer width="100%" height={Math.max(180, busiestChartData.length * 28)}>
                  <BarChart data={busiestChartData} layout="vertical" margin={{ left: 8, right: 16 }}>
                    <CartesianGrid stroke={GRID_STROKE} horizontal={false} />
                    <XAxis type="number" allowDecimals={false} tick={AXIS_TICK} />
                    <YAxis type="category" dataKey="name" width={100} tick={AXIS_TICK} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [v as number, 'Trips completed']} />
                    <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                      {busiestChartData.map((entry) => (
                        <Cell key={entry.name} fill={entry.color} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </article>

            <article className={`${styles.card} ${styles.cardWide}`}>
              <h2 className={styles.cardTitle}>Cancellations &amp; delays over time</h2>
              <p className={styles.cardCaption}>
                Daily completed-trip counts, network-wide, split by outcome. Undetermined gap is always its own
                segment.
              </p>
              {dailySeries.length === 1 && (
                <p className={styles.guard}>
                  ⚠ A single day can't show a trend — select Last 7 days or Last 30 days to see one.
                </p>
              )}
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={dailySeries} margin={{ left: 0, right: 16 }}>
                  <CartesianGrid stroke={GRID_STROKE} vertical={false} />
                  <XAxis dataKey="day" tick={AXIS_TICK} />
                  <YAxis allowDecimals={false} tick={AXIS_TICK} width={40} />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    labelFormatter={(_label, payload) => payload?.[0]?.payload?.fullDate ?? _label}
                  />
                  <Legend wrapperStyle={{ fontSize: 12, color: 'var(--color-text-dim)' }} />
                  <Bar dataKey="onTime" name="On time" stackId="status" fill="var(--color-success)" />
                  <Bar dataKey="late" name="Late" stackId="status" fill="var(--color-warning)" />
                  <Bar dataKey="cancelled" name="Cancelled" stackId="status" fill="var(--color-danger)" />
                  <Bar dataKey="gap" name="Undetermined gap" stackId="status" fill="var(--color-text-dim)" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </article>

            <article className={styles.card}>
              <h2 className={styles.cardTitle}>On-time performance</h2>
              <p className={styles.cardCaption}>
                Delay margin, network-wide, for the selected range. Cancellations shown, never scored as a
                punctuality miss.
              </p>
              <ResponsiveContainer width="100%" height={Math.max(180, histogramRows.length * 32)}>
                <BarChart data={histogramRows} layout="vertical" margin={{ left: 8, right: 16 }}>
                  <CartesianGrid stroke={GRID_STROKE} horizontal={false} />
                  <XAxis type="number" allowDecimals={false} tick={AXIS_TICK} />
                  <YAxis type="category" dataKey="label" width={110} tick={AXIS_TICK} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [v as number, 'Trips']} />
                  <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                    {histogramRows.map((entry) => (
                      <Cell key={entry.label} fill={entry.color} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </article>

            <article className={styles.card}>
              <h2 className={styles.cardTitle}>Completions by hour of day</h2>
              <p className={styles.cardCaption}>
                Terminus-arrival times, Melbourne local time — an arrival proxy, not a departure-frequency count.
              </p>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={networkHourly} margin={{ left: 0, right: 16 }}>
                  <CartesianGrid stroke={GRID_STROKE} vertical={false} />
                  <XAxis dataKey="hour" interval={0} tick={{ ...AXIS_TICK, fontSize: 10 }} angle={-45} textAnchor="end" height={40} />
                  <YAxis allowDecimals={false} tick={AXIS_TICK} width={32} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [v as number, 'Completions']} />
                  <Bar dataKey="count" name="Completions" fill="var(--color-accent)" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </article>

            <article className={styles.card}>
              <h2 className={styles.cardTitle}>Weekday vs. weekend</h2>
              <p className={styles.cardCaption}>
                On-time percentage of completed trips, network-wide, for the selected range.
              </p>
              <ResponsiveContainer width="100%" height={140}>
                <BarChart data={weekdayVsWeekendChartData} layout="vertical" margin={{ left: 8, right: 16 }}>
                  <CartesianGrid stroke={GRID_STROKE} horizontal={false} />
                  <XAxis type="number" domain={[0, 100]} unit="%" tick={AXIS_TICK} />
                  <YAxis type="category" dataKey="name" width={80} tick={AXIS_TICK} />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(_v, _n, item) => {
                      const p = item.payload as (typeof weekdayVsWeekendChartData)[number]
                      return [p.hasData ? `${p.pct.toFixed(0)}% (${p.onTime}/${p.ran} trips)` : 'No data', 'On time']
                    }}
                  />
                  <Bar dataKey="pct" fill="var(--color-accent)" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
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
