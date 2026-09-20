import { Header } from '../Header'
import { VlineLegend } from '../Legend/VlineLegend'
import { StatusPanel } from '../StatusPanel'
import { TrainList } from '../TrainList'
import { Section } from '../Section'
import { useAttribution } from '../../hooks/useAttribution'
import { cx } from '../../lib/cx'
import { METRO_VLINE_NAV_TABS } from '../../lib/vlineNavTabs'
import type { LiveState } from '../../hooks/useLiveFeed'
import type { Theme } from '../../hooks/useTheme'
import styles from './Sidebar.module.css'

// PTV's own official site -- same disclaimer Metro's Sidebar shows, V/Line
// is just as much a portfolio/learning build as Metro is.
const PTV_URL = 'https://transport.vic.gov.au/'
const GITHUB_URL = 'https://github.com/ronniechong/train-tracker'

interface VlineSidebarProps {
  liveState: LiveState
  hiddenRouteIds: ReadonlySet<string>
  onToggleRoute: (routeId: string, visible: boolean) => void
  hideGhosts: boolean
  onToggleHideGhosts: (hide: boolean) => void
  highlightedTripId: string | null
  onSelectTrain: (tripId: string) => void
  theme: Theme
  onThemeChange: (theme: Theme) => void
  /** Only meaningful below the mobile breakpoint -- see Sidebar.module.css.
   * Ignored (sidebar always visible) above it, same as Metro's Sidebar. */
  open: boolean
}

/** V/Line's sidebar reuses Metro's existing shell rather than inventing a
 * new pattern (Phase C design decision, Session 91) -- Header/StatusPanel
 * as-is, a V/Line-specific line-style Legend, and a genuinely new
 * V/Line-only TrainList. Search/StationPanel/Announcements are omitted
 * entirely rather than shown as broken or empty: none of the features they
 * expose (station schedules, search, service alerts, weekly digest) exist
 * for V/Line, so there's nothing for a "not available" state to attach to
 * -- the persistent no-alerts notice below is the one exception, since
 * "V/Line has no live alerts feed" is itself a fact worth stating plainly
 * (R5 in the milestone doc), not a missing feature to hide. */
export function VlineSidebar({
  liveState,
  hiddenRouteIds,
  onToggleRoute,
  hideGhosts,
  onToggleHideGhosts,
  highlightedTripId,
  onSelectTrain,
  theme,
  onThemeChange,
  open,
}: VlineSidebarProps) {
  const attribution = useAttribution()

  return (
    <aside className={cx(styles.sidebar, open && styles.open)}>
      <Header theme={theme} onThemeChange={onThemeChange} tabs={METRO_VLINE_NAV_TABS} activeTabId="vline" />
      <Section>
        <p>No live service alerts for V/Line yet -- this feed isn't wired up for the regional network.</p>
      </Section>
      <VlineLegend hiddenRouteIds={hiddenRouteIds} onToggle={onToggleRoute} />
      <TrainList
        trains={liveState.trains}
        hiddenRouteIds={hiddenRouteIds}
        hideGhosts={hideGhosts}
        highlightedTripId={highlightedTripId}
        onSelectTrain={onSelectTrain}
      />
      <StatusPanel liveState={liveState} hideGhosts={hideGhosts} onToggleHideGhosts={onToggleHideGhosts} grow />
      <Section as="footer" className={styles.footer}>
        {attribution && (
          <p className={styles.attribution}>
            Data: {attribution.source}, licensed{' '}
            <a
              href={attribution.license_url}
              target="_blank"
              rel="noopener noreferrer"
              data-goatcounter-click="click-license-link"
            >
              {attribution.license}
            </a>
            . {attribution.note}
          </p>
        )}
        <p className={styles.disclaimer}>
          Experimental project, not an official transport information source. For live departures
          and disruptions, use the{' '}
          <a href={PTV_URL} target="_blank" rel="noopener noreferrer" data-goatcounter-click="click-ptv-link">
            official PTV website
          </a>
          .
        </p>
        <p className={styles.githubLine}>
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" data-goatcounter-click="click-github-link">
            View source on GitHub
          </a>
        </p>
      </Section>
    </aside>
  )
}
