import { routesById, stationsById } from '../../geometry'
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

interface StationPanelProps {
  stationId: string | null
  trains: Map<string, Train>
  hideGhosts: boolean
  onClear: () => void
  schedule: StationScheduleState
}

/** Station click / search select → persistent sidebar panel (M4 Stage 4
 * remainder, 2026-07-31). "Nearby trains" is computed client-side (geofence
 * match against live train positions, no API call). "Next trains" (added
 * for the station-schedule feature) is the opposite: server-computed from
 * the static timetable + a live Trip Updates overlay where available --
 * fetched via `schedule` (lifted to App.tsx so the on-map popup can show
 * the same data). */
export function StationPanel({ stationId, trains, hideGhosts, onClear, schedule }: StationPanelProps) {
  const station = stationId ? stationsById.get(stationId) : undefined
  const nearby = station ? nearbyTrains(station.id, trains, hideGhosts, stationsById) : []
  const departures = schedule.data?.departures ?? []
  const linesNoServiceToday = schedule.data?.lines_no_service_today ?? []
  const wheelchairBoarding = schedule.data?.wheelchair_boarding ?? null

  return (
    <Section title="Station">
      {!station && <Placeholder>Click a station on the map, or search above</Placeholder>}
      {station && (
        <>
          <div className={styles.header}>
            <span className={styles.name}>{station.name}</span>
            <button
              type="button"
              className={styles.close}
              onClick={() => {
                trackEvent('click-clear-station')
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
                  <span className={styles.swatch} style={{ backgroundColor: markerColor(train, routesById) }} />
                  <span className={styles.trainLine}>{lineNameForTrain(train, routesById)}</span>
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
