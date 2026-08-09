import { useState } from 'react'
import { Link } from 'react-router'
import { useFlags } from '@flagsmith/flagsmith/react'
import { Header } from '../Header'
import { Legend } from '../Legend'
import { Search } from '../Search'
import { StatusPanel } from '../StatusPanel'
import { StationPanel } from '../StationPanel'
import { AlertsPanel } from '../AlertsPanel'
import { WeeklyDigestPanel } from '../WeeklyDigestPanel'
import { Modal } from '../Modal'
import { Tabs, type TabDef } from '../Tabs'
import { Section } from '../Section'
import { useAttribution } from '../../hooks/useAttribution'
import { useAlerts } from '../../hooks/useAlerts'
import { cx } from '../../lib/cx'
import { trackEvent } from '../../lib/analytics'
import type { LiveState } from '../../hooks/useLiveFeed'
import type { StationScheduleState } from '../../hooks/useStationSchedule'
import type { Theme } from '../../hooks/useTheme'
import type { Station } from '../../geometry'
import styles from './Sidebar.module.css'

// Public repo -- this project IS the repo, linking to itself is fine.
const GITHUB_URL = 'https://github.com/ronniechong/train-tracker'

// PTV's own official site -- the real-time source of truth for actual
// travel decisions. This project is a portfolio/learning build, not a
// dependable daily tool (see CLAUDE.md's "close to real-time" framing
// throughout) -- Ronnie's explicit ask (2026-07-31) to say so plainly
// rather than let a stranger assume otherwise.
const PTV_URL = 'https://transport.vic.gov.au/'

/** Tabs + tab content for the Announcements modal, split out from Sidebar
 * itself (2026-08-09) so `useAlerts()` mounts only while the modal is open
 * (same fetch-scoping the tab content used to get on its own, before it
 * needed to be mounted just to render a count) -- the "Service alerts (N)"
 * tab label needs the count immediately when the modal opens, before the
 * user has necessarily clicked into that tab, so the hook has to live
 * above the per-tab conditional rendering, not inside AlertsPanel. Weekly
 * performance is the default tab (explicit call, 2026-08-09) -- also
 * sidesteps a modal that opens on an empty tab most of the time if there
 * happen to be no active alerts. */
function AnnouncementsBody() {
  const [activeTab, setActiveTab] = useState('weekly')
  const { alerts, loading, error } = useAlerts()

  const tabs: TabDef[] = [
    { id: 'weekly', label: 'Weekly performance' },
    { id: 'alerts', label: alerts.length > 0 ? `Service alerts (${alerts.length})` : 'Service alerts' },
  ]

  return (
    <>
      <Tabs tabs={tabs} activeId={activeTab} onChange={setActiveTab} />
      {activeTab === 'weekly' && (
        <>
          <WeeklyDigestPanel />
          <p className={styles.fineprint}>
            Not sourced from PTV. On-time % is calculated by this project from Victoria's GTFS-Realtime feed,
            using PTV's own public punctuality definition (arrival within 4:59 of scheduled time at the
            terminus); cancelled trips count as not on time.
          </p>
        </>
      )}
      {activeTab === 'alerts' && (
        <>
          <AlertsPanel alerts={alerts} loading={loading} error={error} />
          <p className={styles.fineprint}>
            Alerts are pulled directly from Victoria's GTFS-Realtime Service Alerts feed, the same source PTV's
            own apps use — shown here as published, not edited or filtered.
          </p>
        </>
      )}
    </>
  )
}

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
  /** Closes the mobile off-canvas drawer -- same purpose as `onRecenter`'s
   * own inline close, needed here so opening the Announcements modal (which
   * sits behind the drawer in stacking order on mobile, see Modal.module.css)
   * doesn't leave it hidden under a still-open drawer. */
  onCloseDrawer: () => void
  /** Only meaningful below the mobile breakpoint -- see Sidebar.module.css.
   * Ignored (sidebar always visible) above it. */
  open: boolean
  theme: Theme
  onThemeChange: (theme: Theme) => void
  schedule: StationScheduleState
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
  onCloseDrawer,
  open,
  theme,
  onThemeChange,
  schedule,
}: SidebarProps) {
  const attribution = useAttribution()
  // Service Alerts + Weekly Performance were crowding the always-visible
  // sidebar -- moved behind this CTA into a modal instead of removing them,
  // since both are still real content, just lower-cadence than the live
  // map/station panels that should own the default view.
  const [announcementsOpen, setAnnouncementsOpen] = useState(false)
  // M8 Insights nav entry -- gated on the real Flagsmith flag key
  // (`train-insghts`, Ronnie's own typo in the dashboard, used verbatim
  // since the SDK looks up the exact string). Off in Production until
  // the 2-week data-maturity gate is met; on in Development throughout
  // the build (locked 2026-08-04, milestones/08-analytics-insights.md).
  const insightsFlag = useFlags(['train-insghts'])

  return (
    <aside className={cx(styles.sidebar, open && styles.open)}>
      <Header theme={theme} onThemeChange={onThemeChange} />
      <Legend hiddenRouteIds={hiddenRouteIds} onToggle={onToggleRoute} />
      <Search onSelect={onSearchSelect} />
      <Section>
        <button
          type="button"
          className={styles.recenterButton}
          onClick={() => {
            trackEvent('click-recenter-map')
            onRecenter()
          }}
        >
          Recenter map
        </button>
        <button
          type="button"
          className={styles.announcementsButton}
          onClick={() => {
            trackEvent('click-open-announcements')
            setAnnouncementsOpen(true)
            onCloseDrawer()
          }}
        >
          Announcements
        </button>
        {insightsFlag['train-insghts']?.enabled && (
          <Link
            to="/insights"
            className={styles.announcementsButton}
            data-goatcounter-click="click-open-insights"
            onClick={onCloseDrawer}
          >
            Insights
          </Link>
        )}
      </Section>
      {announcementsOpen && (
        <Modal title="Announcements" onClose={() => setAnnouncementsOpen(false)}>
          <AnnouncementsBody />
        </Modal>
      )}
      <StationPanel
        stationId={selectedStationId}
        trains={liveState.trains}
        hideGhosts={hideGhosts}
        onClear={onClearStation}
        schedule={schedule}
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
