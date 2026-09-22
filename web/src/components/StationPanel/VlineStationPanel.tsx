import { vlineRoutesById, vlineStationsById } from '../../vlineGeometry'
import { lineNameForTrain, markerColor, STATUS_LABEL } from '../../map/trainMarkers'
import { relativeTime } from '../../lib/relativeTime'
import { formatTime } from '../../lib/formatTime'
import { Section, Placeholder } from '../Section'
import { trackEvent } from '../../lib/analytics'
import {
  firstHeadwayInGroup,
  groupByDirection,
  NEARBY_RADIUS_M,
  nearbyTrains,
  scheduleBadge,
} from './scheduleView'
import type { Train } from '../../api-types'
import type { StationScheduleState } from '../../hooks/useStationSchedule'
import styles from './StationPanel.module.css'

interface VlineStationPanelProps {
  stationId: string | null
  trains: Map<string, Train>
  hideGhosts: boolean
  onClear: () => void
  schedule: StationScheduleState
}

/** V/Line's station click panel -- reuses Metro's `StationPanel.module.css`
 * and, via `scheduleView.ts`, the exact same "Next trains" + "nearby
 * trains" rendering logic as `StationPanel.tsx` (M15). Structurally the
 * same component with V/Line's own geometry/schedule wired in, not a
 * reimplementation -- the only real difference is the placeholder text
 * (no station search on this page). */
export function VlineStationPanel({ stationId, trains, hideGhosts, onClear, schedule }: VlineStationPanelProps) {
  const station = stationId ? vlineStationsById.get(stationId) : undefined
  const nearby = station ? nearbyTrains(station.id, trains, hideGhosts, vlineStationsById) : []
  const departures = schedule.data?.departures ?? []
  const linesNoServiceToday = schedule.data?.lines_no_service_today ?? []
  const wheelchairBoarding = schedule.data?.wheelchair_boarding ?? null

  return (
    <Section title="Station">
      {!station && <Placeholder>Click a station on the map</Placeholder>}
      {station && (
        <>
          <div className={styles.header}>
            <span className={styles.name}>{station.name}</span>
            <button
              type="button"
              className={styles.close}
              onClick={() => {
                trackEvent('click-vline-clear-station')
                onClear()
              }}
              aria-label="Clear selected station"
            >
              ×
            </button>
          </div>
          {wheelchairBoarding === 1 && <p className={styles.caption}>Wheelchair accessible</p>}
          {wheelchairBoarding === 2 && <p className={styles.caption}>Not wheelchair accessible</p>}

          <h3 className={styles.subheading}>Next trains</h3>
          {!schedule.loading && linesNoServiceToday.length > 0 && (
            <p className={styles.caption}>
              No service today on{' '}
              {linesNoServiceToday.map((line) => line.long_name || line.short_name).join(', ')}.
            </p>
          )}
          {schedule.loading && <p className={styles.empty}>Loading schedule…</p>}
          {!schedule.loading && schedule.error && (
            <p className={styles.empty}>Schedule unavailable right now.</p>
          )}
          {!schedule.loading && !schedule.error && departures.length === 0 && (
            <p className={styles.empty}>No more services today.</p>
          )}
          {!schedule.loading &&
            departures.length > 0 &&
            groupByDirection(departures).map((group) => {
              const headway = firstHeadwayInGroup(group.departures)
              return (
              <div key={group.label} className={styles.directionGroup}>
                <h4 className={styles.directionLabel}>{group.label}</h4>
                {headway && (
                  <p className={headway.gap ? styles.headwayGapCaption : styles.headwayCaption}>
                    {headway.text}
                  </p>
                )}
                <ul className={styles.scheduleList}>
                  {group.departures.map((dep) => {
                    const badge = scheduleBadge(dep)
                    return (
                      <li key={`${dep.trip_id}-${dep.scheduled_time}`} className={styles.scheduleRow}>
                        <span className={styles.scheduleHeadsign}>
                          {dep.headsign}
                          {dep.platform_code && (
                            <span className={styles.platformCode}> · Plat {dep.platform_code}</span>
                          )}
                        </span>
                        <span
                          className={
                            dep.is_cancelled
                              ? `${styles.scheduleTime} ${styles.scheduleTimeCancelled}`
                              : styles.scheduleTime
                          }
                        >
                          {formatTime(dep.is_live && dep.predicted_time ? dep.predicted_time : dep.scheduled_time)}
                        </span>
                        <span className={badge.className}>{badge.label}</span>
                      </li>
                    )
                  })}
                </ul>
              </div>
              )
            })}

          <p className={styles.caption}>Trains within {NEARBY_RADIUS_M}m, by live position — not a schedule.</p>
          {nearby.length === 0 && <p className={styles.empty}>No trains currently near this station.</p>}
          {nearby.length > 0 && (
            <ul className={styles.trainList}>
              {nearby.map(({ train, distanceM }) => (
                <li key={train.trip_id} className={styles.trainRow}>
                  <span className={styles.swatch} style={{ backgroundColor: markerColor(train, vlineRoutesById) }} />
                  <span className={styles.trainLine}>{lineNameForTrain(train, vlineRoutesById)}</span>
                  <span className={styles.trainMeta}>
                    {STATUS_LABEL[train.status]} · {relativeTime(train.last_seen_at)} · {Math.round(distanceM)}m
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Section>
  )
}
