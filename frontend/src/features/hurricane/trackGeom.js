import { project } from '../../geo'

// Storm-path geometry. Distances use the same equirectangular km plane as backend/hurricane.py
// (and the map's projection), so "how far along the path" agrees between the two.
const KM_PER_DEG = 111.19
const COS = Math.cos((27.7 * Math.PI) / 180)
// the backend's accepted box (backend/grid.py → LAT_MIN…LON_MAX)
const BOX = { lonMin: -87.7, lonMax: -79.4, latMin: 24.3, latMax: 31.1 }
export const MAX_POINTS = 40
export const MIN_TRACK_KM = 15 // shorter than this is a click, not a path

// map units per km (the projection draws 1° of latitude as a fixed number of units)
export const MU_PER_KM = (project(-81, 27)[1] - project(-81, 28)[1]) / KM_PER_DEG

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
const round4 = (v) => Math.round(v * 1e4) / 1e4

export const toLonLat = ({ lon, lat }) => [round4(clamp(lon, BOX.lonMin, BOX.lonMax)), round4(clamp(lat, BOX.latMin, BOX.latMax))]

const km = ([lon, lat]) => [lon * COS * KM_PER_DEG, lat * KM_PER_DEG]
export const distKm = (a, b) => {
  const [x1, y1] = km(a)
  const [x2, y2] = km(b)
  return Math.hypot(x2 - x1, y2 - y1)
}

export const lengthKm = (pts) => pts.reduce((sum, p, i) => (i ? sum + distKm(pts[i - 1], p) : 0), 0)

// Ramer–Douglas–Peucker, loosened until the path fits the backend's point limit.
export function simplify(pts, maxPoints = MAX_POINTS) {
  if (pts.length <= 2) return pts
  let eps = 1.5
  let out = rdp(pts, eps)
  while (out.length > maxPoints) {
    eps *= 1.6
    out = rdp(pts, eps)
  }
  return out
}

function rdp(pts, eps) {
  const xy = pts.map(km)
  const keep = new Array(pts.length).fill(false)
  keep[0] = keep[pts.length - 1] = true
  const stack = [[0, pts.length - 1]]
  while (stack.length) {
    const [a, b] = stack.pop()
    let best = -1
    let bestD = eps
    for (let i = a + 1; i < b; i++) {
      const d = pointSegKm(xy[i], xy[a], xy[b])
      if (d > bestD) {
        bestD = d
        best = i
      }
    }
    if (best >= 0) {
      keep[best] = true
      stack.push([a, best], [best, b])
    }
  }
  return pts.filter((_, i) => keep[i])
}

function pointSegKm([px, py], [ax, ay], [bx, by]) {
  const dx = bx - ax
  const dy = by - ay
  const len2 = dx * dx + dy * dy
  const t = len2 > 0 ? clamp(((px - ax) * dx + (py - ay) * dy) / len2, 0, 1) : 0
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy))
}

// The path on the map: projected points, cumulative lengths, and a point at any fraction of the way.
export function pathOnMap(pts) {
  const xy = pts.map(([lon, lat]) => project(lon, lat))
  const cum = [0]
  for (let i = 1; i < xy.length; i++) cum.push(cum[i - 1] + Math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]))
  const total = cum[cum.length - 1]
  const d = xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`).join('')
  function at(frac) {
    const s = clamp(frac, 0, 1) * total
    let i = 1
    while (i < cum.length - 1 && cum[i] < s) i++
    const seg = cum[i] - cum[i - 1]
    const t = seg > 0 ? (s - cum[i - 1]) / seg : 0
    return [xy[i - 1][0] + t * (xy[i][0] - xy[i - 1][0]), xy[i - 1][1] + t * (xy[i][1] - xy[i - 1][1])]
  }
  const n = xy.length
  const heading = n >= 2 ? (Math.atan2(xy[n - 1][1] - xy[n - 2][1], xy[n - 1][0] - xy[n - 2][0]) * 180) / Math.PI : 0
  return { xy, d, total, at, heading }
}

// Points framing the path plus the storm's reach on every side, for the camera.
export function framePoints(pts, radiusKm) {
  if (!pts.length) return []
  const dLat = radiusKm / KM_PER_DEG
  const dLon = radiusKm / (KM_PER_DEG * COS)
  const lons = pts.map((p) => p[0])
  const lats = pts.map((p) => p[1])
  const [x0, x1, y0, y1] = [Math.min(...lons) - dLon, Math.max(...lons) + dLon, Math.min(...lats) - dLat, Math.max(...lats) + dLat]
  return [
    [x0, y0],
    [x1, y1],
  ]
}
