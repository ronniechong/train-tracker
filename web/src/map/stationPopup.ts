import * as maplibregl from 'maplibre-gl'
import './stationPopup.css'
import { routesById, routesByStationId, stationsById } from '../geometry'
import { formatTime } from '../lib/formatTime'
import type { ScheduledTrain, StationScheduleResponse } from '../api-types'

const SVG_NS = 'http://www.w3.org/2000/svg'

/** One row per distinct direction (by direction_id, falling back to
 * headsign for the rare trip with no direction_id) -- `departures` is
 * already sorted chronologically and capped server-side, so the first
 * occurrence of each key is the soonest for that direction. Compact
 * summary only, for the on-map popup; the sidebar StationPanel shows the
 * full per-direction list. */
function soonestPerDirection(departures: ScheduledTrain[]): ScheduledTrain[] {
  const seen = new Set<string>()
  const soonest: ScheduledTrain[] = []
  for (const dep of departures) {
    const key = dep.direction_id !== null ? String(dep.direction_id) : dep.headsign
    if (seen.has(key)) continue
    seen.add(key)
    soonest.push(dep)
  }
  return soonest
}

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

/** Builds the click-tooltip content for one station: name, one swatch+name
 * row per serving line, then (if loaded) one compact schedule row per
 * direction. Deliberately a flat list of appended rows rather than a fixed
 * template -- Ronnie's plan is to add more metadata here over time, so a
 * new field should just be another row, not a restructure. */
function buildStationPopupContent(
  stationId: string,
  schedule: StationScheduleResponse | null,
): HTMLElement | null {
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

  if (schedule && schedule.station_id === stationId) {
    for (const dep of soonestPerDirection(schedule.departures)) {
      const row = document.createElement('div')
      row.className = 'station-popup-schedule-row'
      const headsign = document.createElement('span')
      headsign.className = 'station-popup-schedule-headsign'
      headsign.textContent = dep.headsign
      const time = document.createElement('span')
      time.className = dep.is_cancelled
        ? 'station-popup-schedule-time station-popup-schedule-time-cancelled'
        : 'station-popup-schedule-time'
      time.textContent = formatTime(dep.is_live && dep.predicted_time ? dep.predicted_time : dep.scheduled_time)
      row.append(headsign, time)
      if (dep.is_cancelled) {
        const cancelled = document.createElement('span')
        cancelled.className = 'station-popup-schedule-cancelled-label'
        cancelled.textContent = 'Cancelled'
        row.append(cancelled)
      }
      content.append(row)
    }
  }

  return content
}

export interface StationPopupManager {
  /** Shows the popup for `stationId`, hides it for `null`. Driven by
   * App.tsx's `selectedStationId`, not the raw click event -- so the popup
   * opens/closes in lockstep with the sidebar panel via the same
   * click-same-station-again / click-elsewhere / close-button paths,
   * without duplicating that logic here. `schedule` is whatever
   * `useStationSchedule` currently holds for the selected station (or
   * `null` while loading/unavailable) -- MapView re-calls `sync` whenever
   * it changes, so the popup updates in place once the fetch resolves. */
  sync(stationId: string | null, schedule: StationScheduleResponse | null): void
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
    sync(stationId, schedule) {
      if (!stationId) {
        popup.remove()
        return
      }
      const station = stationsById.get(stationId)
      const content = buildStationPopupContent(stationId, schedule)
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
