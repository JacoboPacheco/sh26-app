import { STATES } from '../../geo'

// The national map's projection for the Views page: Albers equal-area conic (the usual "U.S. map"), so the
// lower 48 read as one shape instead of the flat, stretched look of longitude/latitude. Pure functions and
// the outline paths, built once from the committed Census state outlines (geo.js STATES).

const D = Math.PI / 180
const PHI1 = 29.5 * D
const PHI2 = 45.5 * D
const PHI0 = 37.5 * D
const LAM0 = -96 * D
const N = (Math.sin(PHI1) + Math.sin(PHI2)) / 2
const C = Math.cos(PHI1) ** 2 + 2 * N * Math.sin(PHI1)
const RHO0 = Math.sqrt(C - 2 * N * Math.sin(PHI0)) / N
const S = 1000 // map units per radian of the projection

export function albers(lon, lat) {
  const rho = Math.sqrt(C - 2 * N * Math.sin(lat * D)) / N
  const th = N * (lon * D - LAM0)
  return [S * rho * Math.sin(th), -S * (RHO0 - rho * Math.cos(th))]
}

const bboxOf = (points) => {
  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  points.forEach(([x, y]) => {
    if (x < x0) x0 = x
    if (x > x1) x1 = x
    if (y < y0) y0 = y
    if (y > y1) y1 = y
  })
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

const r1 = (n) => Math.round(n * 10) / 10

// [{code, name, d, box}]: `d` is the SVG path, `box` the state's bounding box in map units
export const STATE_SHAPES = Object.entries(STATES).map(([code, s]) => {
  const rings = s.rings.map((ring) => ring.map(([lon, lat]) => albers(lon, lat)))
  const d = rings.map((ring) => `M${ring.map(([x, y]) => `${r1(x)} ${r1(y)}`).join('L')}Z`).join('')
  return { code, name: s.name, d, box: bboxOf(rings.flat()) }
})

export const MAP_BOX = (() => {
  const b = bboxOf(STATE_SHAPES.flatMap((s) => [[s.box.x, s.box.y], [s.box.x + s.box.w, s.box.y + s.box.h]]))
  const pad = 14
  return { x: b.x - pad, y: b.y - pad, w: b.w + 2 * pad, h: b.h + 2 * pad }
})()

// the view box that shows the given states (or all of them), padded, with the map's aspect ratio
export function boxForStates(codes) {
  const shapes = STATE_SHAPES.filter((s) => codes.includes(s.code))
  if (!shapes.length) return MAP_BOX
  const b = bboxOf(shapes.flatMap((s) => [[s.box.x, s.box.y], [s.box.x + s.box.w, s.box.y + s.box.h]]))
  const pad = Math.max(b.w, b.h) * 0.12 + 10
  return fitAspect({ x: b.x - pad, y: b.y - pad, w: b.w + 2 * pad, h: b.h + 2 * pad }, MAP_BOX.w / MAP_BOX.h)
}

// grow a box (around its centre) until it has the given width/height ratio
export function fitAspect(b, ratio) {
  let { x, y, w, h } = b
  if (w / h < ratio) {
    const nw = h * ratio
    x -= (nw - w) / 2
    w = nw
  } else {
    const nh = w / ratio
    y -= (nh - h) / 2
    h = nh
  }
  return { x, y, w, h }
}

// a site's place on the map, or null when it has no point
export function sitePoint(s) {
  return Number.isFinite(s.lat) && Number.isFinite(s.lon) ? albers(s.lon, s.lat) : null
}
