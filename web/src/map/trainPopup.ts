import * as maplibregl from 'maplibre-gl'
import './trainPopup.css'
import { relativeTime } from '../lib/relativeTime'
import { lineNameForTrain, markerColor, nextStopLabel, STATUS_LABEL, trainIdentityLabel } from './trainMarkers'
import type { Train } from '../api-types'

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
 * tooltip (trainMarkers.ts), plus the Track/Untrack action -- deliberately
 * a separate element tree from the hover tooltip rather than reusing it,
 * since this one needs to stay open (click, not `:hover`) and carry an
 * interactive button, which the pure-CSS hover tooltip never needs to. */
function buildTrainPopupContent(
  train: Train, isTracked: boolean, onToggleTrack: () => void, onRequestDelayPrediction: () => void,
): HTMLElement {
  const content = document.createElement('div')
  content.className = 'train-popup-content'

  const titleRow = document.createElement('div')
  titleRow.className = 'train-popup-title-row'
  titleRow.append(createSwatch(markerColor(train)))
  const title = document.createElement('span')
  title.className = 'train-popup-title'
  title.textContent = lineNameForTrain(train)
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

  const meta = document.createElement('div')
  meta.className = 'train-popup-meta'
  const trackedPrefix = isTracked ? 'Tracked · ' : ''
  meta.textContent = `${trackedPrefix}${STATUS_LABEL[train.status]} · confirmed ${relativeTime(train.last_seen_at)}`
  content.append(meta)

  const button = document.createElement('button')
  button.type = 'button'
  button.className = isTracked ? 'train-popup-button train-popup-button--untrack' : 'train-popup-button'
  button.textContent = isTracked ? 'Untrack this train' : 'Track this train'
  button.addEventListener('click', (event) => {
    event.stopPropagation()
    onToggleTrack()
  })
  content.append(button)

  // "Am I late?" (M5 delay/ETA prediction) -- a plain regression lookup
  // against already-tracked live state, no LLM call, so there's no per-
  // click cost to worry about; the result (with an "as of <time>" stamp)
  // shows in the hover tooltip (trainMarkers.ts), not here, since this
  // popup closes as soon as the user clicks elsewhere on the map.
  const delayButton = document.createElement('button')
  delayButton.type = 'button'
  delayButton.className = 'train-popup-button train-popup-button--secondary'
  delayButton.textContent = 'Am I late?'
  delayButton.addEventListener('click', (event) => {
    event.stopPropagation()
    onRequestDelayPrediction()
  })
  content.append(delayButton)

  return content
}

export interface TrainPopupManager {
  /** Shows the track/untrack popup for `tripId`, hides it for `null`.
   * `train` is looked up by the caller (MapView already holds the full
   * trains map) and passed in rather than looked up here, same shape as
   * stationPopup.ts's `schedule` param. `isTracked` drives the button
   * label/action; `onToggleTrack` is called with no further args since the
   * caller already knows which trip this popup is for. Same for
   * `onRequestDelayPrediction` -- the "Am I late?" CTA. */
  sync(
    tripId: string | null, train: Train | null, isTracked: boolean,
    onToggleTrack: () => void, onRequestDelayPrediction: () => void,
  ): void
  /** Removes the popup from the map. Call on MapView unmount. */
  destroy(): void
}

export function createTrainPopupManager(map: maplibregl.Map): TrainPopupManager {
  const popup = new maplibregl.Popup({
    className: 'train-popup',
    closeButton: false,
    closeOnClick: false,
    offset: 12,
  })

  return {
    sync(tripId, train, isTracked, onToggleTrack, onRequestDelayPrediction) {
      if (!tripId || !train || train.latitude === null || train.longitude === null) {
        popup.remove()
        return
      }
      const content = buildTrainPopupContent(train, isTracked, onToggleTrack, onRequestDelayPrediction)
      popup.setLngLat([train.longitude, train.latitude]).setDOMContent(content).addTo(map)
    },
    destroy() {
      popup.remove()
    },
  }
}
