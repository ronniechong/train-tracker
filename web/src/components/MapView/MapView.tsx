import { useEffect, useRef, useState } from 'react'
import type * as maplibregl from 'maplibre-gl'
import {
  addGeometryLayers,
  applyHiddenRoutes,
  flyToDefaultView,
  flyToStation,
  initMap,
  registerStationInteractions,
  resetInitialView,
  setMapStyle,
} from '../../map/mapController'
import { createTrainMarkerManager, type TrainMarkerManager } from '../../map/trainMarkers'
import { createStationPopupManager, type StationPopupManager } from '../../map/stationPopup'
import { LoadingOverlay } from '../LoadingOverlay'
import type { Train } from '../../api-types'
import type { StationScheduleState } from '../../hooks/useStationSchedule'
import type { Theme } from '../../hooks/useTheme'
import styles from './MapView.module.css'

// A search selection carries its own `nonce` so re-selecting the same
// station still triggers a fly (object identity alone wouldn't repeat if
// the fields were otherwise unchanged) -- App.tsx increments it on every
// search select.
export interface FlyToRequest {
  stationId: string
  lat: number
  lon: number
  nonce: number
}

interface MapViewProps {
  trains: Map<string, Train>
  hiddenRouteIds: ReadonlySet<string>
  hideGhosts: boolean
  onStationClick: (stationId: string | null) => void
  selectedStationId: string | null
  flyToRequest: FlyToRequest | null
  // null means "no request yet" -- App.tsx increments a counter on every
  // recenter-button click, mirroring flyToRequest's nonce so repeated
  // clicks each re-trigger the effect below.
  recenterRequest: number | null
  theme: Theme
  schedule: StationScheduleState
}

/** Owns the MapLibre instance imperatively -- trains/routes update via
 * `useEffect` calling into `map/mapController.ts` and `map/trainMarkers.ts`,
 * not JSX diffing (see milestones/03b-web-react-design-system.md decision
 * #1: re-rendering ~200 markers through React state on every SSE delta
 * would undo M4's MapLibre-native-transition design). */
export function MapView({
  trains,
  hiddenRouteIds,
  hideGhosts,
  onStationClick,
  selectedStationId,
  flyToRequest,
  recenterRequest,
  theme,
  schedule,
}: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const markerManagerRef = useRef<TrainMarkerManager | null>(null)
  const popupManagerRef = useRef<StationPopupManager | null>(null)
  // Effects below read the latest hiddenRouteIds without re-running the
  // mount effect (which must only run once) when it changes.
  const hiddenRouteIdsRef = useRef(hiddenRouteIds)
  hiddenRouteIdsRef.current = hiddenRouteIds
  // Same reason: registerStationInteractions is wired once, in the mount
  // effect, but must always call the latest onStationClick from props.
  const onStationClickRef = useRef(onStationClick)
  onStationClickRef.current = onStationClick
  // Tracks whichever theme the basemap is CURRENTLY showing, so the style-
  // swap effect below only fires on a genuine change, not on mount (initMap
  // already loads the right style first time) or on unrelated re-renders.
  const appliedThemeRef = useRef(theme)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const map = initMap(container, appliedThemeRef.current)
    mapRef.current = map

    map.on('load', () => {
      resetInitialView(map)
      addGeometryLayers(map, hiddenRouteIdsRef.current)
      registerStationInteractions(map, (stationId) => onStationClickRef.current(stationId))
      markerManagerRef.current = createTrainMarkerManager(map)
      popupManagerRef.current = createStationPopupManager(map)
      setLoaded(true)
    })

    // Explicit teardown so React StrictMode's dev-only double-invoke of
    // effects can't leave two live map instances -- destroy markers before
    // the map they're attached to, then remove the map itself.
    return () => {
      markerManagerRef.current?.destroy()
      markerManagerRef.current = null
      popupManagerRef.current?.destroy()
      popupManagerRef.current = null
      map.remove()
      mapRef.current = null
      setLoaded(false)
    }
  }, [])

  useEffect(() => {
    if (!loaded || !mapRef.current) return
    applyHiddenRoutes(mapRef.current, hiddenRouteIds)
    markerManagerRef.current?.sync(trains, hiddenRouteIds, hideGhosts)
  }, [loaded, hiddenRouteIds, trains, hideGhosts])

  // Popup opens/closes in lockstep with selectedStationId -- same
  // click-same-station-again / click-elsewhere / close-button deselect
  // paths App.tsx already drives the sidebar panel with, see
  // stationPopup.ts's sync() doc comment. Also re-runs when `schedule`
  // changes so the popup updates in place once the async fetch resolves,
  // not just on the initial stationId change.
  useEffect(() => {
    if (!loaded) return
    popupManagerRef.current?.sync(selectedStationId, schedule.data)
  }, [loaded, selectedStationId, schedule.data])

  // Only search selections request a fly (see App.tsx's selectStation vs.
  // handleSearchSelect) -- clicking a station directly shouldn't recentre
  // the camera on itself, the user is already looking right at it.
  useEffect(() => {
    if (!loaded || !mapRef.current || !flyToRequest) return
    flyToStation(mapRef.current, flyToRequest)
  }, [loaded, flyToRequest])

  // Sidebar "recenter map" CTA.
  useEffect(() => {
    if (!loaded || !mapRef.current || recenterRequest === null) return
    flyToDefaultView(mapRef.current)
  }, [loaded, recenterRequest])

  // Basemap follows the app's light/dark theme (M4 Stage 5 follow-up,
  // 2026-07-31). setStyle() wipes style-level sources/layers (route
  // lines, station points), so they're re-added once the new style
  // finishes loading -- markers/popups are untouched, they're plain DOM
  // overlays outside the style entirely.
  useEffect(() => {
    if (!loaded || !mapRef.current || appliedThemeRef.current === theme) return
    appliedThemeRef.current = theme
    const map = mapRef.current
    setMapStyle(map, theme, () => {
      addGeometryLayers(map, hiddenRouteIdsRef.current)
      applyHiddenRoutes(map, hiddenRouteIdsRef.current)
    })
  }, [loaded, theme])

  return (
    <main className={styles.mapContainer}>
      <div ref={containerRef} className={styles.map} />
      <LoadingOverlay visible={!loaded} />
    </main>
  )
}
