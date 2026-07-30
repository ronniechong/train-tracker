import { useEffect, useRef, useState } from 'react'
import type * as maplibregl from 'maplibre-gl'
import { addGeometryLayers, applyHiddenRoutes, initMap, resetInitialView } from '../map/mapController'
import { createTrainMarkerManager, type TrainMarkerManager } from '../map/trainMarkers'
import { LoadingOverlay } from './LoadingOverlay'
import type { Train } from '../api-types'
import styles from './MapView.module.css'

interface MapViewProps {
  trains: Map<string, Train>
  hiddenRouteIds: ReadonlySet<string>
}

/** Owns the MapLibre instance imperatively -- trains/routes update via
 * `useEffect` calling into `map/mapController.ts` and `map/trainMarkers.ts`,
 * not JSX diffing (see milestones/03b-web-react-design-system.md decision
 * #1: re-rendering ~200 markers through React state on every SSE delta
 * would undo M4's MapLibre-native-transition design). */
export function MapView({ trains, hiddenRouteIds }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const markerManagerRef = useRef<TrainMarkerManager | null>(null)
  // Effects below read the latest hiddenRouteIds without re-running the
  // mount effect (which must only run once) when it changes.
  const hiddenRouteIdsRef = useRef(hiddenRouteIds)
  hiddenRouteIdsRef.current = hiddenRouteIds
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const map = initMap(container)
    mapRef.current = map

    map.on('load', () => {
      resetInitialView(map)
      addGeometryLayers(map, hiddenRouteIdsRef.current)
      markerManagerRef.current = createTrainMarkerManager(map)
      setLoaded(true)
    })

    // Explicit teardown so React StrictMode's dev-only double-invoke of
    // effects can't leave two live map instances -- destroy markers before
    // the map they're attached to, then remove the map itself.
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
    applyHiddenRoutes(mapRef.current, hiddenRouteIds)
    markerManagerRef.current?.sync(trains, hiddenRouteIds)
  }, [loaded, hiddenRouteIds, trains])

  return (
    <main className={styles.mapContainer}>
      <div ref={containerRef} className={styles.map} />
      <LoadingOverlay visible={!loaded} />
    </main>
  )
}
