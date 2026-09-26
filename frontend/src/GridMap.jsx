import { createContext, memo, useCallback, useContext, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import outline from './data/florida_outline.json'
import { CITIES, HEIGHT, WIDTH, project, toPath, unproject } from './geo'

const LAND = outline.land.map((ring) => toPath(ring)).join('')
const LAKES = outline.lakes.map((l) => toPath(l))
const TOP = new Set(['ln--hot', 'ln--over', 'ln--tripped'])
const MAX_ZOOM = 12
const FOCUS_MAX_ZOOM = 6
const FOCUS_MIN_BOX = 140 // map units (~1.4° of latitude): the tightest a focus zooms
const EASE_MS = 900

// What a map layer needs to draw at the right size: the current zoom and the projection.
const MapViewCtx = createContext({ k: 1, project })
export const useMapView = () => useContext(MapViewCtx)

// The full-screen night map of Florida: branches colored by loading, substations as city lights
// that go out when they lose power, the data-center sites. It renders what the store computed —
// no power-flow logic here.
//
// Interaction: click (or drop the data-center card) calls onPlace(lat, lon); wheel zooms; drag pans.
// A feature can take over the pointer with `tool` = {down, move, up, cursor} — each gets
// {lat, lon} in map coordinates — e.g. hurricane mode drawing a storm track.
// Layers: `children` are SVG drawn inside the camera (above the grid); `overlay({svgRef, gRef})`
// renders HTML over the map (the flow canvas reads gRef's screen matrix every frame).
// The parent moves the camera through `ref.current.focus([[lon, lat], ...])`.
export default function GridMap({ ref, grid, lineClasses, subClasses, sites = [], headroomMode, onPlace, tool, children, overlay }) {
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
      reset() {
        easeTo({ k: 1, tx: 0, ty: 0 })
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

  // map coordinates under a client (screen) point, inside the zoomed group
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
    e.currentTarget.setPointerCapture(e.pointerId)
    if (tool) {
      const p = toMap(e.clientX, e.clientY)
      drag.current = { tool: true }
      if (p) tool.down?.(p)
      return
    }
    const scale = svgRef.current.getScreenCTM()?.a || 1
    drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty, scale, moved: false }
  }

  function onPointerMove(e) {
    const d = drag.current
    if (tool) {
      const p = toMap(e.clientX, e.clientY)
      if (p) tool.move?.(p, !!d)
      return
    }
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
    if (tool) {
      const p = toMap(e.clientX, e.clientY)
      if (p) tool.up?.(p)
      return
    }
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
  const mapView = useMemo(() => ({ k, project }), [k])
  // lights shrink as you zoom in (by √zoom, so they still grow a little); bucketed so the heavy
  // layer re-renders only when the zoom crosses a step, not on every pan frame
  const zoomBucket = k < 1.5 ? 1 : k < 2.5 ? 2 : k < 4 ? 3 : k < 6 ? 5 : 8

  return (
    <div className={`map${tool ? ' map--tool' : ''}`} style={tool?.cursor ? { cursor: tool.cursor } : undefined}>
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
          {/* the halo of a lit substation: warm sodium light fading out */}
          <radialGradient id="citylight">
            <stop offset="0" className="light-core" />
            <stop offset="1" className="light-edge" />
          </radialGradient>
        </defs>
        <rect className="map-sea" x={-WIDTH} y={-HEIGHT} width={WIDTH * 3} height={HEIGHT * 3} />
        <g
          ref={gRef}
          className={easing ? 'map-cam map-cam--ease' : 'map-cam'}
          style={{ transform: `translate(${view.tx}px, ${view.ty}px) scale(${k})` }}
        >
          <path className="map-land" d={LAND} />
          {LAKES.map((d) => (
            <path key={d} className="map-lake" d={d} />
          ))}
          <GridLayers geom={geom} lineClasses={lineClasses} subClasses={subClasses} zoomBucket={zoomBucket} />
          <MapViewCtx.Provider value={mapView}>{children}</MapViewCtx.Provider>
          {CITIES.map((c) => {
            const [x, y] = project(c.lon, c.lat)
            return (
              <text key={c.name} className="map-city" x={x + 6 / k} y={y - 4 / k} fontSize={11 / k}>
                {c.name}
              </text>
            )
          })}
          {sites.map((s, i) => {
            const [x, y] = project(s.lon, s.lat)
            return (
              <g key={`${s.lat},${s.lon},${i}`} className={s.primary ? 'site site--primary' : 'site'} transform={`translate(${x} ${y})`}>
                <circle className="site-ring" r={16 / k} />
                <rect className="site-dot" x={-5 / k} y={-5 / k} width={10 / k} height={10 / k} />
              </g>
            )
          })}
        </g>
      </svg>
      {overlay?.({ svgRef, gRef })}
      <button type="button" className="map-reset" onClick={() => easeTo({ k: 1, tx: 0, ty: 0 })} disabled={k === 1}>
        All of Florida
      </button>
    </div>
  )
}

// The heavy part (≈3,300 lines + 1,300 lights), memoized so panning and zooming don't re-render it.
const GridLayers = memo(function GridLayers({ geom, lineClasses, subClasses, zoomBucket = 1 }) {
  const shrink = 1 / Math.sqrt(zoomBucket)
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
            <circle key={d.id} cx={d.x} cy={d.y} r={7 * shrink} fill="url(#blackout)" />
          ))}
      </g>
      <g className="halos">
        {geom.dots.map((d) => (
          <circle key={d.id} className={`halo ${subClasses[d.id] || ''}`} cx={d.x} cy={d.y} r={d.r * 3.2 * shrink} fill="url(#citylight)" />
        ))}
      </g>
      <g className="lines">{geom.segments.map((s) => line(s, false))}</g>
      {/* stressed lines drawn again on top so a red line is never hidden under a calm one */}
      <g className="lines">{geom.segments.filter((s) => TOP.has(lineClasses[s.i])).map((s) => line(s, true))}</g>
      <g className="subs">
        {geom.dots.map((d) => (
          <circle key={d.id} className={`sub ${subClasses[d.id] || ''}`} cx={d.x} cy={d.y} r={d.r * shrink} />
        ))}
      </g>
    </>
  )
})
