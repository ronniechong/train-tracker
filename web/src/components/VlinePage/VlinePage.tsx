import { useState } from 'react'
import { Navigate } from 'react-router'
import { VlineSidebar } from '../Sidebar/VlineSidebar'
import { VlineMapView } from '../MapView/VlineMapView'
import { DrawerToggle } from '../DrawerToggle/DrawerToggle'
import { useLiveFeed } from '../../hooks/useLiveFeed'
import { useStationSchedule } from '../../hooks/useStationSchedule'
import { useTheme } from '../../hooks/useTheme'
import { useVlineRouteGate } from '../../hooks/useVlineFeature'
import { trackEvent, trackVlineStationSelect } from '../../lib/analytics'
import { vlineStationsById } from '../../vlineGeometry'
import styles from '../../App.module.css'

/** Separate route, separate component tree, separate `useLiveFeed`
 * connection -- not a toggle/flag inside Metro's `App` state: the backend
 * runs V/Line as an isolated process so a fault there can't touch Metro;
 * folding both into one frontend state tree would quietly recouple them
 * on the client side. Trimmed relative to `App.tsx`: no station search,
 * no train tracking, no delay predictions -- none of those have a V/Line
 * backend equivalent. Station click DOES have full schedule parity with
 * Metro -- `PinnedScheduleCache` is mode-agnostic. */
export function VlinePage() {
  const routeGate = useVlineRouteGate()
  const liveState = useLiveFeed('/api/vline', routeGate === 'enabled')
  const [theme, setTheme] = useTheme()
  const [hiddenRouteIds, setHiddenRouteIds] = useState<ReadonlySet<string>>(() => new Set())
  const [hideGhosts, setHideGhosts] = useState(true)
  const [highlightedTripId, setHighlightedTripId] = useState<string | null>(null)
  const [selectedStationId, setSelectedStationId] = useState<string | null>(null)
  const [recenterRequest, setRecenterRequest] = useState<number | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  // Lifted here (not called separately in VlineSidebar/VlineMapView), same
  // reason as App.tsx's own `schedule` -- the sidebar panel and the on-map
  // popup both need the SAME schedule data for whichever station is
  // selected.
  const schedule = useStationSchedule(selectedStationId, '/api/vline')

  // Route-level gate, not just a hidden nav entry -- see useVlineRouteGate.
  if (routeGate === 'loading') return null
  if (routeGate === 'disabled') return <Navigate to="/" replace />

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

  function handleSelectTrain(tripId: string): void {
    setHighlightedTripId((prev) => (prev === tripId ? null : tripId))
  }

  // Same click-same-station-again / click-elsewhere toggle as Metro's own
  // handleStationClick (App.tsx).
  function handleStationClick(stationId: string | null): void {
    setSelectedStationId((prev) => {
      if (stationId === null || stationId === prev) return null
      const station = vlineStationsById.get(stationId)
      if (station) trackVlineStationSelect(station.name)
      return stationId
    })
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
      <VlineSidebar
        liveState={liveState}
        hiddenRouteIds={hiddenRouteIds}
        onToggleRoute={handleToggleRoute}
        hideGhosts={hideGhosts}
        onToggleHideGhosts={setHideGhosts}
        highlightedTripId={highlightedTripId}
        onSelectTrain={handleSelectTrain}
        selectedStationId={selectedStationId}
        onClearStation={() => setSelectedStationId(null)}
        onRecenter={() => setRecenterRequest((n) => (n ?? 0) + 1)}
        theme={theme}
        onThemeChange={setTheme}
        open={drawerOpen}
        schedule={schedule}
      />
      <VlineMapView
        trains={liveState.trains}
        hiddenRouteIds={hiddenRouteIds}
        hideGhosts={hideGhosts}
        theme={theme}
        highlightedTripId={highlightedTripId}
        onSelectTrain={handleSelectTrain}
        onStationClick={handleStationClick}
        recenterRequest={recenterRequest}
        selectedStationId={selectedStationId}
        schedule={schedule}
      />
    </div>
  )
}
