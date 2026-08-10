import * as maplibregl from 'maplibre-gl'
import './trainMarkers.css'
import { routesById } from '../geometry'
import { relativeTime } from '../lib/relativeTime'
import { formatStartTime } from '../lib/formatStartTime'
import { isRouteHidden } from './mapController'
import type { Train } from '../api-types'

// Deliberately not design tokens: these render on the map itself, not the
// app's UI chrome, so they stay constant regardless of light/dark theme.
const GHOST_COLOR = '#888888'
const UNKNOWN_ROUTE_COLOR = '#ffffff'
// Reserved for a tracked train's dot -- checked against every real line
// color in data/geometry.json (navy/blue/amber/light-blue/grey/green/red/
// pink) plus GHOST_COLOR/UNKNOWN_ROUTE_COLOR above, none close to this
// purple, so a tracked train is never confusable with "just its line's
// normal color."
export const TRACKED_COLOR = '#B026FF'

const OPACITY_BY_STATUS: Record<Train['status'], string> = {
  live: '1',
  coasting: '0.75',
  ghost: '0.4',
}

// Exported: StationPanel.tsx reuses this so a train's status reads the same
// way in the map tooltip and the sidebar panel.
export const STATUS_LABEL: Record<Train['status'], string> = {
  live: 'Live',
  coasting: 'Coasting',
  ghost: 'Ghost',
}

// Exported: same reuse reason as STATUS_LABEL above.
export function markerColor(train: Train): string {
  if (train.status === 'ghost') return GHOST_COLOR
  return (train.route_id && routesById.get(train.route_id)?.color) || UNKNOWN_ROUTE_COLOR
}

export function lineNameForTrain(train: Train): string {
  return (train.route_id && routesById.get(train.route_id)?.name) || 'Unknown line'
}

// Distinguishes individual trains on the same line -- e.g. two Belgrave
// trains only differ by which one is which departure/destination. `null`
// when either half is unavailable (no static snapshot pinned yet, or a
// real-time-only ADDED trip with no static headsign) -- omitted from the
// tooltip entirely rather than showing a half-filled label.
export function trainIdentityLabel(train: Train): string | null {
  const time = formatStartTime(train.start_time)
  if (!time || !train.trip_headsign) return null
  return `${time} to ${train.trip_headsign}`
}

interface MarkerElements {
  root: HTMLDivElement
  pulse: HTMLDivElement
  dot: HTMLDivElement
  arrow: HTMLDivElement
  tooltip: HTMLDivElement
  tooltipSwatch: SVGRectElement
  tooltipTitle: HTMLSpanElement
  tooltipIdentity: HTMLDivElement
  tooltipMeta: HTMLDivElement
}

const SVG_NS = 'http://www.w3.org/2000/svg'

// Plain DOM elements, not JSX -- MapLibre's Marker API wants a raw
// HTMLElement, and re-rendering ~200 of these through React on every SSE
// delta would be wasteful for what's ultimately just a `setLngLat()` call
// per update. Class names here are real global CSS classes
// (trainMarkers.css), NOT CSS Modules -- a module would hash/rename them
// and break this string-based wiring.
/** Root's position is set instantly on each update via MapLibre's own
 * `transform` (no CSS transition -- tried an eased position transition
 * (2026-07-31) but a straight-line interpolation between two correct
 * real-world fixes visibly cuts corners across any curve in the track
 * (shapes.txt curves, City Loop tunnels, coastal bends), since MapLibre
 * only knows the screen-pixel position, not the route shape between two
 * points; reverted rather than ship trains that visibly leave the rails.
 * True path-following interpolation would need real engineering -- point-
 * to-polyline projection + arc-length interpolation, driven by
 * requestAnimationFrame rather than a plain CSS transition -- parked as a
 * future option, not attempted here). Pulse/dot/arrow/tooltip are
 * independent children so their own CSS animation/rotation/visibility
 * never fights the position update. Tooltip show/hide is pure CSS
 * (`:hover`), no JS listeners needed. */
