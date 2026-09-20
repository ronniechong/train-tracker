import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Bounds, Geometry } from '../geometry'
import type { Theme } from '../hooks/useTheme'

// OpenFreeMap: free, no API key, no published rate limits. Provisional;
// swap the style URLs here if it turns out to behave badly under real
// use. `dark` confirmed live as a real, separate OpenFreeMap style (not a
// CSS filter) -- same URL pattern as `liberty`.
const OPENFREEMAP_STYLES: Record<Theme, string> = {
  light: 'https://tiles.openfreemap.org/styles/liberty',
  dark: 'https://tiles.openfreemap.org/styles/dark',
}

// Fixed, decisive zoom rather than "at least the current zoom" -- a search
// result should feel consistent regardless of where the camera happened to
// be, and it's comfortably above Metro's stationMinZoom so the station
// itself renders immediately. Metro-only (V/Line has no search feature).
const STATION_FLY_ZOOM = 14

export interface MapDataConfig {
  geometry: Geometry
  routesByStationId: ReadonlyMap<string, Set<string>>
  /** [lon, lat] -- deliberately NOT the same as fitting the network's real
   * bounding box: an asymmetric network (e.g. Metro's Stony Point pulling
   * the box far south) would center the empty middle of that box, not
   * where a user is actually oriented. */
  center: [number, number]
  initialZoom: number
  /** How far the user can zoom out. Metro's compact network stays legible
   * at 9; V/Line's much larger geographic extent needs a lower floor so
   * the whole network can actually be zoomed out to. */
  minZoom: number
  /** Zoom level at which station points start rendering, to avoid
   * cluttering the whole-network view. Metro's dense 226-station network
   * needs 11; V/Line's sparser 110 stations across a much wider area can
   * show much earlier. */
  stationMinZoom: number
  /** Keeps each mode's MapLibre source/layer ids distinct -- harmless even
   * though only one mode's map is ever mounted at a time (route change
   * unmounts the whole tree), but avoids any possibility of collision if
   * that ever changes. */
  sourceIdPrefix: string
  /** Optional per-route MapLibre `line-dasharray`, keyed by route id.
   * Omitted for Metro (every route renders as one plain solid line, same
   * as before this config existed). V/Line uses this to carry per-corridor
   * identity via line style instead of colour, since `line-dasharray`
   * isn't a data-driven MapLibre paint property -- one MapLibre layer per
   * distinct dash pattern, not one shared layer, is what makes this work. */
  lineDashArrayByRouteId?: ReadonlyMap<string, number[] | undefined>
}

const SOLID_DASH_KEY = 'solid'

function dashKeyFor(dashArray: number[] | undefined): string {
  return dashArray ? dashArray.join('-') : SOLID_DASH_KEY
}

function boundsToLngLatBounds(bounds: Bounds): maplibregl.LngLatBoundsLike {
  return [
    [bounds.west, bounds.south],
    [bounds.east, bounds.north],
  ]
}

/** Whether a train's route is currently toggled off in the legend. `null`
 * route_id (ghosts with no known route) is never considered hidden. */
export function isRouteHidden(routeId: string | null, hiddenRouteIds: ReadonlySet<string>): boolean {
  return routeId !== null && hiddenRouteIds.has(routeId)
}

export interface MapController {
  initMap(container: HTMLElement, theme: Theme): maplibregl.Map
  setMapStyle(map: maplibregl.Map, theme: Theme, onReady: () => void): void
  resetInitialView(map: maplibregl.Map): void
  flyToDefaultView(map: maplibregl.Map): void
  addGeometryLayers(map: maplibregl.Map, hiddenRouteIds: ReadonlySet<string>): void
  applyHiddenRoutes(map: maplibregl.Map, hiddenRouteIds: ReadonlySet<string>): void
  registerStationInteractions(map: maplibregl.Map, onStationClick: (stationId: string | null) => void): void
  flyToStation(map: maplibregl.Map, station: { lat: number; lon: number }): void
}

/** Builds a map controller bound to one geometry set + view config --
 * Metro and V/Line each get their own instance (see `metroMapConfig.ts`/
 * `vlineMapConfig.ts`) rather than this module hardcoding Metro's data,
 * so the same MapLibre wiring works for both without duplicating it. */
