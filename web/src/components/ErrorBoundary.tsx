import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

// A lazy route chunk (InsightsPage/VlinePage) 404s if a tab has been open
// since before a deploy replaced the old hashed filenames -- not a real
// bug, just a stale reference. One auto-reload picks up the current
// index.html/chunk hashes; the sessionStorage guard stops a genuine,
// persistent failure from reload-looping forever.
const CHUNK_RELOAD_KEY = 'chunk-load-reload-attempted'

function isChunkLoadError(error: Error): boolean {
  return /Failed to fetch dynamically imported module|error loading dynamically imported module/i.test(
    error.message,
  )
}

// A route-level crash (e.g. a bug on /insights) shouldn't blank the whole
// app to a silent white screen -- catches it, logs it loudly (React 19's
// createRoot alone still swallows some render errors from view in prod
// builds), and shows the message so it's actually debuggable.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Uncaught render error:', error, info.componentStack)
    if (isChunkLoadError(error) && !sessionStorage.getItem(CHUNK_RELOAD_KEY)) {
      sessionStorage.setItem(CHUNK_RELOAD_KEY, '1')
      window.location.reload()
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: '2rem', fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
          <h1>Something broke</h1>
          <p>{this.state.error.message}</p>
          <pre>{this.state.error.stack}</pre>
        </div>
      )
    }
    return this.props.children
  }
}
