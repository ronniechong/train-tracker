import { haversineM } from '../../lib/geo'
import type { Station } from '../../geometry'
import type { ScheduledTrain, Train } from '../../api-types'
import styles from './StationPanel.module.css'

// Shared by StationPanel.tsx (Metro) and VlineStationPanel.tsx (V/Line) --
// both render the exact same "Next trains" + "nearby trains" behaviour
// against their own geometry/schedule data, so the logic lives here once
// rather than as two copies that could quietly drift apart.

// Below this, a live prediction reads as "on time" rather than delayed --
// GTFS delay can be mildly negative (running early) too, so this is an
// absolute-value band, not just a lower bound.
const ON_TIME_BAND_S = 60

export interface ScheduleBadge {
  label: string
  className: string
}

export interface HeadwayCaption {
  text: string
  gap: boolean
}

// M12 #4: this platform's own (route, direction) rolling headway --
// `average_headway_seconds` is only set once the buffer has 2+ arrivals,
// same null-means-no-data convention the rest of this panel already uses
// (platform_code, wheelchair_boarding): insufficient sample renders
// nothing at all, not a placeholder.
export function headwayCaption(dep: ScheduledTrain, labelLine: boolean): HeadwayCaption | null {
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
export function firstHeadwayInGroup(departures: ScheduledTrain[]): HeadwayCaption | null {
  for (const dep of departures) {
    const caption = headwayCaption(dep, dep !== departures[0])
    if (caption) return caption
  }
  return null
}

export function scheduleBadge(dep: ScheduledTrain): ScheduleBadge {
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

export interface DirectionGroup {
  label: string
  departures: ScheduledTrain[]
}

// direction_id is a clean, reliable inbound/outbound signal here (M1
// spike, confirmed across all 15 Metro routes, and the same GTFS field
// for V/Line): 1 = city-bound, 0 = outbound to terminus. `other` only
// ever catches the rare trip with no direction_id at all -- kept as its
// own group rather than silently dropped or merged.
export function groupByDirection(departures: ScheduledTrain[]): DirectionGroup[] {
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
export const NEARBY_RADIUS_M = 500

export interface NearbyTrain {
  train: Train
  distanceM: number
}

export function nearbyTrains(
  stationId: string,
  trains: Map<string, Train>,
  hideGhosts: boolean,
  stationsById: ReadonlyMap<string, Station>,
): NearbyTrain[] {
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
