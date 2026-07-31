import { stationsById } from '../geometry'
import { lineNameForTrain, markerColor, STATUS_LABEL } from '../map/trainMarkers'
import { relativeTime } from '../lib/relativeTime'
import { haversineM } from '../lib/geo'
import { Section, Placeholder } from './Section'
import type { Train } from '../api-types'
import styles from './StationPanel.module.css'

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
}

/** Station click / search select → persistent sidebar panel (M4 Stage 4
 * remainder, 2026-07-31). Computed client-side (geofence match against
 * live train positions) -- no API change, this is presentation logic over
 * data already public. */
export function StationPanel({ stationId, trains, hideGhosts, onClear }: StationPanelProps) {
  const station = stationId ? stationsById.get(stationId) : undefined
  const nearby = station ? nearbyTrains(station.id, trains, hideGhosts) : []

  return (
    <Section title="Station">
      {!station && <Placeholder>Click a station on the map, or search above</Placeholder>}
      {station && (
        <>
          <div className={styles.header}>
            <span className={styles.name}>{station.name}</span>
            <button type="button" className={styles.close} onClick={onClear} aria-label="Clear selected station">
              ×
            </button>
          </div>
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
