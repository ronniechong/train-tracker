import { Header } from './Header'
import { Legend } from './Legend'
import { Search } from './Search'
import { StatusPanel } from './StatusPanel'
import { StationPanel } from './StationPanel'
import { Section, Placeholder } from './Section'
import type { LiveState } from '../hooks/useLiveFeed'
import styles from './Sidebar.module.css'

interface SidebarProps {
  liveState: LiveState
  hiddenRouteIds: ReadonlySet<string>
  onToggleRoute: (routeId: string, visible: boolean) => void
}

export function Sidebar({ liveState, hiddenRouteIds, onToggleRoute }: SidebarProps) {
  return (
    <aside className={styles.sidebar}>
      <Header />
      <Legend hiddenRouteIds={hiddenRouteIds} onToggle={onToggleRoute} />
      <Search />
      <StatusPanel liveState={liveState} />
      <StationPanel />
      <Section>
        <Placeholder>CTA — Stage 5</Placeholder>
      </Section>
      <Section as="footer" className={styles.footer}>
        <Placeholder>Attribution — Stage 5</Placeholder>
      </Section>
    </aside>
  )
}
