import * as maplibregl from 'maplibre-gl'
import './trainPopup.css'
import { relativeTime } from '../lib/relativeTime'
import type { Route } from '../geometry'
import {
  delayPredictionLabel, lineNameForTrain, markerColor, nextStopLabel, STATUS_LABEL, trainIdentityLabel,
} from './trainMarkers'
import type { Train } from '../api-types'
import type { DelayPredictionState } from '../hooks/useDelayPredictions'

const SVG_NS = 'http://www.w3.org/2000/svg'

function createSwatch(color: string): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, 'svg')
  svg.setAttribute('class', 'train-popup-swatch')
  svg.setAttribute('viewBox', '0 0 10 10')
  svg.setAttribute('width', '10')
  svg.setAttribute('height', '10')
  const rect = document.createElementNS(SVG_NS, 'rect')
  rect.setAttribute('x', '0.75')
  rect.setAttribute('y', '0.75')
  rect.setAttribute('width', '8.5')
  rect.setAttribute('height', '8.5')
  rect.setAttribute('rx', '1.5')
  rect.setAttribute('fill', color)
  rect.setAttribute('stroke', '#ffffff')
  rect.setAttribute('stroke-width', '1')
  svg.append(rect)
  return svg
}

/** Click-triggered popup content: same underlying data as the hover
 * tooltip (trainMarkers.ts), plus the Track/Untrack + "Am I late?" actions
 * when the caller provides handlers for them -- deliberately a separate
 * element tree from the hover tooltip rather than reusing it, since this
 * one needs to stay open (click, not `:hover`) and can carry interactive
 * buttons, which the pure-CSS hover tooltip never needs to. `routesById`
 * is injected (Metro's or V/Line's) rather than imported at module scope,
 * same reuse-for-either-mode reason as trainMarkers.ts. V/Line's call site
 * omits `onToggleTrack`/`onRequestDelayPrediction` entirely -- tracking and
 * delay predictions have no V/Line backend equivalent (Phase C design
 * brief's out-of-scope list) -- rather than passing no-op handlers. */
function buildTrainPopupContent(
  train: Train, routesById: ReadonlyMap<string, Route>, isTracked: boolean,
  onToggleTrack: (() => void) | undefined, onRequestDelayPrediction: (() => void) | undefined,
  delayPrediction: DelayPredictionState | undefined,
): HTMLElement {
  const content = document.createElement('div')
  content.className = 'train-popup-content'

  const titleRow = document.createElement('div')
  titleRow.className = 'train-popup-title-row'
  titleRow.append(createSwatch(markerColor(train, routesById)))
  const title = document.createElement('span')
  title.className = 'train-popup-title'
  title.textContent = lineNameForTrain(train, routesById)
  titleRow.append(title)
  content.append(titleRow)

  const identity = trainIdentityLabel(train)
  if (identity) {
    const identityRow = document.createElement('div')
    identityRow.className = 'train-popup-identity'
    identityRow.textContent = identity
    content.append(identityRow)
  }

  const nextStop = nextStopLabel(train)
  if (nextStop) {
    const nextStopRow = document.createElement('div')
    nextStopRow.className = 'train-popup-identity'
    nextStopRow.textContent = nextStop
    content.append(nextStopRow)
  }

  // "Am I late?" (M5 delay/ETA prediction) result -- placed right under
  // "Next: <station>" rather than after the buttons, grouping it with
  // the other per-trip schedule facts instead of the actions. Long
  // terminus names (e.g. "Cranbourne via Metro Tunnel") need to wrap,
  // not overflow the popup -- `train-popup-identity--wrap` overrides the
  // rest of the popup's deliberate single-line `white-space: nowrap`.
  const delayText = delayPredictionLabel(delayPrediction, train.trip_headsign)
  if (delayText) {
    const delayRow = document.createElement('div')
    const isStale = delayPrediction?.status === 'ok' && delayPrediction.stale
    delayRow.className = isStale
      ? 'train-popup-identity train-popup-identity--wrap train-popup-identity--stale'
      : 'train-popup-identity train-popup-identity--wrap'
    delayRow.textContent = delayText
    content.append(delayRow)
  }

  const meta = document.createElement('div')
  meta.className = 'train-popup-meta'
  const trackedPrefix = isTracked ? 'Tracked · ' : ''
  meta.textContent = `${trackedPrefix}${STATUS_LABEL[train.status]} · confirmed ${relativeTime(train.last_seen_at)}`
  content.append(meta)

  if (onToggleTrack) {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = isTracked ? 'train-popup-button train-popup-button--untrack' : 'train-popup-button'
    button.textContent = isTracked ? 'Untrack this train' : 'Track this train'
    button.addEventListener('click', (event) => {
      event.stopPropagation()
      onToggleTrack()
    })
    content.append(button)
  }

  if (onRequestDelayPrediction) {
    const delayButton = document.createElement('button')
    delayButton.type = 'button'
    delayButton.className = 'train-popup-button train-popup-button--secondary'
    delayButton.textContent = 'Am I late?'
    delayButton.addEventListener('click', (event) => {
      event.stopPropagation()
      onRequestDelayPrediction()
    })
    content.append(delayButton)
  }

  return content
}

export interface TrainPopupManager {
  /** Shows the click popup for `tripId`, hides it for `null`. `train` is
   * looked up by the caller (MapView already holds the full trains map)
   * and passed in rather than looked up here, same shape as
   * stationPopup.ts's `schedule` param. `isTracked` drives the Track
   * button's label/action when `onToggleTrack` is provided; V/Line's call
   * site omits it (and `onRequestDelayPrediction`) entirely, since neither
   * feature exists for V/Line -- the popup then renders as pure info, no
   * buttons. `delayPrediction` is this trip's current prediction state (if
   * any has been requested), rendered inline when present -- passed in,
   * not looked up here, same "caller already holds the map" shape as
   * `train` above. */
  sync(
    tripId: string | null, train: Train | null, isTracked: boolean,
    onToggleTrack: (() => void) | undefined, onRequestDelayPrediction: (() => void) | undefined,
    delayPrediction: DelayPredictionState | undefined,
  ): void
  /** Removes the popup from the map. Call on MapView unmount. */
  destroy(): void
}

export function createTrainPopupManager(
  map: maplibregl.Map, routesById: ReadonlyMap<string, Route>,
): TrainPopupManager {
  const popup = new maplibregl.Popup({
    className: 'train-popup',
    closeButton: false,
    closeOnClick: false,
    offset: 12,
  })

  return {
    sync(tripId, train, isTracked, onToggleTrack, onRequestDelayPrediction, delayPrediction) {
      if (!tripId || !train || train.latitude === null || train.longitude === null) {
        popup.remove()
        return
      }
      const content = buildTrainPopupContent(
        train, routesById, isTracked, onToggleTrack, onRequestDelayPrediction, delayPrediction,
      )
      popup.setLngLat([train.longitude, train.latitude]).setDOMContent(content).addTo(map)
    },
    destroy() {
      popup.remove()
    },
  }
}
