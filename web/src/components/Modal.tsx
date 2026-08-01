import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import { cx } from '../lib/cx'
import styles from './Modal.module.css'

interface ModalProps {
  title: string
  onClose: () => void
  children: ReactNode
}

/** Generic centered dialog over a click-to-dismiss backdrop -- first use is
 * the Announcements CTA (Service Alerts + Weekly Performance, pulled out of
 * the always-visible sidebar since together they were crowding it). Escape
 * and backdrop-click both close, matching the existing drawer backdrop's
 * click-to-dismiss convention (App.module.css's `.drawerBackdrop`) rather
 * than introducing a second dismissal pattern.
 *
 * Rendered via a portal to `document.body`, NOT inline where it's invoked
 * (Sidebar.tsx) -- Sidebar.module.css's mobile rules put a `transform`
 * (translateX, for the drawer slide) on `.sidebar` unconditionally, open or
 * closed. A `transform` on any ancestor makes THAT element the containing
 * block for `position: fixed` descendants instead of the viewport (a real
 * CSS gotcha, not a hypothetical one -- this is exactly why the modal first
 * rendered constrained to the drawer's own ~340px width instead of the full
 * screen). A portal sidesteps the whole class of bug rather than requiring
 * every future caller to remember never to nest this under a transformed
 * ancestor. */
export function Modal({ title, onClose, children }: ModalProps) {
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    // Body scroll lock -- without this, a tall modal body plus a tall page
    // behind it lets the backdrop's own scroll leak through on touch
    // devices, a common modal bug worth heading off even though it's not
    // been hit here yet.
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [onClose])

  return createPortal(
    <div className={styles.backdrop} onClick={onClose}>
      <div
        className={cx(styles.panel)}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <div className={styles.header}>
          <p className={styles.title}>{title}</p>
          <button type="button" className={styles.closeButton} onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className={styles.body}>{children}</div>
      </div>
    </div>,
    document.body,
  )
}
