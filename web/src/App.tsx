import { useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { MapView } from './components/MapView'
import { useLiveFeed } from './hooks/useLiveFeed'
import styles from './App.module.css'

export function App() {
  const liveState = useLiveFeed()
  const [hiddenRouteIds, setHiddenRouteIds] = useState<ReadonlySet<string>>(() => new Set())
  // Off by default -- genuine ghosts stay visible, honestly labelled, per
  // Ronnie's call (2026-07-31): hiding them is opt-in, not the default.
  const [hideGhosts, setHideGhosts] = useState(false)

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

  return (
    <div className={styles.shell}>
      <Sidebar
        liveState={liveState}
        hiddenRouteIds={hiddenRouteIds}
        onToggleRoute={handleToggleRoute}
        hideGhosts={hideGhosts}
        onToggleHideGhosts={setHideGhosts}
      />
      <MapView trains={liveState.trains} hiddenRouteIds={hiddenRouteIds} hideGhosts={hideGhosts} />
    </div>
  )
}
