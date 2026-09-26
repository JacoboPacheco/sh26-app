// AI-boom mode: the preset buildout and small display helpers. The backend (grid.py) validates
// every site and snaps it to its nearest substation; the snap here only puts the marker where the
// campus will actually connect, and refuses clicks the backend would refuse, before they are sent.
import { HOMES_PER_MW } from '../../store'

export const MAX_EXTRA = 11 // the backend takes 12 data centers per case; one slot is the main site
export const CAMPUS_MW = 1000 // what a click adds
export const SIZE_MIN = 100
export const SIZE_MAX = 2000
const MAX_SNAP_KM = 75 // matches backend/grid.py: farther than this from any substation is off the model
const FL_BBOX = [-87.7, 24.3, -79.4, 31.1] // [lon0, lat0, lon1, lat1] when the grid has none
const BBOX_PAD_DEG = 0.3
const STATEWIDE_DEG = 2.5 // campuses spread wider than this: show the whole state
const FRAME_PAD_DEG = 0.5

// Five 1 GW campuses, each on a substation near a big metro (on land, 100 kV and up), picked so
// the white squares don't cover the city names. Together: 71 lines over limit and a 30-step
// cascade (~1.4 million homes, estimate) — backend/smoke_checks/boom.py uses the same points.
export const PRESET = [
  { id: 'jax', metro: 'Jacksonville', sub: 13477, lat: 30.3064, lon: -81.666 },
  { id: 'orl', metro: 'Orlando', sub: 13682, lat: 28.5603, lon: -81.3734 },
  { id: 'tpa', metro: 'Tampa', sub: 14658, lat: 27.9404, lon: -82.4302 },
  { id: 'fmy', metro: 'Fort Myers', sub: 14187, lat: 26.5765, lon: -81.8802 },
  { id: 'mia', metro: 'Miami', sub: 14323, lat: 25.7995, lon: -80.3041 },
].map((p) => ({ ...p, mw: CAMPUS_MW }))

function km(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180
  const a =
    Math.sin(((lat2 - lat1) * r) / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lon2 - lon1) * r) / 2) ** 2
  return 2 * 6371 * Math.asin(Math.sqrt(a))
}

// The substation a campus dropped at (lat, lon) connects to, or null when the point is off the model
// (outside the state's box, `bbox` = the grid's meta.bbox, or farther than MAX_SNAP_KM from any substation).
export function snapToSub(subs, lat, lon, bbox = FL_BBOX) {
  const [lon0, lat0, lon1, lat1] = bbox || FL_BBOX
  const p = BBOX_PAD_DEG
  if (!(lat >= lat0 - p && lat <= lat1 + p && lon >= lon0 - p && lon <= lon1 + p)) return null
  let best = null
  let bestKm = Infinity
  for (const s of subs) {
    const d = km(lat, lon, s.lat, s.lon)
    if (d < bestKm) {
      bestKm = d
      best = s
    }
  }
  return best && bestKm <= MAX_SNAP_KM ? best : null
}

// "FORT MYERS 3" -> "Fort Myers 3"
export const niceName = (name) =>
  String(name || '')
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase())

// 1000 -> "1 GW", 1500 -> "1.5 GW", 800 -> "800 MW"
export function sizeLabel(mw) {
  if (mw >= 1000) return `${(Math.round(mw / 100) / 10).toLocaleString('en-US')} GW`
  return `${Math.round(mw)} MW`
}

// homes a load this size would power, rounded for reading aloud: "3.5 million", "700,000"
export function homesLabel(mw) {
  const n = mw * HOMES_PER_MW
  if (n >= 1e6) return `${(Math.round(n / 1e5) / 10).toLocaleString('en-US')} million`
  return Math.round(n).toLocaleString('en-US')
}

// Bring points into view with room around them (the panels float over the map's edges); a set that
// spans the state gets the whole state.
export function frame(points, focus, mapRef) {
  if (!points.length) return
  const lons = points.map((p) => p[0])
  const lats = points.map((p) => p[1])
  const [w, e, s, n] = [Math.min(...lons), Math.max(...lons), Math.min(...lats), Math.max(...lats)]
  if (Math.max(e - w, n - s) > STATEWIDE_DEG) mapRef.current?.reset()
  else focus([...points, [w - FRAME_PAD_DEG, s - FRAME_PAD_DEG], [e + FRAME_PAD_DEG, n + FRAME_PAD_DEG]])
}

let seq = 0
export const newId = () => `c${Date.now().toString(36)}${(seq++).toString(36)}`
