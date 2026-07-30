import { useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { MapView } from './components/MapView'
import { useLiveFeed } from './hooks/useLiveFeed'
import styles from './App.module.css'

export function App() {
  const liveState = useLiveFeed()
  const [hiddenRouteIds, setHiddenRouteIds] = useState<ReadonlySet<string>>(() => new Set())

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
      <Sidebar liveState={liveState} hiddenRouteIds={hiddenRouteIds} onToggleRoute={handleToggleRoute} />
      <MapView trains={liveState.trains} hiddenRouteIds={hiddenRouteIds} />
    </div>
  )
}
