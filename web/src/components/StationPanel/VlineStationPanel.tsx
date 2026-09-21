import { vlineRoutesById, vlineStationsById } from '../../vlineGeometry'
import { lineNameForTrain, markerColor, STATUS_LABEL } from '../../map/trainMarkers'
import { relativeTime } from '../../lib/relativeTime'
import { haversineM } from '../../lib/geo'
import { Section, Placeholder } from '../Section'
import { trackEvent } from '../../lib/analytics'
import type { Train } from '../../api-types'
import styles from './StationPanel.module.css'

// Same radius/rationale as StationPanel.tsx's own NEARBY_RADIUS_M -- no
// V/Line-specific tuning needed, the stations are just farther apart.
const NEARBY_RADIUS_M = 500

interface NearbyTrain {
  train: Train
  distanceM: number
}

function nearbyTrains(stationId: string, trains: Map<string, Train>, hideGhosts: boolean): NearbyTrain[] {
  const station = vlineStationsById.get(stationId)
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

interface VlineStationPanelProps {
  stationId: string | null
  trains: Map<string, Train>
  hideGhosts: boolean
  onClear: () => void
}

/** V/Line's station click panel -- reuses Metro's `StationPanel.module.css`
 * for the same look, but only the "nearby trains" half: computed entirely
 * client-side from live positions, no API call. Metro's "Next trains"
 * section reads from a per-station schedule endpoint V/Line's backend
 * doesn't have (see the Phase C design brief's out-of-scope list), so it's
 * left out here rather than faked. */
export function VlineStationPanel({ stationId, trains, hideGhosts, onClear }: VlineStationPanelProps) {
  const station = stationId ? vlineStationsById.get(stationId) : undefined
  const nearby = station ? nearbyTrains(station.id, trains, hideGhosts) : []

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
          <p className={styles.caption}>Trains within {NEARBY_RADIUS_M}m, by live position -- not a schedule.</p>
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