function createMarkerElements(): MarkerElements {
  const root = document.createElement('div')
  root.className = 'train-marker'

  // Invisible, larger than the visible 12px dot -- rendered position is
  // negative-inset off the root so it's outside the root's own box, but
  // hovering/tapping it still triggers the root's `:hover`/tap state (CSS
  // hover follows the actual rendered element, not the ancestor's box).
  // Keeps the tiny visual dot but gives touch/mouse a real target size.
  const hitbox = document.createElement('div')
  hitbox.className = 'train-marker-hitbox'
  root.append(hitbox)

  const pulse = document.createElement('div')
  pulse.className = 'train-pulse'
  root.append(pulse)

  const dot = document.createElement('div')
  dot.className = 'train-dot'
  root.append(dot)

  const arrow = document.createElement('div')
  arrow.className = 'train-arrow'
  root.append(arrow)

  const tooltip = document.createElement('div')
  tooltip.className = 'train-tooltip'
  const titleRow = document.createElement('div')
  titleRow.className = 'train-tooltip-title-row'

  const swatchSvg = document.createElementNS(SVG_NS, 'svg')
  swatchSvg.setAttribute('class', 'train-tooltip-swatch')
  swatchSvg.setAttribute('viewBox', '0 0 10 10')
  swatchSvg.setAttribute('width', '10')
  swatchSvg.setAttribute('height', '10')
  const tooltipSwatch = document.createElementNS(SVG_NS, 'rect')
  tooltipSwatch.setAttribute('x', '0.75')
  tooltipSwatch.setAttribute('y', '0.75')
  tooltipSwatch.setAttribute('width', '8.5')
  tooltipSwatch.setAttribute('height', '8.5')
  tooltipSwatch.setAttribute('rx', '1.5')
  tooltipSwatch.setAttribute('stroke', '#ffffff')
  tooltipSwatch.setAttribute('stroke-width', '1')
  swatchSvg.append(tooltipSwatch)

  const tooltipTitle = document.createElement('span')
  tooltipTitle.className = 'train-tooltip-title'
  titleRow.append(swatchSvg, tooltipTitle)
  const tooltipIdentity = document.createElement('div')
  tooltipIdentity.className = 'train-tooltip-identity'
  const tooltipMeta = document.createElement('div')
  tooltipMeta.className = 'train-tooltip-meta'
  tooltip.append(titleRow, tooltipIdentity, tooltipMeta)
  root.append(tooltip)

  return { root, pulse, dot, arrow, tooltip, tooltipSwatch, tooltipTitle, tooltipIdentity, tooltipMeta }
}

function styleMarkerElements(elements: MarkerElements, train: Train, isTracked: boolean): void {
  // Two distinct colors, deliberately not one: `lineColor` is the train's
  // actual line -- always what the tooltip swatch shows, tracked or not,
  // since the swatch's job is "which line is this," not "is this tracked"
  // (found live: reusing one `color` var for both the dot AND the swatch
  // made the swatch turn purple too, which reads as "this train's line IS
  // purple," wrong). `dotColor` is what the marker itself renders --
  // overridden to the reserved tracked color, with the ring/glow in CSS
  // (.train-marker--tracked) doing the rest of the "this one's tracked"
  // signal.
  const lineColor = markerColor(train)
  const dotColor = isTracked ? TRACKED_COLOR : lineColor
  elements.dot.style.backgroundColor = dotColor
  elements.dot.style.opacity = OPACITY_BY_STATUS[train.status]
  elements.dot.classList.toggle('train-dot--ghost', train.status === 'ghost')
  elements.root.classList.toggle('train-marker--tracked', isTracked)

  // Only pulse when the feed has actually confirmed this train recently --
  // pulsing a ghost/coasting train would visually claim freshness the data
  // doesn't have.
  elements.pulse.style.display = train.status === 'live' ? 'block' : 'none'
  elements.pulse.style.backgroundColor = dotColor

  // `bearing` is a real compass heading from the feed (VP-populated,
  // 0-360, see CLAUDE.md), not inferred -- omit the arrow entirely rather
  // than guess when it's null (no live VP fix, e.g. most ghosts).
  if (train.bearing === null) {
    elements.arrow.style.display = 'none'
  } else {
    elements.arrow.style.display = 'block'
    elements.arrow.style.transform = `rotate(${train.bearing}deg)`
  }

  // A swatch, not colored text -- some line colors (e.g. Belgrave/Lilydale's
  // dark navy) are unreadable as text on the tooltip's dark background.
  // Same fix the legend already uses.
  elements.tooltipSwatch.setAttribute('fill', lineColor)
  elements.tooltipTitle.textContent = lineNameForTrain(train)
  const identity = trainIdentityLabel(train)
  elements.tooltipIdentity.textContent = identity
  elements.tooltipIdentity.style.display = identity ? 'block' : 'none'
  const trackedPrefix = isTracked ? 'Tracked · ' : ''
  elements.tooltipMeta.textContent = `${trackedPrefix}${STATUS_LABEL[train.status]} · confirmed ${relativeTime(train.last_seen_at)}`
}

