// Map math for the SVG: an equirectangular projection (longitude shrunk by cos of the middle
// latitude) over the current region, plus the loading → color-class buckets. Rendering only;
// every number shown to the user comes from the backend.
//
// The projection is module state: setProjection(region, bbox) swaps it, and WIDTH / HEIGHT / VIEW
// are live bindings (`export let`), so every importer reads the current region's values. Florida
// keeps its original hand-tuned view exactly. Every region is scaled so its longer side is
// Florida's (680 map units), so line widths, dots and labels look the same size in every state.
// The store sets the projection before it hands the new region's grid to the map.
import states from './data/states_outline.json'

const FL_VIEW = { lonMin: -85.6, lonMax: -79.3, latMin: 24.3, latMax: 31.1, midLat: 27.7, s: 100 }
const SIDE = (FL_VIEW.latMax - FL_VIEW.latMin) * FL_VIEW.s // 680: the longer side of every view
const PAD = 0.06 // share of the region's span added on each side

export const STATES = states.states // {code: {name, bbox: [lon0, lat0, lon1, lat1], rings: [[[lon, lat], ...]]}}
export const US_BBOX = states.bbox // lower 48 + DC

export let VIEW = FL_VIEW // {lonMin, lonMax, latMin, latMax, midLat, s (map units per degree of latitude)}
export let REGION = 'FL' // the region the projection is set for ('US' = the national map)
let COS = Math.cos((VIEW.midLat * Math.PI) / 180)
export let WIDTH = (VIEW.lonMax - VIEW.lonMin) * COS * VIEW.s
export let HEIGHT = (VIEW.latMax - VIEW.latMin) * VIEW.s

// The view for a region: Florida's own; any other from its outline's bbox (else `bbox`,
// [lon0, lat0, lon1, lat1]), padded.
export function viewFor(region, bbox) {
  if (region === 'FL') return FL_VIEW
  const b = region === 'US' ? US_BBOX : STATES[region]?.bbox || bbox
  if (!b) return FL_VIEW
  const [x0, y0, x1, y1] = b
  const px = Math.max((x1 - x0) * PAD, 0.1)
  const py = Math.max((y1 - y0) * PAD, 0.1)
  const v = { lonMin: x0 - px, lonMax: x1 + px, latMin: y0 - py, latMax: y1 + py, midLat: (y0 + y1) / 2 }
  const cos = Math.cos((v.midLat * Math.PI) / 180)
  v.s = SIDE / Math.max(v.latMax - v.latMin, (v.lonMax - v.lonMin) * cos)
  return v
}

// Switch the projection (idempotent). Returns true when it changed.
export function setProjection(region, bbox) {
  const code = region || 'FL'
  const v = viewFor(code, bbox)
  const same = ['lonMin', 'lonMax', 'latMin', 'latMax', 's'].every((k) => v[k] === VIEW[k])
  if (code === REGION && same) return false
  VIEW = v
  REGION = code
  COS = Math.cos((v.midLat * Math.PI) / 180)
  WIDTH = (v.lonMax - v.lonMin) * COS * v.s
  HEIGHT = (v.latMax - v.latMin) * v.s
  return true
}

// Set the projection for a grid payload from /api/grid (meta.region + meta.bbox).
export const setProjectionFor = (grid) => setProjection(grid?.meta?.region || 'FL', grid?.meta?.bbox)

export const project = (lon, lat) => [(lon - VIEW.lonMin) * COS * VIEW.s, (VIEW.latMax - lat) * VIEW.s]
export const unproject = (x, y) => ({ lon: x / (COS * VIEW.s) + VIEW.lonMin, lat: VIEW.latMax - y / VIEW.s })

// A frozen copy of the current projection (the map uses it to fly from one region's view to the next).
export function snapshot() {
  const v = VIEW
  const cos = COS
  return {
    region: REGION,
    width: WIDTH,
    height: HEIGHT,
    project: (lon, lat) => [(lon - v.lonMin) * cos * v.s, (v.latMax - lat) * v.s],
    unproject: (x, y) => ({ lon: x / (cos * v.s) + v.lonMin, lat: v.latMax - y / v.s }),
  }
}

// Map units per km in the current projection (north-south; 1° of latitude = 111.19 km).
export const muPerKm = () => VIEW.s / 111.19

export const toPath = (pts, close = true) =>
  pts.map(([lon, lat], i) => `${i ? 'L' : 'M'}${project(lon, lat).map((v) => v.toFixed(1)).join(' ')}`).join('') +
  (close ? 'Z' : '')