export function createMapController(config: MapDataConfig): MapController {
  const routesSourceId = `${config.sourceIdPrefix}-routes`
  const stationsSourceId = `${config.sourceIdPrefix}-stations`
  const routeLinesLayerIdPrefix = `${config.sourceIdPrefix}-route-lines`
  const stationPointsLayerId = `${config.sourceIdPrefix}-station-points`
  const stationHitLayerId = `${config.sourceIdPrefix}-station-hit`
  // Populated by addGeometryLayers -- one layer id per distinct dash
  // pattern actually present in this geometry (just one, "-solid", for
  // Metro). applyHiddenRoutes needs to filter every one of them, and a
  // basemap swap needs to know what to re-add.
  let routeLineLayerIds: string[] = []

  function routesToGeoJSON(): GeoJSON.FeatureCollection<GeoJSON.LineString> {
    return {
      type: 'FeatureCollection',
      features: config.geometry.routes
        .filter((route) => route.shape.length >= 2)
        .map((route) => ({
          type: 'Feature',
          properties: {
            id: route.id,
            name: route.name,
            color: route.color,
            dashKey: dashKeyFor(config.lineDashArrayByRouteId?.get(route.id)),
          },
          geometry: { type: 'LineString', coordinates: route.shape },
        })),
    }
  }

  function stationsToGeoJSON(hidden: ReadonlySet<string>): GeoJSON.FeatureCollection<GeoJSON.Point> {
    return {
      type: 'FeatureCollection',
      features: config.geometry.stations
        .filter((station) => {
          const servingRoutes = config.routesByStationId.get(station.id)
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

  function initMap(container: HTMLElement, theme: Theme): maplibregl.Map {
    return new maplibregl.Map({
      container,
      style: OPENFREEMAP_STYLES[theme],
      center: config.center,
      zoom: config.initialZoom,
      minZoom: config.minZoom,
      maxBounds: boundsToLngLatBounds(config.geometry.bounds),
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
  function setMapStyle(map: maplibregl.Map, theme: Theme, onReady: () => void): void {
    map.setStyle(OPENFREEMAP_STYLES[theme])
    map.once('style.load', onReady)
  }

  /** MapLibre computes its initial center/zoom against the container's size
   * at construction time, clamped by `maxBounds`. Mounting inside a React
   * effect risks that measurement racing the CSS grid/`100dvh` layout
   * settling, which -- given an asymmetric network bounding box -- can
   * clamp the camera to a visibly wrong spot rather than just a slightly
   * wrong zoom. Cheap and correct regardless of root cause: force a resize
   * against the now-settled container, then re-assert the intended camera.
   * Call once, after the map's `load` event. */
  function resetInitialView(map: maplibregl.Map): void {
    map.jumpTo({ center: config.center, zoom: config.initialZoom })
  }

  /** User-facing "recenter map" action (sidebar CTA) -- animated, unlike
   * `resetInitialView` above which is an instant jump used only to work
   * around the load-time container-sizing race. */
  function flyToDefaultView(map: maplibregl.Map): void {
    map.flyTo({ center: config.center, zoom: config.initialZoom })
  }

  /** Adds the route-line and station-point layers. Call once after the
   * map's `load` event -- GeoJSON sources can't be added before the style
   * is ready. */
  function addGeometryLayers(map: maplibregl.Map, hiddenRouteIds: ReadonlySet<string>): void {
    map.addSource(routesSourceId, { type: 'geojson', data: routesToGeoJSON() })
    map.addSource(stationsSourceId, { type: 'geojson', data: stationsToGeoJSON(hiddenRouteIds) })

    // Distinct dash patterns actually present in this geometry -- for
    // Metro that's just the one entry (SOLID_DASH_KEY, since
    // lineDashArrayByRouteId is never set), so this reduces to the single
    // plain layer this used to be unconditionally.
    const dashPatterns = new Map<string, number[] | undefined>()
    for (const route of config.geometry.routes) {
      const dashArray = config.lineDashArrayByRouteId?.get(route.id)
      dashPatterns.set(dashKeyFor(dashArray), dashArray)
    }
    if (dashPatterns.size === 0) dashPatterns.set(SOLID_DASH_KEY, undefined)

    routeLineLayerIds = [...dashPatterns.keys()].map((key) => `${routeLinesLayerIdPrefix}-${key}`)
    for (const [dashKey, dashArray] of dashPatterns) {
      map.addLayer({
        id: `${routeLinesLayerIdPrefix}-${dashKey}`,
        type: 'line',
        source: routesSourceId,
        filter: ['==', ['get', 'dashKey'], dashKey],
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 3,
          'line-opacity': 0.85,
          ...(dashArray ? { 'line-dasharray': dashArray } : {}),
        },
      })
    }

    // Invisible, larger circle underneath the visible dot -- MapLibre hit-
    // tests whatever's actually rendered at the pixel, so an 8px-diameter
    // dot means an 8px-diameter click/tap target. This layer is added
    // first (renders below) and is what interactions below actually
    // query/listen on; the visible dot layer stays purely cosmetic.
    map.addLayer({
      id: stationHitLayerId,
      type: 'circle',
      source: stationsSourceId,
      minzoom: config.stationMinZoom,
      paint: {
        'circle-radius': 16,
        'circle-color': '#000000',
        'circle-opacity': 0,
      },
    })

    map.addLayer({
      id: stationPointsLayerId,
      type: 'circle',
      source: stationsSourceId,
      minzoom: config.stationMinZoom,
      paint: {
        'circle-radius': 4,
        'circle-color': '#ffffff',
        'circle-stroke-color': '#1a1a1a',
        'circle-stroke-width': 1.5,
      },
    })
  }

  /** Legend show/hide toggle. Updates a filter on the shared route-lines
   * layer (one filter, not one MapLibre layer per route) and recomputes
   * which stations should still show (a station only hides once every
   * route serving it is hidden -- toggling one line off shouldn't hide an
   * interchange still served by another visible line). Train markers are
   * DOM elements outside MapLibre's own layers, so they're not handled
   * here -- callers re-sync trainMarkers separately using the same
   * `hiddenRouteIds`. */
  function applyHiddenRoutes(map: maplibregl.Map, hiddenRouteIds: ReadonlySet<string>): void {
    const hidden = [...hiddenRouteIds]
    const hiddenFilter: maplibregl.FilterSpecification | null =
      hidden.length === 0 ? null : ['!', ['in', ['get', 'id'], ['literal', hidden]]]
    for (const layerId of routeLineLayerIds) {
      const dashKey = layerId.slice(routeLinesLayerIdPrefix.length + 1)
      const dashFilter: maplibregl.FilterSpecification = ['==', ['get', 'dashKey'], dashKey]
      map.setFilter(layerId, hiddenFilter ? ['all', dashFilter, hiddenFilter] : dashFilter)
    }
    const stationsSource = map.getSource(stationsSourceId) as maplibregl.GeoJSONSource
    stationsSource.setData(stationsToGeoJSON(hiddenRouteIds))
  }

  /** Station click/hover wiring. One `map`-level click handler that
   * resolves hit-testing via `queryRenderedFeatures`, rather than a
   * separate station-layer click handler racing a map-level "clicked
   * empty space" handler -- single source of truth for what was actually
   * clicked. `null` means the click missed every station (including
   * stations not currently rendered below `stationMinZoom`), which the
   * caller treats as "clear selection". Call once, after the map's `load`
   * event. */
  function registerStationInteractions(
    map: maplibregl.Map,
    onStationClick: (stationId: string | null) => void,
  ): void {
    map.on('mouseenter', stationHitLayerId, () => {
      map.getCanvas().style.cursor = 'pointer'
    })
    map.on('mouseleave', stationHitLayerId, () => {
      map.getCanvas().style.cursor = ''
    })
    map.on('click', (event) => {
      // Train markers are DOM elements layered above the map canvas, not a
      // MapLibre layer -- a click landing on one still bubbles up to this
      // handler. Trains are already the foreground interactive element
      // there (their own hover tooltip), so back off entirely rather than
      // also resolving the station underneath.
      const target = event.originalEvent.target
      if (target instanceof Element && target.closest('.train-marker')) return

      const features = map.queryRenderedFeatures(event.point, { layers: [stationHitLayerId] })
      const stationId = (features[0]?.properties?.id as string | undefined) ?? null
      onStationClick(stationId)
    })
  }

  function flyToStation(map: maplibregl.Map, station: { lat: number; lon: number }): void {
    map.flyTo({ center: [station.lon, station.lat], zoom: STATION_FLY_ZOOM })
  }

  return {
    initMap,
    setMapStyle,
    resetInitialView,
    flyToDefaultView,
    addGeometryLayers,
    applyHiddenRoutes,
    registerStationInteractions,
    flyToStation,
  }
}
