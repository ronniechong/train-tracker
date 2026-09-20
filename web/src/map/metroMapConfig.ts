import { geometry, routesByStationId } from '../geometry'
import type { MapDataConfig } from './mapController'

// Deliberately NOT a fit of the network's real bounding box: the network's
// bounds aren't symmetric around the CBD (Stony Point pulls it far south,
// Pakenham/Belgrave pull it east), so fitting the whole-network box on
// load centers the empty middle of that box, not the city -- noticeably
// off-center. Fixed center/zoom here instead; `geometry.bounds` (used for
// `maxBounds`) still uses the real computed extent so panning stays
// limited to the actual network.
const MELBOURNE_CBD: [number, number] = [144.9631, -37.8136]
const INITIAL_ZOOM = 11

export const METRO_MAP_CONFIG: MapDataConfig = {
  geometry,
  routesByStationId,
  center: MELBOURNE_CBD,
  initialZoom: INITIAL_ZOOM,
  minZoom: 9,
  stationMinZoom: 11,
  sourceIdPrefix: 'metro',
}
