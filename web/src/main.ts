import './style.css'
import { initMap, addGeometryLayers } from './map.ts'
import { renderLegend } from './legend.ts'
import { startLiveFeed } from './live.ts'
import { syncTrains } from './trains.ts'
import { renderStatus } from './status.ts'
import type { Train } from './api-types.ts'

document.querySelector<HTMLDivElement>('#app')!.innerHTML = `
<div id="shell">
  <aside id="sidebar">
    <header id="site-header">
      <div class="header-title-row">
        <img src="/favicon.svg" alt="" width="28" height="28" />
        <h1>Melbourne Train Tracker</h1>
      </div>
      <p>Melbourne Metro, close to real-time 😅</p>
    </header>

    <section id="legend"></section>

    <section id="search">
      <p class="section-title">Search</p>
      <p class="placeholder">Jump to a station — Stage 4</p>
    </section>

    <section id="status"></section>

    <section id="selected-station">
      <p class="section-title">Station</p>
      <p class="placeholder">Click a station on the map — Stage 4</p>
    </section>

    <section id="cta">
      <p class="placeholder">CTA — Stage 5</p>
    </section>

    <footer id="site-footer">
      <p class="placeholder">Attribution — Stage 5</p>
    </footer>
  </aside>

  <main id="map-container">
    <div id="loading-overlay">
      <svg class="spinner" viewBox="0 0 50 50" width="40" height="40" aria-hidden="true">
        <circle class="spinner-track" cx="25" cy="25" r="20" fill="none" stroke-width="4"></circle>
        <circle class="spinner-arc" cx="25" cy="25" r="20" fill="none" stroke-width="4"></circle>
      </svg>
      <p>Loading live map…</p>
    </div>
  </main>
</div>
`

const map = initMap(document.querySelector<HTMLDivElement>('#map-container')!)

map.on('load', () => {
  addGeometryLayers(map)

  let latestTrains: Map<string, Train> = new Map()
  renderLegend(map, document.querySelector<HTMLDivElement>('#legend')!, () => {
    syncTrains(map, latestTrains)
  })
  document.querySelector<HTMLDivElement>('#loading-overlay')!.classList.add('hidden')

  const statusEl = document.querySelector<HTMLDivElement>('#status')!
  startLiveFeed((state) => {
    latestTrains = state.trains
    syncTrains(map, state.trains)
    renderStatus(statusEl, state)
  })
})
