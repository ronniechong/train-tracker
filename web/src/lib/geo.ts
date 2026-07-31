/** Mirrors service/src/traintracker/state/geo.py's haversine_m -- same
 * formula, independent implementation (the frontend has no access to the
 * Python module). Metres between two lat/lon points. */
export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const EARTH_RADIUS_M = 6_371_000
  const toRad = (deg: number) => (deg * Math.PI) / 180
  const dLat = toRad(lat2 - lat1)
  const dLon = toRad(lon2 - lon1)
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a))
}
