import { Header } from './Header'
import { Legend } from './Legend'
import { Search } from './Search'
import { StatusPanel } from './StatusPanel'
import { StationPanel } from './StationPanel'
import { Section } from './Section'
import { useAttribution } from '../hooks/useAttribution'
import { cx } from '../lib/cx'
import type { LiveState } from '../hooks/useLiveFeed'
import type { Theme } from '../hooks/useTheme'
import type { Station } from '../geometry'
import styles from './Sidebar.module.css'

// Public repo -- this project IS the repo, linking to itself is fine.
const GITHUB_URL = 'https://github.com/ronniechong/train-tracker'

// PTV's own official site -- the real-time source of truth for actual
// travel decisions. This project is a portfolio/learning build, not a
// dependable daily tool (see CLAUDE.md's "close to real-time" framing
// throughout) -- Ronnie's explicit ask (2026-07-31) to say so plainly
// rather than let a stranger assume otherwise.
const PTV_URL = 'https://transport.vic.gov.au/'

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
  /** Only meaningful below the mobile breakpoint -- see Sidebar.module.css.
   * Ignored (sidebar always visible) above it. */
  open: boolean
  theme: Theme
  onThemeChange: (theme: Theme) => void
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
  open,
  theme,
  onThemeChange,
}: SidebarProps) {
  const attribution = useAttribution()

  return (
    <aside className={cx(styles.sidebar, open && styles.open)}>
      <Header theme={theme} onThemeChange={onThemeChange} />
      <Legend hiddenRouteIds={hiddenRouteIds} onToggle={onToggleRoute} />
      <Search onSelect={onSearchSelect} />
      <Section>
        <button type="button" className={styles.recenterButton} onClick={onRecenter}>
          Recenter map
        </button>
      </Section>
      <StationPanel
        stationId={selectedStationId}
        trains={liveState.trains}
        hideGhosts={hideGhosts}
        onClear={onClearStation}
      />
      <StatusPanel liveState={liveState} hideGhosts={hideGhosts} onToggleHideGhosts={onToggleHideGhosts} grow />
      <Section as="footer" className={styles.footer}>
        {attribution && (
          <p className={styles.attribution}>
            Data: {attribution.source}, licensed{' '}
            <a href={attribution.license_url} target="_blank" rel="noopener noreferrer">
              {attribution.license}
            </a>
            . {attribution.note}
          </p>
        )}
        <p className={styles.disclaimer}>
          Experimental project, not an official transport information source. For live departures
          and disruptions, use the{' '}
          <a href={PTV_URL} target="_blank" rel="noopener noreferrer">
            official PTV website
          </a>
          .
        </p>
        <p className={styles.githubLine}>
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            View source on GitHub
          </a>
        </p>
      </Section>
    </aside>
  )
}
