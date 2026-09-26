// Map math for the SVG: an equirectangular projection (longitude shrunk by cos of the middle
// latitude) over peninsular Florida, plus the loading → color-class buckets. Rendering only;
// every number shown to the user comes from the backend.

export const VIEW = { lonMin: -85.6, lonMax: -79.3, latMin: 24.3, latMax: 31.1 }
const COS = Math.cos((27.7 * Math.PI) / 180)
const S = 100 // SVG units per degree of latitude

export const WIDTH = (VIEW.lonMax - VIEW.lonMin) * COS * S
export const HEIGHT = (VIEW.latMax - VIEW.latMin) * S

export const project = (lon, lat) => [(lon - VIEW.lonMin) * COS * S, (VIEW.latMax - lat) * S]
export const unproject = (x, y) => ({ lon: x / (COS * S) + VIEW.lonMin, lat: VIEW.latMax - y / S })

export const toPath = (pts, close = true) =>
  pts.map(([lon, lat], i) => `${i ? 'L' : 'M'}${project(lon, lat).map((v) => v.toFixed(1)).join(' ')}`).join('') +
  (close ? 'Z' : '')

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

export const fmt = (n) => Math.round(n).toLocaleString('en-US')
