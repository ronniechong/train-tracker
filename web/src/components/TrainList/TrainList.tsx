import { isTrainFilteredOut, lineNameForTrain, STATUS_LABEL, trainIdentityLabel } from '../../map/trainMarkers'
import { vlineRoutesById } from '../../vlineGeometry'
import { Section, Placeholder } from '../Section'
import { cx } from '../../lib/cx'
import { trackEvent } from '../../lib/analytics'
import type { Train } from '../../api-types'
import styles from './TrainList.module.css'

interface TrainListProps {
  trains: Map<string, Train>
  hiddenRouteIds: ReadonlySet<string>
  hideGhosts: boolean
  highlightedTripId: string | null
  onSelectTrain: (tripId: string) => void
}

/** V/Line-only addition to the reused Metro sidebar shell (see the Phase C
 * design decisions doc) -- closes a real discoverability gap Metro's map
 * doesn't have: on a much larger, pannable regional map, a train can be
 * entirely off-screen with nothing else on screen indicating it exists.
 * Plain bordered rows, no card/carousel treatment, same filtering
 * (`isTrainFilteredOut`) as the map's own markers so the list and the map
 * never disagree about which trains are currently shown. */
export function TrainList({ trains, hiddenRouteIds, hideGhosts, highlightedTripId, onSelectTrain }: TrainListProps) {
  const visibleTrains = [...trains.values()].filter((train) => !isTrainFilteredOut(train, hiddenRouteIds, hideGhosts))

  return (
    <Section title="Trains">
      {visibleTrains.length === 0 ? (
        <Placeholder>No V/Line trains currently active.</Placeholder>
      ) : (
        <ul className={styles.list}>
          {visibleTrains.map((train) => {
            const identity = trainIdentityLabel(train)
            return (
              <li key={train.trip_id}>
                <button
                  type="button"
                  className={cx(styles.row, train.trip_id === highlightedTripId && styles.highlighted)}
                  onClick={() => {
                    trackEvent('click-vline-train-list-row')
                    onSelectTrain(train.trip_id)
                  }}
                >
                  <span className={styles.rowMain}>
                    <span className={styles.lineName}>{lineNameForTrain(train, vlineRoutesById)}</span>
                    {identity && <span className={styles.identity}>{identity}</span>}
                  </span>
                  <span className={cx(styles.statusLabel, styles[`status-${train.status}`])}>
                    {STATUS_LABEL[train.status]}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </Section>
  )
}
