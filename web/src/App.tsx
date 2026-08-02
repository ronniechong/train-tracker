import { useState } from 'react'
import { Sidebar } from './components/Sidebar/Sidebar'
import { MapView, type FlyToRequest } from './components/MapView/MapView'
import { DrawerToggle } from './components/DrawerToggle/DrawerToggle'
import { useLiveFeed } from './hooks/useLiveFeed'
import { useStationSchedule } from './hooks/useStationSchedule'
import { useTheme } from './hooks/useTheme'
import { routesByStationId, stationsById, type Station } from './geometry'
import { trackEvent, trackStationSelect } from './lib/analytics'
import styles from './App.module.css'

export function App() {
  const liveState = useLiveFeed()
  // Lifted up (not called separately in Header/MapView) so both the app
  // chrome (via data-theme, CSS tokens) and the map's own basemap swap
  // stay in sync off one shared value -- two independent useTheme() calls
  // would each keep their own local state, unaware of the other toggling.
  const [theme, setTheme] = useTheme()
  const [hiddenRouteIds, setHiddenRouteIds] = useState<ReadonlySet<string>>(() => new Set())
  // On by default (reversed 2026-07-31, Session 26 -- was off/opt-in at
  // Session 24) -- still a real toggle, not a removal: ghosts stay fully
  // available and honestly labelled, just not shown until asked for.
  const [hideGhosts, setHideGhosts] = useState(true)
  // Shared by both station-selection entry points (search + map click) --
  // see M4 Stage 4 remainder, 2026-07-31.
  const [selectedStationId, setSelectedStationId] = useState<string | null>(null)
  // Lifted here (not called separately in Sidebar/MapView) for the same
  // reason as theme above: StationPanel (sidebar) and the on-map popup
  // both need the SAME schedule data for whichever station is selected.
  const schedule = useStationSchedule(selectedStationId)
  const [flyToRequest, setFlyToRequest] = useState<FlyToRequest | null>(null)
  const [recenterRequest, setRecenterRequest] = useState<number | null>(null)
  // M4 Stage 5: mobile off-canvas drawer. Harmless to leave true above the
  // breakpoint -- Sidebar.module.css's `.open` rule only exists inside a
  // `max-width: 768px` media query, so this has zero visual effect on
  // desktop regardless of its value.
  const [drawerOpen, setDrawerOpen] = useState(false)

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
    // Selecting a station while the mobile drawer is open would otherwise
    // leave the result hidden behind it -- close so the map (and the fly/
    // panel update) is actually visible.
    setDrawerOpen(false)
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
    if (station) {
      trackStationSelect(station.name, 'map')
      selectStation(station)
    }
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
      <DrawerToggle open={drawerOpen} onToggle={() => setDrawerOpen((prev) => !prev)} />
      {drawerOpen && (
        <div
          className={styles.drawerBackdrop}
          onClick={() => {
            trackEvent('click-dismiss-drawer-backdrop')
            setDrawerOpen(false)
          }}
        />
      )}
      <Sidebar
        liveState={liveState}
        hiddenRouteIds={hiddenRouteIds}
        onToggleRoute={handleToggleRoute}
        hideGhosts={hideGhosts}
        onToggleHideGhosts={setHideGhosts}
        onSearchSelect={handleSearchSelect}
        selectedStationId={selectedStationId}
        onClearStation={() => setSelectedStationId(null)}
        onRecenter={() => {
          setRecenterRequest((n) => (n ?? 0) + 1)
          setDrawerOpen(false)
        }}
        onCloseDrawer={() => setDrawerOpen(false)}
        open={drawerOpen}
        theme={theme}
        onThemeChange={setTheme}
        schedule={schedule}
      />
      <MapView
        trains={liveState.trains}
        hiddenRouteIds={hiddenRouteIds}
        hideGhosts={hideGhosts}
        onStationClick={handleStationClick}
        selectedStationId={selectedStationId}
        flyToRequest={flyToRequest}
        recenterRequest={recenterRequest}
        theme={theme}
        schedule={schedule}
      />
    </div>
  )
}
