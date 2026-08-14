import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { geometry, routesByStationId, type Bounds } from '../geometry'
import type { Theme } from '../hooks/useTheme'

// OpenFreeMap: free, no API key, no published rate limits. Provisional;
// swap the style URLs here if it turns out to behave badly under real
// use. `dark` confirmed live as a real, separate OpenFreeMap style (not a
// CSS filter) -- same URL pattern as `liberty`.
const OPENFREEMAP_STYLES: Record<Theme, string> = {
  light: 'https://tiles.openfreemap.org/styles/liberty',
  dark: 'https://tiles.openfreemap.org/styles/dark',
}

const ROUTES_SOURCE_ID = 'metro-routes'
const STATIONS_SOURCE_ID = 'metro-stations'
const ROUTE_LINES_LAYER_ID = 'metro-route-lines'
const STATION_POINTS_LAYER_ID = 'metro-station-points'
const STATION_HIT_LAYER_ID = 'metro-station-hit'

// Stations only render once zoomed in enough to avoid ~226 dots cluttering
// the whole-network view.
const STATION_MIN_ZOOM = 11

// Deliberately NOT the same as maxBounds: the network's real bounding box
// isn't symmetric around the CBD (Stony Point pulls it far south, Pakenham/
// Belgrave pull it east), so fitting the whole-network box on load centers
// the empty middle of that box, not the city — noticeably off-center.
// Fixed center/zoom here instead; maxBounds (below) still uses the real
// computed extent so panning stays limited to the actual network.
const MELBOURNE_CBD: [number, number] = [144.9631, -37.8136]
const INITIAL_ZOOM = 11

function boundsToLngLatBounds(bounds: Bounds): maplibregl.LngLatBoundsLike {
  return [
    [bounds.west, bounds.south],
    [bounds.east, bounds.north],
  ]
}

function routesToGeoJSON(): GeoJSON.FeatureCollection<GeoJSON.LineString> {
  return {
    type: 'FeatureCollection',
    features: geometry.routes
      .filter((route) => route.shape.length >= 2)
      .map((route) => ({
        type: 'Feature',
        properties: { id: route.id, name: route.name, color: route.color },
        geometry: { type: 'LineString', coordinates: route.shape },
      })),
  }
}

function stationsToGeoJSON(hidden: ReadonlySet<string>): GeoJSON.FeatureCollection<GeoJSON.Point> {
  return {
    type: 'FeatureCollection',
    features: geometry.stations
      .filter((station) => {
        const servingRoutes = routesByStationId.get(station.id)
        // No known serving routes -- shouldn't happen given the build
        // script only includes referenced stations, but keep visible
        // rather than guess if it ever does.
        if (!servingRoutes || servingRoutes.size === 0) return true
        return [...servingRoutes].some((routeId) => !hidden.has(routeId))
      })
      .map((station) => ({
        type: 'Feature',
        properties: { id: station.id, name: station.name },
        geometry: { type: 'Point', coordinates: [station.lon, station.lat] },
      })),
  }
}

/** Whether a train's route is currently toggled off in the legend. `null`
 * route_id (ghosts with no known route) is never considered hidden. */
export function isRouteHidden(routeId: string | null, hiddenRouteIds: ReadonlySet<string>): boolean {
  return routeId !== null && hiddenRouteIds.has(routeId)
}

export function initMap(container: HTMLElement, theme: Theme): maplibregl.Map {
  return new maplibregl.Map({
    container,
    style: OPENFREEMAP_STYLES[theme],
    center: MELBOURNE_CBD,
    zoom: INITIAL_ZOOM,
    minZoom: 9,
    maxBounds: boundsToLngLatBounds(geometry.bounds),
    attributionControl: { compact: true },
  }).addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
}

/** Swaps the basemap for a theme change on an already-mounted map.
 * `setStyle` wipes every style-level source/layer we added (route lines,
 * station points -- NOT markers/popups, which are plain DOM overlays
 * outside the style entirely and survive untouched), so `onReady` is
 * where the caller re-adds them via `addGeometryLayers`/
 * `applyHiddenRoutes` -- `style.load` is MapLibre's real "safe to add
 * layers again" event, not `load` (that only fires once, on initial
 * mount). Camera position (center/zoom/bearing) is untouched by a style
 * swap -- it's map state, not style state. */
export function setMapStyle(map: maplibregl.Map, theme: Theme, onReady: () => void): void {
  map.setStyle(OPENFREEMAP_STYLES[theme])
  map.once('style.load', onReady)
}

/** MapLibre computes its initial center/zoom against the container's size
 * at construction time, clamped by `maxBounds`. Mounting inside a React
 * effect (vs. the vanilla build's synchronous script-tag init) risks that
 * measurement racing the CSS grid/`100dvh` layout settling, which -- given
 * the network's bounds are asymmetric around the CBD (Stony Point pulls
 * the south edge much further out than the north edge, see CLAUDE.md) --
 * can clamp the camera to a visibly wrong spot rather than just a slightly
 * wrong zoom. Cheap and correct regardless of root cause: force a resize
 * against the now-settled container, then re-assert the intended camera.
 * Call once, after the map's `load` event. */
export function resetInitialView(map: maplibregl.Map): void {
  map.jumpTo({ center: MELBOURNE_CBD, zoom: INITIAL_ZOOM })
}

