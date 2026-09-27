import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { CITIES, fmt, project } from '../../geo'
import { useOverload } from '../../store'
import { markFixLabels, shortWork, useAimedElement } from './fixModel'

// WHAT THE FIX CHANGES, on the map: a small green label at each element the fix raises (what gets built there and
// its MVA before -> after), on a short leader line, placed clear of the other labels, the map's city names, the
// upgraded lines, the campus and any area still dark. Drawn inside the map's camera (portaled into `.map-cam`, like
// the briefing's MapOverlay) and sized in real screen pixels (the camera's zoom times the SVG's own scale), so a
// label reads the same on a phone as on a laptop. Decorative for assistive tech: the results column's list says the
// same in words (aria-hidden here).
//   elements  fixElements(...) (biggest first; the first MAX get a label, the rest stay drawn in green)
//   campus    [{at: [lon, lat], text, sub}] a label at the campus for a fix that changes the campus itself
//   dark      [[lon, lat]] substations still without power with the fix (the labels keep off them)
const MAX = 6
const LEAD = 22 // px from the element to the label
const LH = 14 // px between the two lines
const T1 = 7.5 // px per character, first line (13 px, bold)
const T2 = 6.2 // px per character, second line (11 px)
// directions tried, in order of preference: up-right first (the map's own labels sit to the right of their dots)
const DIRS = [
  [1, -1],
  [-1, -1],
  [1, 1],
  [-1, 1],
  [1, 0],
  [-1, 0],
  [0, -1],
  [0, 1],
]

export default function FixLabels({ elements = [], campus = [], dark = [] }) {
  const { branchById, subPos } = useOverload()
  const { cam, k, ppu } = useCamera()
  const aimed = useAimedElement()
  const s = Math.max(k, 0.5) * ppu // screen px per map unit
  const placed = useMemo(() => {
    // each label's anchor, in map units: a transformer at its substation, a line at its middle
    const anchors = []
    const segs = []
    for (const el of elements) {
      const b = branchById.get(Number(el.id))
      const a = b && subPos(b.from_sub)
      const z = b && subPos(b.to_sub)
      if (!a || !z) continue
      const [x1, y1] = project(a[0], a[1])
      const [x2, y2] = project(z[0], z[1])
      segs.push([x1, y1, x2, y2])
      if (anchors.filter((q) => q.kind === 'el').length < MAX)
        anchors.push({ kind: 'el', id: el.id, x: (x1 + x2) / 2, y: (y1 + y2) / 2, t1: shortWork(el), t2: el.from != null ? `${fmt(Math.round(el.from))} → ${fmt(Math.round(el.to))} MVA` : `${fmt(Math.round(el.to))} MVA` })
    }
    for (const c of campus) {
      const [x, y] = project(c.at[0], c.at[1])
      anchors.push({ kind: 'campus', id: `c-${c.text}`, x, y, t1: c.text, t2: c.sub || null })
    }
    // the map's city names (GridMap: at the city + 6 px, baseline 4 px up, 11 px type, all in zoom-scaled units)
    const cities = CITIES.map((c) => {
      const [x, y] = project(c.lon, c.lat)
      return [x * s + 6 * ppu, y * s - 15 * ppu, x * s + (6 + c.name.length * 6.4) * ppu, y * s - 1 * ppu]
    })
    return place(anchors, segs, dark.map(([lon, lat]) => project(lon, lat)), cities, s)
  }, [elements, campus, dark, branchById, subPos, s, ppu])
  // while these labels are on the map, Fix it's own "+N MVA" labels step aside (BestSitesLayer)
  const drawn = !!cam && placed.length > 0
  useEffect(() => {
    if (!drawn) return undefined
    markFixLabels(true)
    return () => markFixLabels(false)
  }, [drawn])

  if (!cam || !placed.length) return null
  const w = 1 / s
  return createPortal(
    // (hidden while Present the damage is open: its slides fly the camera and pin their own places; fixChanges.css)
    <g className="fxl" aria-hidden="true" data-review-hide="">
      {placed.map((p) => {
        const on = aimed != null && String(aimed) === String(p.id)
        const off = aimed != null && !on
        return (
          <g key={p.id} className={`fxl__lbl${on ? ' fxl__lbl--on' : ''}${off ? ' fxl__lbl--off' : ''}`} transform={`translate(${p.x} ${p.y}) scale(${w})`}>
            {on && <circle className="fxl__ring" r="11" />}
            <line className="fxl__lead" x1="0" y1="0" x2={p.lx} y2={p.ly} />
            <circle className="fxl__dot" r="2.6" />
            <text className="fxl__t1" x={p.tx} y={p.ty} textAnchor={p.anchor}>
              {p.t1}
            </text>
            {p.t2 && (
              <text className="fxl__t2" x={p.tx} y={p.ty + LH} textAnchor={p.anchor}>
                {p.t2}
              </text>
            )}
          </g>
        )
      })}
    </g>,
    cam,
  )
}

