import styles from './DrawerToggle.module.css'

interface DrawerToggleProps {
  open: boolean
  onToggle: () => void
}

/** Floating button over the map, mobile-only (see .module.css) -- opens/
 * closes the sidebar drawer. Morphs between hamburger and close glyphs so
 * one button covers both directions rather than needing a separate close
 * affordance inside the drawer itself. */
export function DrawerToggle({ open, onToggle }: DrawerToggleProps) {
  return (
    <button
      type="button"
      className={styles.toggle}
      onClick={onToggle}
      aria-label={open ? 'Close menu' : 'Open menu'}
      aria-expanded={open}
    >
      {open ? '×' : '☰'}
    </button>
  )
}
