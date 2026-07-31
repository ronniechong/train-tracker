import { Header } from './Header'
import { Legend } from './Legend'
import { Search } from './Search'
import { StatusPanel } from './StatusPanel'
import { StationPanel } from './StationPanel'
import { Section, Placeholder } from './Section'
import type { LiveState } from '../hooks/useLiveFeed'
import type { Station } from '../geometry'
import styles from './Sidebar.module.css'

interface SidebarProps {
  liveState: LiveState
  hiddenRouteIds: ReadonlySet<string>
  onToggleRoute: (routeId: string, visible: boolean) => void
  hideGhosts: boolean
  onToggleHideGhosts: (hide: boolean) => void
  onSearchSelect: (station: Station) => void
  selectedStationId: string | null
  onClearStation: () => void
  onRecenter: () => void
}

export function Sidebar({
  liveState,
  hiddenRouteIds,
  onToggleRoute,
  hideGhosts,
  onToggleHideGhosts,
  onSearchSelect,
  selectedStationId,
  onClearStation,
  onRecenter,
}: SidebarProps) {
  return (
    <aside className={styles.sidebar}>
      <Header />
      <Legend hiddenRouteIds={hiddenRouteIds} onToggle={onToggleRoute} />
      <Search onSelect={onSearchSelect} />
      <Section>
        <button type="button" className={styles.recenterButton} onClick={onRecenter}>
          Recenter map
        </button>
      </Section>
      <StatusPanel liveState={liveState} hideGhosts={hideGhosts} onToggleHideGhosts={onToggleHideGhosts} />
      <StationPanel
        stationId={selectedStationId}
        trains={liveState.trains}
        hideGhosts={hideGhosts}
        onClear={onClearStation}
      />
      <Section>
        <Placeholder>CTA — Stage 5</Placeholder>
      </Section>
      <Section as="footer" className={styles.footer}>
        <Placeholder>Attribution — Stage 5</Placeholder>
      </Section>
    </aside>
  )
}