// The state (code) a point falls in, or null (even-odd point-in-polygon over the outlines).
export function regionAt(lat, lon) {
  for (const [code, st] of Object.entries(STATES)) {
    const [x0, y0, x1, y1] = st.bbox
    if (lon < x0 || lon > x1 || lat < y0 || lat > y1) continue
    let inside = false
    for (const ring of st.rings) {
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i]
        const [xj, yj] = ring[j]
        if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside
      }
    }
    if (inside) return code
  }
  return null
}

// Branch loading (% of rating) → class. Over 100 % is an overload.
export function loadClass(pct) {
  if (pct >= 100) return 'ln--over'
  if (pct >= 80) return 'ln--hot'
  if (pct >= 50) return 'ln--warm'
  return 'ln--calm'
}

// Headroom (MW a substation can take) against the slider's size → class.
export function headroomClass(mw, size) {
  if (mw >= size) return 'sub--hr-ok'
  if (mw >= size / 2) return 'sub--hr-mid'
  return 'sub--hr-low'
}

// Florida's ten labels (hand-placed). Other states: citiesFor(grid).
export const CITIES = [
  { name: 'Tallahassee', lat: 30.44, lon: -84.28 },
  { name: 'Jacksonville', lat: 30.33, lon: -81.66 },
  { name: 'Gainesville', lat: 29.65, lon: -82.32 },
  { name: 'Daytona Beach', lat: 29.21, lon: -81.02 },
  { name: 'Orlando', lat: 28.54, lon: -81.38 },
  { name: 'Tampa', lat: 27.95, lon: -82.46 },
  { name: 'Sarasota', lat: 27.34, lon: -82.53 },
  { name: 'Fort Myers', lat: 26.64, lon: -81.87 },
  { name: 'West Palm Beach', lat: 26.72, lon: -80.05 },
  { name: 'Miami', lat: 25.76, lon: -80.19 },
]

// px from a town's point to the start of its name on the map: 6, or just past a campus marker sitting on the town
// (the hero's Fort Myers: the dot would cover the name's first letters). `sites`: [{lat, lon}] or [[lon, lat]]; `k`: the zoom.
const SITE_DOT_PX = 16 // the marker's pulse ring (the dot inside it is 6)
export function cityLabelDx(city, sites, k) {
  const [cx, cy] = project(city.lon, city.lat)
  let dx0 = 6
  for (const s of sites || []) {
    const [sx, sy] = Array.isArray(s) ? project(s[0], s[1]) : project(s.lon, s.lat)
    const dx = (sx - cx) * k
    const dy = (sy - cy) * k
    // the name's first letters span x 6..24 and y -13..-1 from the town's point; the marker is a disc of SITE_DOT_PX
    if (dx + SITE_DOT_PX > dx0 && dx - SITE_DOT_PX < 24 && dy + SITE_DOT_PX > -13 && dy - SITE_DOT_PX < -1) dx0 = Math.max(dx0, dx + SITE_DOT_PX + 2)
  }
  return dx0
}

// The map labels for a grid: Florida's ten, or for any other state the areas (towns the
// substations are named after) with the most load, spread apart, placed at their load-weighted center.
const cityCache = new WeakMap()
export function citiesFor(grid, count = 10) {
  const region = grid?.meta?.region || 'FL'
  if (region === 'FL') return CITIES
  if (!grid?.subs?.length) return []
  if (cityCache.has(grid)) return cityCache.get(grid)
  const byArea = new Map()
  grid.subs.forEach((s) => {
    const name = s.area || s.name
    const w = Math.max(s.load_mw, 0.1)
    const a = byArea.get(name) || { name, mw: 0, lat: 0, lon: 0 }
    a.mw += w
    a.lat += s.lat * w
    a.lon += s.lon * w
    byArea.set(name, a)
  })
  const b = grid.meta?.bbox || [0, 0, 1, 1]
  const minGap = Math.max(b[2] - b[0], b[3] - b[1]) / 9 // degrees between labels
  const out = []
  ;[...byArea.values()]
    .map((a) => ({ name: a.name, mw: a.mw, lat: a.lat / a.mw, lon: a.lon / a.mw }))
    .sort((x, y) => y.mw - x.mw)
    .forEach((a) => {
      if (out.length >= count) return
      if (out.every((c) => Math.hypot(c.lat - a.lat, c.lon - a.lon) >= minGap)) out.push({ name: a.name, lat: a.lat, lon: a.lon })
    })
  cityCache.set(grid, out)
  return out
}

export const fmt = (n) => Math.round(n).toLocaleString('en-US')
