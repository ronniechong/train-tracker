import type * as maplibregl from 'maplibre-gl'
import { geometry, type Route } from './geometry'
import { setRouteVisible } from './map'

// Groups lines by color family to match PTV's own published line-color
// spec sheet (Ronnie's reference, 2026-07-30), City Circle pinned first
// per his ask -- not alphabetical, which scattered same-colored lines
// apart from each other. Stony Point isn't in that spec sheet but shares
// Frankston's color/family, so it's placed right after Williamstown.
// Anything not listed here (future new routes) falls back to alphabetical
// at the end rather than silently vanishing.
const LEGEND_ORDER = [
  'City Circle',
  'Sandringham',
  'Frankston',
  'Werribee',
  'Williamstown',
  'Stony Point',
  'Cranbourne',
  'Pakenham',
  'Belgrave',
  'Lilydale',
  'Alamein',
  'Glen Waverley',
  'Sunbury',
  'Craigieburn',
  'Upfield',
  'Mernda',
  'Hurstbridge',
  'Flemington Racecourse',
]

function sortedForLegend(routes: Route[]): Route[] {
  return [...routes].sort((a, b) => {
    const indexA = LEGEND_ORDER.indexOf(a.name)
    const indexB = LEGEND_ORDER.indexOf(b.name)
    if (indexA === -1 && indexB === -1) return a.name.localeCompare(b.name)
    if (indexA === -1) return 1
    if (indexB === -1) return -1
    return indexA - indexB
  })
}

/** `onToggle` re-syncs train markers immediately after a route is hidden/
 * shown -- without it, hidden-route trains would keep showing until the
 * next SSE delta arrives (up to ~10s later, the poller's own cadence). */
export function renderLegend(map: maplibregl.Map, container: HTMLElement, onToggle: () => void): void {
  container.innerHTML = '<p class="section-title">Lines</p>'

  const list = document.createElement('ul')
  list.className = 'legend-list'

  for (const route of sortedForLegend(geometry.routes)) {
    const item = document.createElement('li')
    item.className = 'legend-item'

    const label = document.createElement('label')
    label.className = 'legend-item-row'

    const swatch = document.createElement('span')
    swatch.className = 'legend-swatch'
    swatch.style.backgroundColor = route.color

    const name = document.createElement('span')
    name.textContent = route.name

    const nameGroup = document.createElement('span')
    nameGroup.className = 'legend-name-group'
    nameGroup.append(swatch, name)

    const checkbox = document.createElement('input')
    checkbox.type = 'checkbox'
    checkbox.checked = true
    checkbox.addEventListener('change', () => {
      setRouteVisible(map, route.id, checkbox.checked)
      onToggle()
    })

    const track = document.createElement('span')
    track.className = 'toggle-track'
    const thumb = document.createElement('span')
    thumb.className = 'toggle-thumb'
    track.append(thumb)

    const toggle = document.createElement('span')
    toggle.className = 'toggle'
    toggle.append(checkbox, track)

    label.append(nameGroup, toggle)
    item.append(label)
    list.append(item)
  }

  container.append(list)
}
