import { memo, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import outline from './data/florida_outline.json'
import { CITIES, HEIGHT, WIDTH, project, toPath, unproject } from './geo'
import { Button } from './ui'

const LAND = outline.land.map((ring) => toPath(ring)).join('')
const LAKES = outline.lakes.map((l) => toPath(l))
const TOP = new Set(['ln--hot', 'ln--over', 'ln--tripped'])
const MAX_ZOOM = 12
const FOCUS_MAX_ZOOM = 6
const FOCUS_MIN_BOX = 140 // map units (~1.4° of latitude): the tightest a focus zooms
const EASE_MS = 900

// The dark Florida map: the grid's branches colored by loading, substations as dots sized by load,
// and the data-center site. Click (or drop the data-center card) to place the site; wheel zooms,
// drag pans. It renders what the parent computed — no power-flow logic here. The parent moves the
// camera through `ref.current.focus([[lon, lat], ...])`, which eases to fit those points.
export default function GridMap({ ref, grid, lineClasses, subClasses, site, headroomMode, onPlace }) {
  const svgRef = useRef(null)
  const gRef = useRef(null)
  const drag = useRef(null)
  const easeTimer = useRef(null)
  const [view, setView] = useState({ k: 1, tx: 0, ty: 0 })
  const [easing, setEasing] = useState(false)

  // animate the next view change (a camera move), then drop the transition so panning stays direct
  const easeTo = useCallback((next) => {
    clearTimeout(easeTimer.current)
    setEasing(true)
    setView(next)
    easeTimer.current = setTimeout(() => setEasing(false), EASE_MS)
  }, [])
  useEffect(() => () => clearTimeout(easeTimer.current), [])

  useImperativeHandle(
    ref,
    () => ({
      focus(points) {
        if (!points.length) return
        const xy = points.map(([lon, lat]) => project(lon, lat))
        const xs = xy.map((p) => p[0])
        const ys = xy.map((p) => p[1])
        const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
        const bw = Math.max(x1 - x0 + 40, FOCUS_MIN_BOX)
        const bh = Math.max(y1 - y0 + 40, FOCUS_MIN_BOX)
        const k = Math.min(FOCUS_MAX_ZOOM, Math.max(1, Math.min(WIDTH / bw, HEIGHT / bh)))
        const cx = (x0 + x1) / 2
        const cy = (y0 + y1) / 2
        easeTo({ k, tx: WIDTH / 2 - cx * k, ty: HEIGHT / 2 - cy * k })
      },
    }),
    [easeTo],
  )

  const geom = useMemo(() => {
    const xy = new Map(grid.subs.map((s) => [s.id, project(s.lon, s.lat)]))
    const segments = []
    grid.branches.forEach((b, i) => {
      if (b.from_sub === b.to_sub) return // a transformer inside one substation has no length
      const [x1, y1] = xy.get(b.from_sub)
      const [x2, y2] = xy.get(b.to_sub)
      segments.push({ i, x1, y1, x2, y2, kv: b.kv >= 300 ? 'kv-hi' : 'kv-lo' })
    })
    const dots = grid.subs.map((s) => {
      const [x, y] = xy.get(s.id)
      return { id: s.id, x, y, r: 0.7 + Math.sqrt(Math.max(s.load_mw, 0)) / 12 }
    })
    return { segments, dots }
  }, [grid])

  // map units under a client (screen) point, inside the zoomed group
  const toMap = useCallback((clientX, clientY) => {
    const ctm = gRef.current?.getScreenCTM()
    if (!ctm) return null
    const p = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse())
    return unproject(p.x, p.y)
  }, [])

  // wheel zoom around the pointer; a native listener because React's can't preventDefault reliably
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return undefined
    const onWheel = (e) => {
      e.preventDefault()
      const ctm = svg.getScreenCTM()
      if (!ctm) return
      const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse())
      setView((v) => {
        const k = Math.min(MAX_ZOOM, Math.max(1, v.k * (e.deltaY < 0 ? 1.25 : 0.8)))
        if (k === 1) return { k: 1, tx: 0, ty: 0 }
        return { k, tx: p.x - ((p.x - v.tx) * k) / v.k, ty: p.y - ((p.y - v.ty) * k) / v.k }
      })
    }
    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => svg.removeEventListener('wheel', onWheel)
  }, [])

  function onPointerDown(e) {
    if (e.button !== 0) return
    const scale = svgRef.current.getScreenCTM()?.a || 1
    drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty, scale, moved: false }
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  function onPointerMove(e) {
    const d = drag.current
    if (!d) return
    const dx = e.clientX - d.x
    const dy = e.clientY - d.y
    if (!d.moved && Math.hypot(dx, dy) < 5) return
    d.moved = true
    if (view.k > 1) setView((v) => ({ ...v, tx: d.tx + dx / d.scale, ty: d.ty + dy / d.scale }))
  }

  function onPointerUp(e) {
    const d = drag.current
    drag.current = null
    if (!d || d.moved) return
    const p = toMap(e.clientX, e.clientY)
    if (p) onPlace(p.lat, p.lon)
  }

  function onDrop(e) {
    e.preventDefault()
    const p = toMap(e.clientX, e.clientY)
    if (p) onPlace(p.lat, p.lon)
  }

  const k = view.k
  const sitePt = site ? project(site.lon, site.lat) : null

  return (
    <div className="map">
      <svg
        ref={svgRef}
        className={`map-svg${headroomMode ? ' map-svg--headroom' : ''}`}
        viewBox={`0 0 ${WIDTH.toFixed(0)} ${HEIGHT.toFixed(0)}`}
        role="img"
        aria-label="Map of the synthetic Florida grid. Click the map or drop the data center on it to place it."
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => (drag.current = null)}
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDrop}
      >
        <defs>
          {/* the shadow a dark substation casts: the land around it goes black */}
          <radialGradient id="blackout">
            <stop offset="0" className="blackout-core" />
            <stop offset="1" className="blackout-edge" />
          </radialGradient>
        </defs>
        <rect className="map-sea" width={WIDTH} height={HEIGHT} />
        <g
          ref={gRef}
          className={easing ? 'map-cam map-cam--ease' : 'map-cam'}
          style={{ transform: `translate(${view.tx}px, ${view.ty}px) scale(${k})` }}
        >
          <path className="map-land" d={LAND} />
          {LAKES.map((d) => (
            <path key={d} className="map-lake" d={d} />
          ))}
          <GridLayers geom={geom} lineClasses={lineClasses} subClasses={subClasses} />
          {CITIES.map((c) => {
            const [x, y] = project(c.lon, c.lat)
            return (
              <text key={c.name} className="map-city" x={x + 6 / k} y={y - 4 / k} fontSize={11 / k}>
                {c.name}
              </text>
            )
          })}
          {sitePt && (
            <g className="site" transform={`translate(${sitePt[0]} ${sitePt[1]})`}>
              <circle className="site-ring" r={14 / k} />
              <circle className="site-dot" r={6 / k} />
            </g>
          )}
        </g>
      </svg>
      <div className="map-tools">
        <Button variant="secondary" onClick={() => easeTo({ k: 1, tx: 0, ty: 0 })} disabled={k === 1}>
          Reset view
        </Button>
      </div>
    </div>
  )
}