export interface TrainMarkerManager {
  /** Reconciles markers against the current train set + legend visibility
   * + the "hide ghost trains" preference. `trackedTripId` (null = nothing
   * tracked) drives the tracked marker's distinct color/glow and its
   * tooltip's "Tracked" prefix. */
  sync(
    trains: Map<string, Train>,
    hiddenRouteIds: ReadonlySet<string>,
    hideGhosts: boolean,
    trackedTripId: string | null,
  ): void
  /** Removes every marker from the map. Call on MapView unmount. */
  destroy(): void
}

/** Owns all train marker state for one map instance. Instantiated once per
 * `MapView` mount (not a module singleton) so remounting the map — e.g.
 * React StrictMode's dev-only double-invoke — can't leave stale markers
 * pointing at a removed map.
 *
 * `onTrainClick` opens the click/tap track-untrack popup (trainPopup.ts) --
 * deliberately separate from this marker's own hover tooltip (pure CSS
 * `:hover`, no listener), per the click-vs-hover split decided in planning:
 * hover is a quick glance, click is the one path that also works on touch
 * (which never fires `:hover` at all).
 *
 * `onTrainRemoved` fires whenever a trip's marker is torn down -- including
 * hidden-by-legend/hide-ghosts removals, not just the train leaving the
 * feed entirely. App.tsx only acts on it when the removed trip is the
 * currently-tracked one (clears tracking); a legend-hide of an untracked
 * train's marker is a no-op call. */
export function createTrainMarkerManager(
  map: maplibregl.Map,
  onTrainClick: (tripId: string) => void,
  onTrainRemoved: (tripId: string) => void,
): TrainMarkerManager {
  const markers = new Map<string, maplibregl.Marker>()
  const elementsByTripId = new Map<string, MarkerElements>()

  function removeTrain(tripId: string): void {
    markers.get(tripId)?.remove()
    markers.delete(tripId)
    elementsByTripId.delete(tripId)
    onTrainRemoved(tripId)
  }

  function upsertTrain(
    train: Train,
    hiddenRouteIds: ReadonlySet<string>,
    hideGhosts: boolean,
    trackedTripId: string | null,
  ): void {
    if (
      train.latitude === null ||
      train.longitude === null ||
      isRouteHidden(train.route_id, hiddenRouteIds) ||
      (hideGhosts && train.status === 'ghost')
    ) {
      removeTrain(train.trip_id)
      return
    }
    let marker = markers.get(train.trip_id)
    let elements = elementsByTripId.get(train.trip_id)
    if (!marker || !elements) {
      elements = createMarkerElements()
      elements.root.addEventListener('click', (event) => {
        event.stopPropagation()
        onTrainClick(train.trip_id)
      })
      elementsByTripId.set(train.trip_id, elements)
      marker = new maplibregl.Marker({ element: elements.root })
      marker.setLngLat([train.longitude, train.latitude])
      marker.addTo(map)
      markers.set(train.trip_id, marker)
    } else {
      marker.setLngLat([train.longitude, train.latitude])
    }
    styleMarkerElements(elements, train, train.trip_id === trackedTripId)
  }

  return {
    sync(trains, hiddenRouteIds, hideGhosts, trackedTripId) {
      for (const tripId of markers.keys()) {
        if (!trains.has(tripId)) removeTrain(tripId)
      }
      for (const train of trains.values()) {
        upsertTrain(train, hiddenRouteIds, hideGhosts, trackedTripId)
      }
    },
    destroy() {
      for (const tripId of [...markers.keys()]) removeTrain(tripId)
    },
  }
}
