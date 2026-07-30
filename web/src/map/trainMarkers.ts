import * as maplibregl from 'maplibre-gl'
import './trainMarkers.css'
import { geometry } from '../geometry'
import { isRouteHidden } from './mapController'
import type { Train } from '../api-types'

const routeColorById = new Map(geometry.routes.map((route) => [route.id, route.color]))
const routeNameById = new Map(geometry.routes.map((route) => [route.id, route.name]))

// Deliberately not design tokens: these render on the map itself, not the
// app's UI chrome, so they stay constant regardless of light/dark theme.
const GHOST_COLOR = '#888888'
const UNKNOWN_ROUTE_COLOR = '#ffffff'

const OPACITY_BY_STATUS: Record<Train['status'], string> = {
  live: '1',
  coasting: '0.75',
  ghost: '0.4',
}

const STATUS_LABEL: Record<Train['status'], string> = {
  live: 'Live',
  coasting: 'Coasting',
  ghost: 'Ghost',
}

function markerColor(train: Train): string {
  if (train.status === 'ghost') return GHOST_COLOR
  return (train.route_id && routeColorById.get(train.route_id)) || UNKNOWN_ROUTE_COLOR
}

// Computed at the moment each update renders, not live-ticking while a
// tooltip is open -- precise enough given the ~10s poll cadence this data
// is refreshed at anyway.
function relativeTime(iso: string | null): string {
  if (!iso) return 'unknown'
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  return `${Math.round(seconds / 60)}m ago`
}

interface MarkerElements {
  root: HTMLDivElement
  pulse: HTMLDivElement
  dot: HTMLDivElement
  arrow: HTMLDivElement
  tooltip: HTMLDivElement
  tooltipSwatch: SVGRectElement
  tooltipTitle: HTMLSpanElement
  tooltipMeta: HTMLDivElement
}

const SVG_NS = 'http://www.w3.org/2000/svg'

// Plain DOM elements, not JSX -- MapLibre's Marker API wants a raw
// HTMLElement, and re-rendering ~200 of these through React on every SSE
// delta would undo the M4 decision to use MapLibre-native CSS transitions
// for smooth movement (see mapController.ts / milestones/04-map.md). Class
// names here are real global CSS classes (trainMarkers.css), NOT CSS
// Modules -- a module would hash/rename them and break this string-based
// wiring.
/** Root carries MapLibre's own position transform+transition (smooth
 * movement, per the M4 spec-review steelman — no hand-rolled
 * interpolation). Pulse/dot/arrow/tooltip are independent children so
 * their own CSS animation/rotation/visibility never fights that. Tooltip
 * show/hide is pure CSS (`:hover`), no JS listeners needed. */
function createMarkerElements(): MarkerElements {
  const root = document.createElement('div')
  root.className = 'train-marker'

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
  const tooltipMeta = document.createElement('div')
  tooltipMeta.className = 'train-tooltip-meta'
  tooltip.append(titleRow, tooltipMeta)
  root.append(tooltip)

  return { root, pulse, dot, arrow, tooltip, tooltipSwatch, tooltipTitle, tooltipMeta }
}

function styleMarkerElements(elements: MarkerElements, train: Train): void {
  const color = markerColor(train)
  elements.dot.style.backgroundColor = color
  elements.dot.style.opacity = OPACITY_BY_STATUS[train.status]
  elements.dot.classList.toggle('train-dot--ghost', train.status === 'ghost')

  // Only pulse when the feed has actually confirmed this train recently --
  // pulsing a ghost/coasting train would visually claim freshness the data
  // doesn't have.
  elements.pulse.style.display = train.status === 'live' ? 'block' : 'none'
  elements.pulse.style.backgroundColor = color

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
  const lineName = (train.route_id && routeNameById.get(train.route_id)) || 'Unknown line'
  elements.tooltipSwatch.setAttribute('fill', color)
  elements.tooltipTitle.textContent = lineName
  elements.tooltipMeta.textContent = `${STATUS_LABEL[train.status]} · confirmed ${relativeTime(train.last_seen_at)}`
}

export interface TrainMarkerManager {
  /** Reconciles markers against the current train set + legend visibility
   * + the "hide ghost trains" preference. */
  sync(trains: Map<string, Train>, hiddenRouteIds: ReadonlySet<string>, hideGhosts: boolean): void
  /** Removes every marker from the map. Call on MapView unmount. */
  destroy(): void
}

const INSTANT_CLASS = 'train-marker--instant'

/** Owns all train marker state for one map instance. Instantiated once per
 * `MapView` mount (not a module singleton) so remounting the map — e.g.
 * React StrictMode's dev-only double-invoke — can't leave stale markers
 * pointing at a removed map. */
export function createTrainMarkerManager(map: maplibregl.Map): TrainMarkerManager {
  const markers = new Map<string, maplibregl.Marker>()
  const elementsByTripId = new Map<string, MarkerElements>()
  // While the camera is moving, MapLibre updates every marker's transform
  // on each render frame -- the CSS position transition must be off for
  // that duration (see trainMarkers.css's INSTANT_CLASS comment) or dots
  // visibly lag behind a pan/zoom instead of tracking it.
  let isMoving = false

  function setInstant(elements: MarkerElements, instant: boolean): void {
    elements.root.classList.toggle(INSTANT_CLASS, instant)
  }

  map.on('movestart', () => {
    isMoving = true
    for (const elements of elementsByTripId.values()) setInstant(elements, true)
  })
  map.on('moveend', () => {
    isMoving = false
    for (const elements of elementsByTripId.values()) setInstant(elements, false)
  })

  function removeTrain(tripId: string): void {
    markers.get(tripId)?.remove()
    markers.delete(tripId)
    elementsByTripId.delete(tripId)
  }

  function upsertTrain(train: Train, hiddenRouteIds: ReadonlySet<string>, hideGhosts: boolean): void {
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
      if (isMoving) setInstant(elements, true)
      elementsByTripId.set(train.trip_id, elements)
      marker = new maplibregl.Marker({ element: elements.root })
      marker.setLngLat([train.longitude, train.latitude])
      marker.addTo(map)
      markers.set(train.trip_id, marker)
    } else {
      marker.setLngLat([train.longitude, train.latitude])
    }
    styleMarkerElements(elements, train)
  }

  return {
    sync(trains, hiddenRouteIds, hideGhosts) {
      for (const tripId of markers.keys()) {
        if (!trains.has(tripId)) removeTrain(tripId)
      }
      for (const train of trains.values()) {
        upsertTrain(train, hiddenRouteIds, hideGhosts)
      }
    },
    destroy() {
      for (const tripId of [...markers.keys()]) removeTrain(tripId)
    },
  }
}
