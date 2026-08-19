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
import { createTrainPopupManager, type TrainPopupManager } from '../../map/trainPopup'
import { LoadingOverlay } from '../LoadingOverlay'
import type { Train } from '../../api-types'
import type { StationScheduleState } from '../../hooks/useStationSchedule'
import type { DelayPredictionState } from '../../hooks/useDelayPredictions'
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
  // Train tracking (M12 follow-up): trackedTripId is the currently
  // tracked trip, or null if nothing is tracked. isFollowing is whether
  // the camera should keep re-centering on it right now -- distinct from
  // "is a train tracked" because a manual pan/zoom keeps the train
  // tracked but pauses following until the user hits the resume CTA.
  trackedTripId: string | null
  isFollowing: boolean
  clickedTrainId: string | null
  onTrainClick: (tripId: string) => void
  onTrainRemoved: (tripId: string) => void
  onToggleTrack: (tripId: string) => void
  onUserMapInteraction: () => void
  onResumeTracking: () => void
  // "Am I late?" (M5): per-trip prediction state, keyed by trip_id.
  // onRequestDelayPrediction fires the on-demand fetch for one trip --
  // see hooks/useDelayPredictions.ts.
  delayPredictions: ReadonlyMap<string, DelayPredictionState>
  onRequestDelayPrediction: (tripId: string) => void
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
  trackedTripId,
  isFollowing,
  clickedTrainId,
  onTrainClick,
  onTrainRemoved,
  onToggleTrack,
  onUserMapInteraction,
  onResumeTracking,
  delayPredictions,
  onRequestDelayPrediction,
}: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const markerManagerRef = useRef<TrainMarkerManager | null>(null)
  const popupManagerRef = useRef<StationPopupManager | null>(null)
  const trainPopupManagerRef = useRef<TrainPopupManager | null>(null)
  // Effects below read the latest hiddenRouteIds without re-running the
  // mount effect (which must only run once) when it changes.
  const hiddenRouteIdsRef = useRef(hiddenRouteIds)
  hiddenRouteIdsRef.current = hiddenRouteIds
  // Same reason: registerStationInteractions is wired once, in the mount
  // effect, but must always call the latest onStationClick from props.
  const onStationClickRef = useRef(onStationClick)
  onStationClickRef.current = onStationClick
  // Same latest-callback-ref reason: wired once in the mount effect
  // (marker manager creation, dragstart/zoomstart listeners) but must
  // always call the latest prop.
  const onTrainClickRef = useRef(onTrainClick)
  onTrainClickRef.current = onTrainClick
  const onTrainRemovedRef = useRef(onTrainRemoved)
  onTrainRemovedRef.current = onTrainRemoved
  const onUserMapInteractionRef = useRef(onUserMapInteraction)
  onUserMapInteractionRef.current = onUserMapInteraction
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
      markerManagerRef.current = createTrainMarkerManager(
        map,
        (tripId) => onTrainClickRef.current(tripId),
        (tripId) => onTrainRemovedRef.current(tripId),
      )
      popupManagerRef.current = createStationPopupManager(map)
      trainPopupManagerRef.current = createTrainPopupManager(map)
      // `dragstart` only ever fires from a real user drag gesture, never
      // programmatically -- safe from the follow-camera `easeTo` below
      // (pan-only, no zoom) canceling itself. `zoomstart` fires for BOTH
      // user gestures AND programmatic zoom changes (flyToStation,
      // flyToDefaultView) -- deliberately left listening to both here: the
      // follow camera itself never changes zoom, so it can't self-trigger
      // this, and a search-fly/recenter-button navigating the camera
      // elsewhere is exactly the kind of "user went and looked at
      // something else" that should also pause tracking.
      map.on('dragstart', () => onUserMapInteractionRef.current())
      map.on('zoomstart', () => onUserMapInteractionRef.current())
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
      trainPopupManagerRef.current?.destroy()
      trainPopupManagerRef.current = null
      map.remove()
      mapRef.current = null
      setLoaded(false)
    }
  }, [])

  useEffect(() => {
    if (!loaded || !mapRef.current) return
    applyHiddenRoutes(mapRef.current, hiddenRouteIds)
    markerManagerRef.current?.sync(trains, hiddenRouteIds, hideGhosts, trackedTripId, delayPredictions)
  }, [loaded, hiddenRouteIds, trains, hideGhosts, trackedTripId, delayPredictions])

  // Track/untrack click popup -- same open/close-in-lockstep pattern as
  // the station popup above, driven by App.tsx's clickedTrainId rather
  // than a click event handled locally here.
  useEffect(() => {
    if (!loaded) return
    const train = clickedTrainId ? (trains.get(clickedTrainId) ?? null) : null
    trainPopupManagerRef.current?.sync(
      clickedTrainId, train, clickedTrainId === trackedTripId,
      () => {
        if (clickedTrainId) onToggleTrack(clickedTrainId)
      },
      () => {
        if (clickedTrainId) onRequestDelayPrediction(clickedTrainId)
      },
    )
  }, [loaded, clickedTrainId, trains, trackedTripId, onToggleTrack, onRequestDelayPrediction])

  // Sat-nav-style camera follow: re-centers on the tracked train every time
  // its position updates, as long as isFollowing hasn't been paused by a
  // manual pan/zoom (see the dragstart/zoomstart listeners above). Pan
  // only, no zoom argument -- keeps the user's chosen zoom level and,
  // just as importantly, avoids firing `zoomstart` itself (which would
  // immediately cancel the very follow it just performed).
  useEffect(() => {
    if (!loaded || !mapRef.current || !trackedTripId || !isFollowing) return
    const train = trains.get(trackedTripId)
    if (!train || train.latitude === null || train.longitude === null) return
    mapRef.current.easeTo({ center: [train.longitude, train.latitude], duration: 400 })
  }, [loaded, trackedTripId, isFollowing, trains])

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
      {trackedTripId && !isFollowing && (
        <button type="button" className={styles.resumeTrackingButton} onClick={onResumeTracking}>
          Recenter &amp; resume tracking
        </button>
      )}
    </main>
  )
}