// Place every label around its anchor where it overlaps the least: the labels already placed, the city names, the
// anchors, the upgraded lines and the dark substations. All in screen px (map units × s); the result in map units.
function place(anchors, segs, dark, cities, s) {
  const pts = [] // [x, y, r] obstacles in px
  anchors.forEach((a) => pts.push([a.x * s, a.y * s, a.kind === 'campus' ? 14 : 7]))
  dark.forEach(([x, y]) => pts.push([x * s, y * s, 12]))
  segs.forEach(([x1, y1, x2, y2]) => {
    const n = Math.min(40, Math.ceil((Math.hypot(x2 - x1, y2 - y1) * s) / 12))
    for (let i = 1; i < n; i++) pts.push([(x1 + ((x2 - x1) * i) / n) * s, (y1 + ((y2 - y1) * i) / n) * s, 4])
  })
  const boxes = []
  const out = []
  for (const a of anchors) {
    const ax = a.x * s
    const ay = a.y * s
    const wText = Math.max(a.t1.length * T1, (a.t2 || '').length * T2) + 4
    const h = a.t2 ? 30 : 16
    let best = null
    DIRS.forEach(([dx, dy], order) => {
      const n = Math.hypot(dx, dy)
      const ex = (dx / n) * LEAD
      const ey = (dy / n) * LEAD
      // the label box, relative to the anchor: beside the leader's end on its side, centered when straight up/down
      const x0 = dx > 0 ? ex + 3 : dx < 0 ? ex - 3 - wText : ex - wText / 2
      const y0 = dy < 0 ? ey - h : dy > 0 ? ey : ey - h / 2
      const box = [ax + x0, ay + y0, ax + x0 + wText, ay + y0 + h]
      let cost = order * 0.01
      for (const q of boxes) cost += overlap(box, q) / 40
      for (const q of cities) cost += overlap(box, q) / 60
      for (const [px, py, r] of pts) {
        if (Math.abs(px - ax) < 0.5 && Math.abs(py - ay) < 0.5) continue // its own anchor
        if (px > box[0] - r && px < box[2] + r && py > box[1] - r && py < box[3] + r) cost += r >= 12 ? 6 : r >= 7 ? 3 : 1
      }
      if (!best || cost < best.cost) best = { cost, box, x0, y0, ex, ey, dx }
    })
    boxes.push(best.box)
    const anchor = best.dx > 0 ? 'start' : best.dx < 0 ? 'end' : 'middle'
    const tx = best.dx > 0 ? best.x0 : best.dx < 0 ? best.x0 + wText : best.x0 + wText / 2
    out.push({ id: a.id, x: a.x, y: a.y, t1: a.t1, t2: a.t2, lx: best.ex, ly: best.ey, tx, ty: best.y0 + 12, anchor })
  }
  return out
}

function overlap(a, b) {
  const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0])
  const h = Math.min(a[3], b[3]) - Math.max(a[1], b[1])
  return w > 0 && h > 0 ? w * h : 0
}

// The map's camera group, its current zoom (read off its transform) and the SVG's own screen pixels per map unit
// (it fits its viewBox: "meet"), found once the grid has loaded and found again if the map remounts (the camera's
// reading is the same as features/briefing/MapOverlay.jsx's).
function useCamera() {
  const [cam, setCam] = useState(null)
  const [k, setK] = useState(1)
  const [ppu, setPpu] = useState(1)
  useEffect(() => {
    let obs = null
    let ro = null
    let el = null
    const check = () => {
      if (el?.isConnected) return
      obs?.disconnect()
      ro?.disconnect()
      el = document.querySelector('.map-cam')
      setCam(el)
      if (!el) return
      const cur = el
      const read = () => setK(Number(/scale\(([\d.]+)\)/.exec(cur.style.transform || cur.getAttribute('transform') || '')?.[1]) || 1)
      read()
      obs = new MutationObserver(read)
      obs.observe(cur, { attributes: true, attributeFilter: ['style', 'transform'] })
      const svg = cur.ownerSVGElement
      const size = () => {
        const r = svg?.getBoundingClientRect()
        const vb = svg?.viewBox?.baseVal
        if (r && vb?.width && vb?.height) setPpu(Math.min(r.width / vb.width, r.height / vb.height) || 1)
      }
      size()
      if (svg && typeof ResizeObserver !== 'undefined') {
        ro = new ResizeObserver(size)
        ro.observe(svg)
      }
    }
    check()
    const t = setInterval(check, 400)
    return () => {
      clearInterval(t)
      obs?.disconnect()
      ro?.disconnect()
    }
  }, [])
  return { cam, k, ppu }
}
