import { Toggle } from './Toggle'
import type { Theme } from '../hooks/useTheme'
import styles from './Header.module.css'

interface HeaderProps {
  theme: Theme
  onThemeChange: (theme: Theme) => void
}

// Theme state itself lives in App.tsx, not a local useTheme() call here --
// the map's own basemap needs the same value, and two independent hook
// instances would each keep their own unsynced local state.
export function Header({ theme, onThemeChange }: HeaderProps) {
  return (
    <header className={styles.header}>
      <div className={styles.titleRow}>
        <img src="/favicon.svg" alt="" width={28} height={28} />
        <h1 className={styles.title}>Melbourne Train Tracker</h1>
      </div>
      <div className={styles.taglineRow}>
        <p className={styles.tagline}>Melbourne Metro, close to real-time 😅</p>
        {/* A plain wrapper doesn't make Toggle's checkbox clickable -- its
            input is intentionally zero-size (Toggle.module.css), relying
            on a real <label> ancestor for the browser's native implicit
            label-association click behaviour. Every other Toggle usage
            (Legend.tsx, StatusPanel.tsx) already wraps it in a <label>;
            this one needs to too, found live via a test click that
            couldn't hit the input at all. */}
        <label className={styles.themeToggle}>
          <Toggle
            checked={theme === 'dark'}
            onChange={(checked) => onThemeChange(checked ? 'dark' : 'light')}
            aria-label="Dark mode"
            icon={theme === 'dark' ? '🌙' : '☀️'}
          />
        </label>
      </div>
    </header>
  )
}
