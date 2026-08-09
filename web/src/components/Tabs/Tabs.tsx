import { trackEvent } from '../../lib/analytics'
import { cx } from '../../lib/cx'
import styles from './Tabs.module.css'

export interface TabDef {
  id: string
  label: string
}

interface TabsProps {
  tabs: readonly TabDef[]
  activeId: string
  onChange: (id: string) => void
}

/** Tab-button row only -- rendering the active panel's content is left to
 * the caller (first/only use, Sidebar.tsx's Announcements modal, already
 * has each panel as a separate component; a content-slot API here would
 * just be indirection around a plain `activeId === 'x' && <Panel />`
 * the caller already needs anyway). */
export function Tabs({ tabs, activeId, onChange }: TabsProps) {
  return (
    <div className={styles.tablist} role="tablist">
      {tabs.map((tab) => {
        const selected = tab.id === activeId
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={selected}
            className={cx(styles.tab, selected && styles.active)}
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