/** User-facing "recenter map" action (sidebar CTA) -- animated, unlike
 * `resetInitialView` above which is an instant jump used only to work
 * around the load-time container-sizing race. */
export function flyToDefaultView(map: maplibregl.Map): void {
  map.flyTo({ center: MELBOURNE_CBD, zoom: INITIAL_ZOOM })
}

/** Adds the route-line and station-point layers. Call once after the map's
 * `load` event — GeoJSON sources can't be added before the style is ready. */
export function addGeometryLayers(map: maplibregl.Map, hiddenRouteIds: ReadonlySet<string>): void {
  map.addSource(ROUTES_SOURCE_ID, { type: 'geojson', data: routesToGeoJSON() })
  map.addSource(STATIONS_SOURCE_ID, { type: 'geojson', data: stationsToGeoJSON(hiddenRouteIds) })

  map.addLayer({
    id: ROUTE_LINES_LAYER_ID,
    type: 'line',
    source: ROUTES_SOURCE_ID,
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': ['get', 'color'], 'line-width': 3, 'line-opacity': 0.85 },
  })

  // Invisible, larger circle underneath the visible dot -- MapLibre hit-tests
  // whatever's actually rendered at the pixel, so an 8px-diameter dot means
  // an 8px-diameter click/tap target. This layer is added first (renders
  // below) and is what interactions below actually query/listen on; the
  // visible dot layer stays purely cosmetic.
  map.addLayer({
    id: STATION_HIT_LAYER_ID,
    type: 'circle',
    source: STATIONS_SOURCE_ID,
    minzoom: STATION_MIN_ZOOM,
    paint: {
      'circle-radius': 16,
      'circle-color': '#000000',
      'circle-opacity': 0,
    },
  })

  map.addLayer({
    id: STATION_POINTS_LAYER_ID,
    type: 'circle',
    source: STATIONS_SOURCE_ID,
    minzoom: STATION_MIN_ZOOM,
    paint: {
      'circle-radius': 4,
      'circle-color': '#ffffff',
      'circle-stroke-color': '#1a1a1a',
      'circle-stroke-width': 1.5,
    },
  })
}

/** Legend show/hide toggle. Updates a filter on the shared route-lines
 * layer (one filter, not one MapLibre layer per route — 18 routes, not
 * worth the extra layers) and recomputes which stations should still show
 * (a station only hides once every route serving it is hidden — toggling
 * one line off shouldn't hide an interchange still served by another
 * visible line). Train markers are DOM elements outside MapLibre's own
 * layers, so they're not handled here — callers re-sync trainMarkers
 * separately using the same `hiddenRouteIds`, see `MapView.tsx`. */
export function applyHiddenRoutes(map: maplibregl.Map, hiddenRouteIds: ReadonlySet<string>): void {
  const hidden = [...hiddenRouteIds]
  map.setFilter(
    ROUTE_LINES_LAYER_ID,
    hidden.length === 0 ? null : ['!', ['in', ['get', 'id'], ['literal', hidden]]],
  )
  const stationsSource = map.getSource(STATIONS_SOURCE_ID) as maplibregl.GeoJSONSource
  stationsSource.setData(stationsToGeoJSON(hiddenRouteIds))
}

/** Station click/hover wiring for M4 Stage 4. One `map`-level click handler
 * that resolves hit-testing via `queryRenderedFeatures`, rather than a
 * separate station-layer click handler racing a map-level "clicked empty
 * space" handler -- single source of truth for what was actually clicked.
 * `null` means the click missed every station (including stations not
 * currently rendered below `STATION_MIN_ZOOM`), which the caller treats as
 * "clear selection". Call once, after the map's `load` event. */
export function registerStationInteractions(
  map: maplibregl.Map,
  onStationClick: (stationId: string | null) => void,
): void {
  map.on('mouseenter', STATION_HIT_LAYER_ID, () => {
    map.getCanvas().style.cursor = 'pointer'
  })
  map.on('mouseleave', STATION_HIT_LAYER_ID, () => {
    map.getCanvas().style.cursor = ''
  })
  map.on('click', (event) => {
    // Train markers are DOM elements layered above the map canvas (see
    // trainMarkers.ts), not a MapLibre layer -- a click landing on one
    // still bubbles up to this handler (found live, 2026-07-31: a train
    // sitting on top of its own station opened both the train's hover
    // tooltip AND the station's click popup, overlapping). Trains are
    // already the foreground interactive element there (their own hover
    // tooltip), so back off entirely rather than also resolving the
    // station underneath.
    const target = event.originalEvent.target
    if (target instanceof Element && target.closest('.train-marker')) return

    const features = map.queryRenderedFeatures(event.point, { layers: [STATION_HIT_LAYER_ID] })
    const stationId = (features[0]?.properties?.id as string | undefined) ?? null
    onStationClick(stationId)
  })
}

// Fixed, decisive zoom rather than "at least the current zoom" -- a search
// result should feel consistent regardless of where the camera happened to
// be, and it's comfortably above STATION_MIN_ZOOM so the station itself
// renders immediately.
const STATION_FLY_ZOOM = 14

/** Used by the search "jump to station" flow (App.tsx's flyToRequest). */
export function flyToStation(map: maplibregl.Map, station: { lat: number; lon: number }): void {
  map.flyTo({ center: [station.lon, station.lat], zoom: STATION_FLY_ZOOM })
}
