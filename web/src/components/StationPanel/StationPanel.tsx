import { stationsById } from '../../geometry'
import { lineNameForTrain, markerColor, STATUS_LABEL } from '../../map/trainMarkers'
import { relativeTime } from '../../lib/relativeTime'
import { formatTime } from '../../lib/formatTime'
import { haversineM } from '../../lib/geo'
import { Section, Placeholder } from '../Section'
import { trackEvent } from '../../lib/analytics'
import type { Train, ScheduledTrain } from '../../api-types'
import type { StationScheduleState } from '../../hooks/useStationSchedule'
import styles from './StationPanel.module.css'

// Below this, a live prediction reads as "on time" rather than delayed --
// GTFS delay can be mildly negative (running early) too, so this is an
// absolute-value band, not just a lower bound.
const ON_TIME_BAND_S = 60

interface ScheduleBadge {
  label: string
  className: string
}

function scheduleBadge(dep: ScheduledTrain): ScheduleBadge {
  if (dep.is_cancelled) return { label: 'Cancelled', className: styles.cancelledBadge }
  if (dep.is_added) return { label: 'Extra service', className: styles.addedBadge }
  if (!dep.is_live) return { label: 'Scheduled', className: styles.scheduledBadge }
  if (dep.delay_seconds === null || Math.abs(dep.delay_seconds) <= ON_TIME_BAND_S) {
    return { label: 'On time', className: styles.onTimeBadge }
  }
  if (dep.delay_seconds > 0) {
    return { label: `+${Math.round(dep.delay_seconds / 60)} min`, className: styles.delayBadge }
  }
  return { label: `${Math.round(dep.delay_seconds / 60)} min early`, className: styles.delayBadge }
}

interface DirectionGroup {
  label: string
  departures: ScheduledTrain[]
}

// direction_id is a clean, reliable inbound/outbound signal here (M1
// spike, confirmed across all 15 routes): 1 = city-bound, 0 = outbound to
// terminus. `other` only ever catches the rare trip with no direction_id
// at all -- kept as its own group rather than silently dropped or merged.
function groupByDirection(departures: ScheduledTrain[]): DirectionGroup[] {
  const inbound = departures.filter((d) => d.direction_id === 1)
  const outbound = departures.filter((d) => d.direction_id === 0)
  const other = departures.filter((d) => d.direction_id !== 0 && d.direction_id !== 1)
  return [
    { label: 'Inbound', departures: inbound },
    { label: 'Outbound', departures: outbound },
    { label: 'Other', departures: other },
  ].filter((group) => group.departures.length > 0)
}

// The backend's own geofence check (service/state/station.py's
// GEOFENCE_RADIUS_M = 100m) confirms a train is genuinely AT one specific
// stop -- tight on purpose, for a different question. This panel answers a
// looser one ("what's currently near this station, worth looking at"), so
// it uses a more generous radius; 100m would leave most stations showing
// nothing most of the time.
const NEARBY_RADIUS_M = 500

interface NearbyTrain {
  train: Train
  distanceM: number
}

function nearbyTrains(stationId: string, trains: Map<string, Train>, hideGhosts: boolean): NearbyTrain[] {
  const station = stationsById.get(stationId)
  if (!station) return []
  const nearby: NearbyTrain[] = []
  for (const train of trains.values()) {
    if (train.latitude === null || train.longitude === null) continue
    if (hideGhosts && train.status === 'ghost') continue
    const distanceM = haversineM(station.lat, station.lon, train.latitude, train.longitude)
    if (distanceM <= NEARBY_RADIUS_M) nearby.push({ train, distanceM })
  }
  return nearby.sort((a, b) => a.distanceM - b.distanceM)
}

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
  const nearby = station ? nearbyTrains(station.id, trains, hideGhosts) : []
  const departures = schedule.data?.departures ?? []

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

          <h3 className={styles.subheading}>Next trains</h3>
          {schedule.loading && <p className={styles.empty}>Loading schedule…</p>}
          {!schedule.loading && schedule.error && (
            <p className={styles.empty}>Schedule unavailable right now.</p>
          )}
          {!schedule.loading && !schedule.error && departures.length === 0 && (
            <p className={styles.empty}>No more services today.</p>
          )}
          {!schedule.loading &&
            departures.length > 0 &&
            groupByDirection(departures).map((group) => (
              <div key={group.label} className={styles.directionGroup}>
                <h4 className={styles.directionLabel}>{group.label}</h4>
                <ul className={styles.scheduleList}>
                  {group.departures.map((dep) => {
                    const badge = scheduleBadge(dep)
                    return (
                      <li key={`${dep.trip_id}-${dep.scheduled_time}`} className={styles.scheduleRow}>
                        <span className={styles.scheduleHeadsign}>{dep.headsign}</span>
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
            ))}

          <p className={styles.caption}>Trains within {NEARBY_RADIUS_M}m, by live position — not a schedule.</p>
          {nearby.length === 0 && <p className={styles.empty}>No trains currently near this station.</p>}
          {nearby.length > 0 && (
            <ul className={styles.trainList}>
              {nearby.map(({ train, distanceM }) => (
                <li key={train.trip_id} className={styles.trainRow}>
                  <span className={styles.swatch} style={{ backgroundColor: markerColor(train) }} />
                  <span className={styles.trainLine}>{lineNameForTrain(train)}</span>
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
