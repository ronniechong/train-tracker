import { useEffect, useState } from 'react'

// M4 Stage 5: tokens.css's `data-theme` override hook existed since Stage
// 3b with nothing setting it -- this is that toggle. Persisted explicitly
// once the user picks one; falls back to system preference on first visit
// so a first-time viewer isn't forced into light mode against their OS
// setting.
const STORAGE_KEY = 'traintracker-theme'

export type Theme = 'light' | 'dark'

function initialTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(initialTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  function setTheme(next: Theme): void {
    localStorage.setItem(STORAGE_KEY, next)
    setThemeState(next)
  }

  return [theme, setTheme]
}
