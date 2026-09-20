import { Link } from 'react-router'
import { trackEvent } from '../../lib/analytics'
import { cx } from '../../lib/cx'
import styles from './Tabs.module.css'

export interface TabDef {
  id: string
  label: string
  /** When set, this tab is a route link (e.g. the Metro/V/Line nav switcher)
   * instead of an in-place content switch -- `onChange` is never called for
   * it, since navigating away unmounts the whole tab list. */
  to?: string
}

interface TabsProps {
  tabs: readonly TabDef[]
  activeId: string
  onChange: (id: string) => void
  /** `underline` (default) is the existing content-tab look (Announcements
   * modal). `nav` is a segmented-pill switcher for page-level navigation
   * (Metro|V/Line) -- visually distinct so a full page switch doesn't look
   * like just another in-place content tab. */
  variant?: 'underline' | 'nav'
}

/** Tab-button row only -- rendering the active panel's content is left to
 * the caller (first/only use, Sidebar.tsx's Announcements modal, already
 * has each panel as a separate component; a content-slot API here would
 * just be indirection around a plain `activeId === 'x' && <Panel />`
 * the caller already needs anyway). */
export function Tabs({ tabs, activeId, onChange, variant = 'underline' }: TabsProps) {
  const tabClassName = variant === 'nav' ? styles.navTab : styles.tab
  return (
    <div className={cx(styles.tablist, variant === 'nav' && styles.navTablist)} role="tablist">
      {tabs.map((tab) => {
        const selected = tab.id === activeId
        if (tab.to !== undefined) {
          return (
            <Link
              key={tab.id}
              to={tab.to}
              role="tab"
              aria-selected={selected}
              className={cx(tabClassName, selected && styles.active)}
              onClick={() => trackEvent('click-switch-tab', tab.id)}
            >
              {tab.label}
            </Link>
          )
        }
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={selected}
            className={cx(tabClassName, selected && styles.active)}
            onClick={() => {
              if (!selected) {
                trackEvent('click-switch-tab', tab.id)
                onChange(tab.id)
              }
            }}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}
