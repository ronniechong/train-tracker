import { LINE_DASH_ARRAY, lineStyleForRouteName, vlineGeometry, vlineRoutesByStationId, vlineStationsById } from '../vlineGeometry'
import type { MapDataConfig } from './mapController'

const lineDashArrayByRouteId = new Map(
  vlineGeometry.routes.map((route) => [route.id, LINE_DASH_ARRAY[lineStyleForRouteName(route.name)]]),
)

// Melbourne end of the network -- a first-time user is most likely
// oriented here, not somewhere else in regional Victoria.
const SOUTHERN_CROSS = vlineStationsById.get('vic:rail:SSS')
const DEFAULT_CENTER: [number, number] = SOUTHERN_CROSS
  ? [SOUTHERN_CROSS.lon, SOUTHERN_CROSS.lat]
  : [144.9631, -37.8136]

// Much lower than Metro's 11 -- V/Line spans ~5 degrees of longitude, a
// wide network meant to be explored by panning, not framed all at once.
const INITIAL_ZOOM = 8

// Metro's floor of 9 is too tight here -- it's higher than this page's
// own INITIAL_ZOOM, which was silently forcing the map to open more
// zoomed-in than intended and blocking zooming out to see the whole
// network. 6 lets the full ~5-degree extent actually fit on screen.
const MIN_ZOOM = 6

export const VLINE_MAP_CONFIG: MapDataConfig = {
  geometry: vlineGeometry,
  routesByStationId: vlineRoutesByStationId,
  center: DEFAULT_CENTER,
  initialZoom: INITIAL_ZOOM,
  minZoom: MIN_ZOOM,
  // Metro's 11 would hide V/Line's stations at every zoom level worth
  // looking at -- V/Line's 110 stations are far sparser across a much
  // wider area, so they can show much earlier without clutter.
  stationMinZoom: 7,
  sourceIdPrefix: 'vline',
  lineDashArrayByRouteId,
}
