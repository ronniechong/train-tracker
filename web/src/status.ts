import type { LiveState } from './live'
import type { FeedStatus, Train } from './api-types'

const STATUS_EXPLANATION: Record<Train['status'], string> = {
  live: 'confirmed by the live feed just now.',
  coasting: 'briefly missing from the live feed; still shown from its last known position.',
  ghost: 'not seen in the live feed for a while — position may be stale or scheduled, not live-confirmed.',
}

const CONNECTION_LABEL: Record<LiveState['connection'], string> = {
  connecting: 'Connecting…',
  live: 'Live',
  reconnecting: 'Reconnecting…',
}

function formatFeedName(kebabName: string): string {
  return kebabName
    .split('-')
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ')
}

// Three real states from two real signals -- not fabricated: a feed can be
// fresh (green), fresh but the poller is currently in backoff against the
// upstream API (yellow -- an early warning, not yet a problem for this
// feed specifically), or genuinely stale (red).
function feedDotState(status: FeedStatus, backoffActive: boolean): { class: string; label: string } {
  if (status.stale) return { class: 'status-dot--red', label: 'stale' }
  if (backoffActive) return { class: 'status-dot--yellow', label: 'degraded — poller backing off' }
  return { class: 'status-dot--green', label: 'ok' }
}

function trainCounts(trains: Map<string, Train>): Record<Train['status'], number> {
  const counts: Record<Train['status'], number> = { live: 0, coasting: 0, ghost: 0 }
  for (const train of trains.values()) counts[train.status]++
  return counts
}

export function renderStatus(container: HTMLElement, state: LiveState): void {
  const feedRows = Object.entries(state.feeds)
    .map(([name, status]) => {
      const dot = feedDotState(status, state.backoffActive)
      return `<li><span class="status-dot ${dot.class}" title="${dot.label}"></span>${formatFeedName(name)}</li>`
    })
    .join('')

  const counts = trainCounts(state.trains)

  container.innerHTML = `
    <p class="section-title">Data status</p>
    <p class="status-connection status-connection--${state.connection}">${CONNECTION_LABEL[state.connection]}</p>
    ${state.backoffActive ? '<p class="status-warning">Backoff active — poller throttled</p>' : ''}
    <ul class="status-feed-list">${feedRows}</ul>
    <div class="status-train-counts">
      <span>${counts.live} live</span>
      <span>${counts.coasting} coasting</span>
      <span>${counts.ghost} ghost</span>
      <span class="status-info">
        <span class="status-info-icon">!</span>
        <span class="status-info-tooltip">
          <strong>Live</strong> — ${STATUS_EXPLANATION.live}<br>
          <strong>Coasting</strong> — ${STATUS_EXPLANATION.coasting}<br>
          <strong>Ghost</strong> — ${STATUS_EXPLANATION.ghost}
        </span>
      </span>
    </div>
  `
}
