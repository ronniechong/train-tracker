import { useId, useRef, useState, type KeyboardEvent } from 'react'
import { geometry, routesByStationId, routesById, type Station } from '../geometry'
import { Section } from './Section'
import styles from './Search.module.css'

const MAX_RESULTS = 8

function matchingStations(query: string): Station[] {
  const needle = query.trim().toLowerCase()
  if (!needle) return []
  return geometry.stations.filter((station) => station.name.toLowerCase().includes(needle)).slice(0, MAX_RESULTS)
}

interface SearchProps {
  onSelect: (station: Station) => void
}

/** Typeahead against the bundled station list (M4 Stage 4 remainder,
 * 2026-07-31). Selecting a result flies the map there (App.tsx's
 * flyToRequest) and re-enables any of the station's lines currently hidden
 * in the Legend, so a result on a toggled-off line doesn't land on what
 * looks like empty map. */
export function Search({ onSelect }: SearchProps) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const listboxId = useId()
  const inputRef = useRef<HTMLInputElement>(null)

  const results = matchingStations(query)
  const open = query.trim().length > 0

  function reset(): void {
    setQuery('')
    setActiveIndex(0)
  }

  function selectStation(station: Station): void {
    onSelect(station)
    reset()
    inputRef.current?.blur()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (!open || results.length === 0) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((index) => Math.min(index + 1, results.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((index) => Math.max(index - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const station = results[activeIndex]
      if (station) selectStation(station)
    } else if (event.key === 'Escape') {
      reset()
    }
  }

  return (
    <Section title="Search" className={styles.section}>
      <input
        ref={inputRef}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-autocomplete="list"
        className={styles.input}
        placeholder="Jump to a station…"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value)
          setActiveIndex(0)
        }}
        onKeyDown={handleKeyDown}
      />
      {open && (
        <ul id={listboxId} role="listbox" className={styles.results}>
          {results.length === 0 && <li className={styles.empty}>No matching station</li>}
          {results.map((station, index) => {
            const routeIds = [...(routesByStationId.get(station.id) ?? [])]
            return (
              <li
                key={station.id}
                role="option"
                aria-selected={index === activeIndex}
                className={index === activeIndex ? styles.resultActive : styles.result}
                onMouseEnter={() => setActiveIndex(index)}
                // onMouseDown, not onClick -- fires before the input's own
                // blur, so the selection isn't lost to blur closing the
                // list first.
                onMouseDown={(event) => {
                  event.preventDefault()
                  selectStation(station)
                }}
              >
                <span className={styles.stationName}>{station.name}</span>
                <span className={styles.lineList}>
                  {routeIds.map((routeId) => {
                    const route = routesById.get(routeId)
                    if (!route) return null
                    return (
                      <span key={routeId} className={styles.lineTag}>
                        <span className={styles.swatch} style={{ backgroundColor: route.color }} />
                        {route.name}
                      </span>
                    )
                  })}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </Section>
  )
}
