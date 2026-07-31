import { useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { MapView, type FlyToRequest } from './components/MapView'
import { useLiveFeed } from './hooks/useLiveFeed'
import { routesByStationId, stationsById, type Station } from './geometry'
import styles from './App.module.css'

export function App() {
  const liveState = useLiveFeed()
  const [hiddenRouteIds, setHiddenRouteIds] = useState<ReadonlySet<string>>(() => new Set())
  // Off by default -- genuine ghosts stay visible, honestly labelled, per
  // Ronnie's call (2026-07-31): hiding them is opt-in, not the default.
  const [hideGhosts, setHideGhosts] = useState(false)
  // Shared by both station-selection entry points (search + map click) --
  // see M4 Stage 4 remainder, 2026-07-31.
  const [selectedStationId, setSelectedStationId] = useState<string | null>(null)
  const [flyToRequest, setFlyToRequest] = useState<FlyToRequest | null>(null)
  const [recenterRequest, setRecenterRequest] = useState<number | null>(null)

  function handleToggleRoute(routeId: string, visible: boolean): void {
    setHiddenRouteIds((prev) => {
      const next = new Set(prev)
      if (visible) {
        next.delete(routeId)
      } else {
        next.add(routeId)
      }
      return next
    })
  }

  // Selects a station and, if any of its lines are currently hidden in the
  // Legend, turns them back on -- otherwise jumping to (or clicking) a
  // station on a toggled-off line lands on what looks like an empty patch
  // of map, with no obvious reason why.
  function selectStation(station: Station): void {
    setSelectedStationId(station.id)
    for (const routeId of routesByStationId.get(station.id) ?? []) {
      if (hiddenRouteIds.has(routeId)) handleToggleRoute(routeId, true)
    }
  }

  // Map click: null means the click missed every station (clear
  // selection); clicking the already-selected station again also clears it,
  // matching Search's own "click elsewhere/close button" deselect paths.
  function handleStationClick(stationId: string | null): void {
    if (stationId === null || stationId === selectedStationId) {
      setSelectedStationId(null)
      return
    }
    const station = stationsById.get(stationId)
    if (station) selectStation(station)
  }

  // Search selection additionally requests a camera fly -- a plain map
  // click doesn't, since the user is already looking at what they clicked.
  function handleSearchSelect(station: Station): void {
    selectStation(station)
    setFlyToRequest((prev) => ({
      stationId: station.id,
      lat: station.lat,
      lon: station.lon,
      nonce: (prev?.nonce ?? 0) + 1,
    }))
  }

  return (
    <div className={styles.shell}>
      <Sidebar
        liveState={liveState}
        hiddenRouteIds={hiddenRouteIds}
        onToggleRoute={handleToggleRoute}
        hideGhosts={hideGhosts}
        onToggleHideGhosts={setHideGhosts}
        onSearchSelect={handleSearchSelect}
        selectedStationId={selectedStationId}
        onClearStation={() => setSelectedStationId(null)}
        onRecenter={() => setRecenterRequest((n) => (n ?? 0) + 1)}
      />
      <MapView
        trains={liveState.trains}
        hiddenRouteIds={hiddenRouteIds}
        hideGhosts={hideGhosts}
        onStationClick={handleStationClick}
        selectedStationId={selectedStationId}
        flyToRequest={flyToRequest}
        recenterRequest={recenterRequest}
      />
    </div>
  )
}
