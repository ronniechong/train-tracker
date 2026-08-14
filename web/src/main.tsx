import { StrictMode, Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router'
import { FlagsmithProvider } from '@flagsmith/flagsmith/react'
import '@fontsource-variable/inter/wght.css'
import './styles/tokens.css'
import './styles/global.css'
import { App } from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { flagsmith, flagsmithOptions } from './lib/flags'

// Lazy-loaded: Recharts (and its d3 dependencies) would otherwise ship in
// the main bundle every visitor downloads just to see the live map.
const InsightsPage = lazy(() =>
  import('./components/Insights/InsightsPage').then((m) => ({ default: m.InsightsPage })),
)

// GitHub Pages has no server-side rewrite for a client-side router: a direct
// request or refresh against a deep route (e.g. /insights) hits 404.html,
// which re-encodes the path into a `redirect` query param and sends the
// browser back here. Restore it before the router reads location, so a
// refresh lands on the same route instead of the map.
const redirect = new URLSearchParams(window.location.search).get('redirect')
if (redirect) {
  window.history.replaceState(null, '', redirect)
}

const container = document.getElementById('app')
if (!container) throw new Error('#app root element not found')

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <FlagsmithProvider flagsmith={flagsmith} options={flagsmithOptions}>
        {/* basename from Vite's own configured base (`/train-tracker/` in
            production, `/` in dev) -- see vite.config.ts's `base` comment;
            keeps the router in sync with wherever the app is actually
            served from instead of hardcoding the GitHub Pages subpath. */}
        <BrowserRouter basename={import.meta.env.BASE_URL}>
          <Routes>
            <Route path="/" element={<App />} />
            <Route
              path="/insights"
              element={
                <Suspense fallback={null}>
                  <InsightsPage />
                </Suspense>
              }
            />
          </Routes>
        </BrowserRouter>
      </FlagsmithProvider>
    </ErrorBoundary>
  </StrictMode>,
)