// The heavy part (≈3,300 lines + 1,300 dots), memoized so panning and zooming don't re-render it.
const GridLayers = memo(function GridLayers({ geom, lineClasses, subClasses }) {
  const line = (s, top) => (
    <line
      key={top ? `t${s.i}` : s.i}
      className={`ln ${s.kv} ${lineClasses[s.i] || 'ln--calm'}`}
      x1={s.x1}
      y1={s.y1}
      x2={s.x2}
      y2={s.y2}
    />
  )
  return (
    <>
      <g className="blackout">
        {geom.dots
          .filter((d) => subClasses[d.id] === 'sub--dark')
          .map((d) => (
            <circle key={d.id} cx={d.x} cy={d.y} r={7} fill="url(#blackout)" />
          ))}
      </g>
      <g className="lines">{geom.segments.map((s) => line(s, false))}</g>
      {/* stressed lines drawn again on top so a red line is never hidden under a calm one */}
      <g className="lines">{geom.segments.filter((s) => TOP.has(lineClasses[s.i])).map((s) => line(s, true))}</g>
      <g className="subs">
        {geom.dots.map((d) => (
          <circle key={d.id} className={`sub ${subClasses[d.id] || ''}`} cx={d.x} cy={d.y} r={d.r} />
        ))}
      </g>
    </>
  )
})
