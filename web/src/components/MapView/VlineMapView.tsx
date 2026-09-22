import { useEffect, useRef, useState } from 'react'
import type * as maplibregl from 'maplibre-gl'
import { createMapController } from '../../map/mapController'
import { VLINE_MAP_CONFIG } from '../../map/vlineMapConfig'
import { vlineRoutesById, vlineRoutesByStationId, vlineStationsById } from '../../vlineGeometry'
import { createTrainMarkerManager, type TrainMarkerManager } from '../../map/trainMarkers'
import { createTrainPopupManager, type TrainPopupManager } from '../../map/trainPopup'
import { createStationPopupManager, type StationPopupManager } from '../../map/stationPopup'
import { LoadingOverlay } from '../LoadingOverlay'
import type { Train } from '../../api-types'
import type { StationScheduleState } from '../../hooks/useStationSchedule'
import type { Theme } from '../../hooks/useTheme'
import styles from './MapView.module.css'

interface VlineMapViewProps {
  trains: Map<string, Train>
  hiddenRouteIds: ReadonlySet<string>
  hideGhosts: boolean
  theme: Theme
  /** Selected from the train list -- reuses trainMarkers.ts's existing
   * "tracked" ring visual purely for highlighting, and also drives the
   * click info popup (trainPopup.ts, no Track/"Am I late?" buttons -- those
   * have no V/Line backend equivalent). No camera follow (see the Phase C
   * design brief's out-of-scope list). */
  highlightedTripId: string | null
  onSelectTrain: (tripId: string) => void
  onStationClick: (stationId: string | null) => void
  // null means "no request yet" -- same nonce-via-counter pattern as
  // Metro's own MapView.tsx.
  recenterRequest: number | null
  /** Drives the on-map station popup, same lockstep-with-the-sidebar
   * pattern as Metro's MapView.tsx (M15). */
  selectedStationId: string | null
  schedule: StationScheduleState
}

/** A leaner sibling of `MapView.tsx`, not a copy of it -- reuses the same
 * generalized `mapController`/`trainMarkers`/`trainPopup`/`stationPopup`
 * modules with V/Line's own geometry/config, but drops what the design
 * brief marks genuinely out of scope for V/Line (search fly-to,
 * camera-follow tracking, delay predictions -- none have a V/Line backend
 * equivalent). Station schedule DOES have full parity with Metro (M15). */
export function VlineMapView({
  trains,
  hiddenRouteIds,
  hideGhosts,
  theme,
  highlightedTripId,
  onSelectTrain,
  onStationClick,
  recenterRequest,
  selectedStationId,
  schedule,
}: VlineMapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const markerManagerRef = useRef<TrainMarkerManager | null>(null)
  const trainPopupManagerRef = useRef<TrainPopupManager | null>(null)
  const stationPopupManagerRef = useRef<StationPopupManager | null>(null)
  const controllerRef = useRef(createMapController(VLINE_MAP_CONFIG))
  const onSelectTrainRef = useRef(onSelectTrain)
  onSelectTrainRef.current = onSelectTrain
  const onStationClickRef = useRef(onStationClick)
  onStationClickRef.current = onStationClick
  // Read by the mount effect (must only run once) without re-running it --
  // same pattern as MapView.tsx.
  const hiddenRouteIdsRef = useRef(hiddenRouteIds)
  hiddenRouteIdsRef.current = hiddenRouteIds
  const appliedThemeRef = useRef(theme)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const controller = controllerRef.current
    const map = controller.initMap(container, appliedThemeRef.current)
    mapRef.current = map

    map.on('load', () => {
      controller.resetInitialView(map)
      controller.addGeometryLayers(map, hiddenRouteIdsRef.current)
      controller.registerStationInteractions(map, (stationId) => onStationClickRef.current(stationId))
      markerManagerRef.current = createTrainMarkerManager(
        map,
        vlineRoutesById,
        (tripId) => onSelectTrainRef.current(tripId),
        () => {},
      )
      trainPopupManagerRef.current = createTrainPopupManager(map, vlineRoutesById)
      stationPopupManagerRef.current = createStationPopupManager(
        map,
        vlineStationsById,
        vlineRoutesById,
        vlineRoutesByStationId,
      )
      setLoaded(true)
    })

    return () => {
      markerManagerRef.current?.destroy()
      markerManagerRef.current = null
      trainPopupManagerRef.current?.destroy()
      trainPopupManagerRef.current = null
      stationPopupManagerRef.current?.destroy()
      stationPopupManagerRef.current = null
      map.remove()
      mapRef.current = null
      setLoaded(false)
    }
  }, [])

  useEffect(() => {
    if (!loaded || !mapRef.current) return
    controllerRef.current.applyHiddenRoutes(mapRef.current, hiddenRouteIds)
    markerManagerRef.current?.sync(trains, hiddenRouteIds, hideGhosts, highlightedTripId)
  }, [loaded, hiddenRouteIds, trains, hideGhosts, highlightedTripId])

  // Click info popup -- driven by highlightedTripId (set by either a map
  // marker click or a train-list row click, see VlinePage's shared
  // onSelectTrain), same open/close-in-lockstep pattern as Metro's own
  // clickedTrainId-driven popup, minus the Track/"Am I late?" actions.
  useEffect(() => {
    if (!loaded) return
    const train = highlightedTripId ? (trains.get(highlightedTripId) ?? null) : null
    trainPopupManagerRef.current?.sync(highlightedTripId, train, false, undefined, undefined, undefined)
  }, [loaded, highlightedTripId, trains])

  // Popup opens/closes in lockstep with selectedStationId, same pattern as
  // Metro's MapView.tsx -- see stationPopup.ts's sync() doc comment.
  useEffect(() => {
    if (!loaded) return
    stationPopupManagerRef.current?.sync(selectedStationId, schedule.data)
  }, [loaded, selectedStationId, schedule.data])

  // Sidebar "recenter map" CTA -- same pattern as Metro's MapView.tsx.
  useEffect(() => {
    if (!loaded || !mapRef.current || recenterRequest === null) return
    controllerRef.current.flyToDefaultView(mapRef.current)
  }, [loaded, recenterRequest])

  // Basemap follows the app's light/dark theme -- same pattern as
  // MapView.tsx's own theme-swap effect.
  useEffect(() => {
    if (!loaded || !mapRef.current || appliedThemeRef.current === theme) return
    appliedThemeRef.current = theme
    const map = mapRef.current
    const controller = controllerRef.current
    controller.setMapStyle(map, theme, () => {
      controller.addGeometryLayers(map, hiddenRouteIds)
    })
  }, [loaded, theme, hiddenRouteIds])

  return (
    <main className={styles.mapContainer}>
      <div ref={containerRef} className={styles.map} />
      <LoadingOverlay visible={!loaded} />
    </main>
  )
}
