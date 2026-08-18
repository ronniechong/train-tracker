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

interface HeadwayCaption {
  text: string
  gap: boolean
}

// M12 #4: this platform's own (route, direction) rolling headway --
// `average_headway_seconds` is only set once the buffer has 2+ arrivals,
// same null-means-no-data convention the rest of this panel already uses
// (platform_code, wheelchair_boarding): insufficient sample renders
// nothing at all, not a placeholder.
function headwayCaption(dep: ScheduledTrain, labelLine: boolean): HeadwayCaption | null {
  if (dep.average_headway_seconds === null) return null
  const minutes = Math.round(dep.average_headway_seconds / 60)
  const prefix = labelLine ? `${dep.headsign}: ` : ''
  if (!dep.gap_detected) return { text: `${prefix}~${minutes} min apart`, gap: false }
  const waitMinutes =
    dep.seconds_since_last_arrival !== null ? Math.round(dep.seconds_since_last_arrival / 60) : null
  const text =
    waitMinutes !== null
      ? `${prefix}Gap — last seen ${waitMinutes} min ago (usually ~${minutes} min)`
      : `${prefix}Gap — usually ~${minutes} min apart`
  return { text, gap: true }
}

// A direction group can span several routes (e.g. "Outbound" at Flinders
// Street covers every city-bound line at once), each with its own
// independent headway -- so scanning for the first departure that HAS
// data, not just the group's soonest departure, surfaces something to
// look at far more often. `labelLine` disambiguates which line the
// number belongs to whenever it isn't the group's own next departure.
function firstHeadwayInGroup(departures: ScheduledTrain[]): HeadwayCaption | null {
  for (const dep of departures) {
    const caption = headwayCaption(dep, dep !== departures[0])
    if (caption) return caption
  }
  return null
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
