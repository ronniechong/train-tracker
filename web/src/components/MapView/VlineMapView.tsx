import { useEffect, useRef, useState } from 'react'
import type * as maplibregl from 'maplibre-gl'
import { createMapController } from '../../map/mapController'
import { VLINE_MAP_CONFIG } from '../../map/vlineMapConfig'
import { vlineRoutesById } from '../../vlineGeometry'
import { createTrainMarkerManager, type TrainMarkerManager } from '../../map/trainMarkers'
import { LoadingOverlay } from '../LoadingOverlay'
import type { Train } from '../../api-types'
import type { Theme } from '../../hooks/useTheme'
import styles from './MapView.module.css'

interface VlineMapViewProps {
  trains: Map<string, Train>
  hiddenRouteIds: ReadonlySet<string>
  hideGhosts: boolean
  theme: Theme
  /** Selected from the train list -- reuses trainMarkers.ts's existing
   * "tracked" ring visual purely for highlighting; no camera follow, no
   * click-to-open popup (station click/search/tracking/predictions are
   * all out of scope for V/Line, see the Phase C design brief). */
  highlightedTripId: string | null
  onSelectTrain: (tripId: string) => void
}

/** A leaner sibling of `MapView.tsx`, not a copy of it -- reuses the same
 * generalized `mapController`/`trainMarkers` modules with V/Line's own
 * geometry/config, but drops everything the design brief marks out of
 * scope for V/Line (station click/popup, search fly-to, camera-follow
 * tracking, delay predictions). */
export function VlineMapView({
  trains,
  hiddenRouteIds,
  hideGhosts,
  theme,
  highlightedTripId,
  onSelectTrain,
}: VlineMapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const markerManagerRef = useRef<TrainMarkerManager | null>(null)
  const controllerRef = useRef(createMapController(VLINE_MAP_CONFIG))
  const onSelectTrainRef = useRef(onSelectTrain)
  onSelectTrainRef.current = onSelectTrain
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
      markerManagerRef.current = createTrainMarkerManager(
        map,
        vlineRoutesById,
        (tripId) => onSelectTrainRef.current(tripId),
        () => {},
      )
      setLoaded(true)
    })

    return () => {
      markerManagerRef.current?.destroy()
      markerManagerRef.current = null
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
