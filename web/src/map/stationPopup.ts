import * as maplibregl from 'maplibre-gl'
import './stationPopup.css'
import { routesById, routesByStationId, stationsById } from '../geometry'

const SVG_NS = 'http://www.w3.org/2000/svg'

function createSwatch(color: string): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, 'svg')
  svg.setAttribute('class', 'station-popup-swatch')
  svg.setAttribute('viewBox', '0 0 10 10')
  svg.setAttribute('width', '10')
  svg.setAttribute('height', '10')
  const rect = document.createElementNS(SVG_NS, 'rect')
  rect.setAttribute('x', '0.75')
  rect.setAttribute('y', '0.75')
  rect.setAttribute('width', '8.5')
  rect.setAttribute('height', '8.5')
  rect.setAttribute('rx', '1.5')
  rect.setAttribute('fill', color)
  rect.setAttribute('stroke', '#ffffff')
  rect.setAttribute('stroke-width', '1')
  svg.append(rect)
  return svg
}

/** Builds the click-tooltip content for one station: name, then one
 * swatch+name row per serving line. Deliberately a flat list of appended
 * rows rather than a fixed template -- Ronnie's plan is to add more
 * metadata here over time, so a new field should just be another row, not
 * a restructure. */
function buildStationPopupContent(stationId: string): HTMLElement | null {
  const station = stationsById.get(stationId)
  if (!station) return null

  const content = document.createElement('div')
  content.className = 'station-popup-content'

  const title = document.createElement('div')
  title.className = 'station-popup-title'
  title.textContent = station.name
  content.append(title)

  for (const routeId of routesByStationId.get(stationId) ?? []) {
    const route = routesById.get(routeId)
    if (!route) continue
    const row = document.createElement('div')
    row.className = 'station-popup-row'
    row.append(createSwatch(route.color))
    const label = document.createElement('span')
    label.textContent = route.name
    row.append(label)
    content.append(row)
  }

  return content
}

export interface StationPopupManager {
  /** Shows the popup for `stationId`, hides it for `null`. Driven by
   * App.tsx's `selectedStationId`, not the raw click event -- so the popup
   * opens/closes in lockstep with the sidebar panel via the same
   * click-same-station-again / click-elsewhere / close-button paths,
   * without duplicating that logic here. */
  sync(stationId: string | null): void
  /** Removes the popup from the map. Call on MapView unmount. */
  destroy(): void
}

export function createStationPopupManager(map: maplibregl.Map): StationPopupManager {
  const popup = new maplibregl.Popup({
    className: 'station-popup',
    closeButton: false,
    closeOnClick: false,
    offset: 10,
  })

  return {
    sync(stationId) {
      if (!stationId) {
        popup.remove()
        return
      }
      const station = stationsById.get(stationId)
      const content = buildStationPopupContent(stationId)
      if (!station || !content) {
        popup.remove()
        return
      }
      popup.setLngLat([station.lon, station.lat]).setDOMContent(content).addTo(map)
    },
    destroy() {
      popup.remove()
    },
  }
}
