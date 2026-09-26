import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ErrorBanner, Loading } from '../../ui'
import { useGridlock } from './context'
import { OUTLINES, OUTLINES_BBOX } from './outlines'
import { boundsOf, fmtPairDistance, projectPoints, toneOf, utilityShort } from './format'
import './gridlock.css'

// The Build plans map: our own SVG, an equirectangular projection over the basemap's bbox (the
// two states are small enough that cos(mid-latitude) keeps shapes right). Drag to pan, wheel or
// the buttons to zoom, click a project or an overlap. Strokes don't scale with the zoom; circle
// radii and label sizes are divided by the zoom so they stay the same size on screen.

const STATE_NAMES = { SC: 'South Carolina', GA: 'Georgia', NC: 'North Carolina', FL: 'Florida', AL: 'Alabama', TN: 'Tennessee' }
// a few cities for orientation (static context, not data)
const CITIES = [
  ['Atlanta', 33.749, -84.388],
  ['Augusta', 33.471, -81.975],
  ['Savannah', 32.081, -81.091],
  ['Columbia', 34.0, -81.035],
  ['Charleston', 32.777, -79.931],
  ['Macon', 32.841, -83.632],
  ['Greenville', 34.853, -82.394],
  ['Myrtle Beach', 33.689, -78.887],
  ['Albany', 31.578, -84.156],
  ['Valdosta', 30.833, -83.28],
  ['Brunswick', 31.15, -81.492],
  ['Columbus', 32.461, -84.988],
  ['Florence', 34.195, -79.763],
]
const TIER_RANK = { touching: 0, row: 1, site: 2, crews: 3 }
const REDUCED = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// --- tolerant readers for the basemap (the pipeline owns its exact shape) ---
const isPt = (a) => Array.isArray(a) && a.length >= 2 && typeof a[0] === 'number' && typeof a[1] === 'number'
const isLine = (a) => Array.isArray(a) && isPt(a[0])
function polylines(a) {
  if (isLine(a)) return [a]
  if (Array.isArray(a)) return a.flatMap(polylines)
  if (a && typeof a === 'object') return polylines(a.coords || a.coordinates || a.rings || a.geometry?.coordinates || a.c || null)
  return []
}
function normBbox(b) {
  if (!b) return null
  let v = null
  if (Array.isArray(b) && b.length === 4 && b.every(Number.isFinite)) v = b
  else if (Array.isArray(b) && b.length === 2 && isPt(b[0])) v = [b[0][0], b[0][1], b[1][0], b[1][1]]
  else if (typeof b === 'object') {
    const w = b.west ?? b.min_lon ?? b.minLon
    const s = b.south ?? b.min_lat ?? b.minLat
    const e = b.east ?? b.max_lon ?? b.maxLon
    const n = b.north ?? b.max_lat ?? b.maxLat
    if ([w, s, e, n].every(Number.isFinite)) v = [w, s, e, n]
  }
  if (!v) return null
  if (v[0] > -30 && v[1] < -30) v = [v[1], v[0], v[3], v[2]] // lat-first
  return [Math.min(v[0], v[2]), Math.min(v[1], v[3]), Math.max(v[0], v[2]), Math.max(v[1], v[3])]
}
function kvOf(l) {
  const raw = l?.kv ?? l?.voltage ?? l?.properties?.kv ?? l?.properties?.voltage
  const nums = String(raw ?? '')
    .split(/[;,/ ]+/)
    .map(Number)
    .filter((n) => n > 0)
    .map((n) => (n >= 1000 ? n / 1000 : n))
  return nums.length ? Math.max(...nums) : 0
}
function stateEntries(states) {
  if (!states) return []
  if (Array.isArray(states)) return states.map((s, i) => [s.code || s.id || s.name || String(i), s.rings || s.coords || s.geometry || s])
  return Object.entries(states)
}

export default function PlansMap() {
  const g = useGridlock()
  const { basemap, projects, visible, overlaps, sel, hover, setHover, openOverlap, openProject, registerMap, byId } = g
  const wrapRef = useRef(null)
  const svgRef = useRef(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [view, setView] = useState(null) // {k, x, y}: screen = world * k + (x, y)
  const viewRef = useRef(null)
  const userMoved = useRef(false)
  const anim = useRef(0)
  const [tip, setTip] = useState(null)

  // --- projection ---
  // until the pipeline writes basemap.json the engine sends no outlines: draw the static Census ones
  const base = useMemo(() => {
    const d = basemap.data
    return stateEntries(d?.states).length ? d : { ...(d || {}), states: OUTLINES, bbox: null }
  }, [basemap.data])
  const bbox = useMemo(() => {
    const b = normBbox(base.bbox)
    if (b) return b
    const p = boundsOf((projects.list || []).flatMap(projectPoints))
    const o = OUTLINES_BBOX
    return p ? [Math.min(o[0], p[0]), Math.min(o[1], p[1]), Math.max(o[2], p[2]), Math.max(o[3], p[3])] : o
  }, [base, projects.list])
  const proj = useMemo(() => {
    const c = Math.cos((((bbox[1] + bbox[3]) / 2) * Math.PI) / 180)
    const w = (lon, lat) => [(lon - bbox[0]) * c, bbox[3] - lat]
    return { c, w, W: (bbox[2] - bbox[0]) * c, H: bbox[3] - bbox[1] }
  }, [bbox])

  // --- size ---
  useLayoutEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight })
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const fitView = useCallback(() => {
    if (!size.w || !size.h) return null
    const pad = 28
    const k = Math.min((size.w - pad * 2) / proj.W, (size.h - pad * 2) / proj.H)
    return { k, x: (size.w - proj.W * k) / 2, y: (size.h - proj.H * k) / 2 }
  }, [size, proj])
  const limits = useCallback(() => {
    const f = fitView()
    return f ? { min: f.k * 0.6, max: Math.max(f.k * 4, size.w / 0.35) } : { min: 1, max: 1e6 }
  }, [fitView, size.w])

  const commit = useCallback((v) => {
    viewRef.current = v
    setView(v)
  }, [])

  // fit on first layout and on resize until the user moves the map
  useEffect(() => {
    const f = fitView()
    if (f && (!userMoved.current || !viewRef.current)) commit(f)
  }, [fitView, commit])

  // --- camera moves ---
  const animateTo = useCallback(
    (target) => {
      cancelAnimationFrame(anim.current)
      const from = viewRef.current
      if (!from || REDUCED) return commit(target)
      // interpolate the world point under the screen centre and log(k)
      const a0 = [size.w / 2, size.h / 2]
      const c0 = [(a0[0] - from.x) / from.k, (a0[1] - from.y) / from.k]
      const c1 = [(a0[0] - target.x) / target.k, (a0[1] - target.y) / target.k]
      const t0 = performance.now()
      const dur = 650
      const step = (now) => {
        const t = Math.min(1, (now - t0) / dur)
        const e = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2
        const k = Math.exp(Math.log(from.k) + (Math.log(target.k) - Math.log(from.k)) * e)
        const c = [c0[0] + (c1[0] - c0[0]) * e, c0[1] + (c1[1] - c0[1]) * e]
        commit(t < 1 ? { k, x: a0[0] - c[0] * k, y: a0[1] - c[1] * k } : target)
        if (t < 1) anim.current = requestAnimationFrame(step)
      }
      anim.current = requestAnimationFrame(step)
    },
    [commit, size],
  )
  useEffect(() => () => cancelAnimationFrame(anim.current), [])

  // the visible part of the map: the detail card covers the right side on wide screens
  const insets = useCallback(
    (card) => {
      const wide = size.w > 700
      return { l: 40, t: 40, r: card && wide ? Math.min(420, size.w * 0.45) : 40, b: wide ? 90 : 40 }
    },
    [size.w],
  )

  const flyTo = useCallback(
    (b, { card = false } = {}) => {
      if (!b || !size.w) return
      userMoved.current = true
      const [x0, y0] = proj.w(b[0], b[3])
      const [x1, y1] = proj.w(b[2], b[1])
      const bw = Math.max(x1 - x0, 0.22)
      const bh = Math.max(y1 - y0, 0.16)
      const p = insets(card)
      const aw = Math.max(80, size.w - p.l - p.r)
      const ah = Math.max(80, size.h - p.t - p.b)
      const lim = limits()
      const k = Math.min(lim.max, Math.max(lim.min, Math.min(aw / bw, ah / bh)))
      const cx = (x0 + x1) / 2
      const cy = (y0 + y1) / 2
      animateTo({ k, x: p.l + aw / 2 - cx * k, y: p.t + ah / 2 - cy * k })
    },
    [proj, size, insets, limits, animateTo],
  )

  // pan (same zoom) only if the bounds are outside the visible part of the map
  const ensureVisible = useCallback(
    (b, { card = true } = {}) => {
      const v = viewRef.current
      if (!b || !v) return
      const [x0, y0] = proj.w(b[0], b[3])
      const [x1, y1] = proj.w(b[2], b[1])
      const p = insets(card)
      const sx0 = x0 * v.k + v.x
      const sx1 = x1 * v.k + v.x
      const sy0 = y0 * v.k + v.y
      const sy1 = y1 * v.k + v.y
      if (sx0 >= p.l && sx1 <= size.w - p.r && sy0 >= p.t && sy1 <= size.h - p.b) return
      const aw = size.w - p.l - p.r
      const ah = size.h - p.t - p.b
      animateTo({ k: v.k, x: p.l + aw / 2 - ((x0 + x1) / 2) * v.k, y: p.t + ah / 2 - ((y0 + y1) / 2) * v.k })
    },
    [proj, insets, size, animateTo],
  )

  const fit = useCallback(() => {
    userMoved.current = false
    const f = fitView()
    if (f) animateTo(f)
  }, [fitView, animateTo])

  const zoomAt = useCallback(
    (factor, sx, sy, smooth = false) => {
      const v = viewRef.current
      if (!v) return
      userMoved.current = true
      const lim = limits()
      const k = Math.min(lim.max, Math.max(lim.min, v.k * factor))
      const f = k / v.k
      const next = { k, x: sx - (sx - v.x) * f, y: sy - (sy - v.y) * f }
      if (smooth) animateTo(next)
      else commit(next)
    },
    [limits, commit, animateTo],
  )

  useEffect(() => {
    registerMap({ flyTo, ensureVisible, fit })
  }, [registerMap, flyTo, ensureVisible, fit])

  // --- wheel (non-passive so the page doesn't scroll) ---
  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const onWheel = (e) => {
      e.preventDefault()
      cancelAnimationFrame(anim.current)
      const r = el.getBoundingClientRect()
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY
      zoomAt(Math.exp(-dy * 0.0016), e.clientX - r.left, e.clientY - r.top)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [zoomAt])

  // --- drag to pan, two fingers to pinch ---
  const ptrs = useRef(new Map())
  const drag = useRef(null)
  const suppressClick = useRef(false)
  const onPointerDown = (e) => {
    if (e.button !== undefined && e.button !== 0) return
    cancelAnimationFrame(anim.current)
    const r = svgRef.current.getBoundingClientRect()
    ptrs.current.set(e.pointerId, [e.clientX - r.left, e.clientY - r.top])
    drag.current = { start: [...ptrs.current.values()].map((p) => [...p]), view: viewRef.current, moved: false, captured: false, id: e.pointerId }
    suppressClick.current = false
  }
  const onPointerMove = (e) => {
    const d = drag.current
    if (!d || !ptrs.current.has(e.pointerId)) return
    const r = svgRef.current.getBoundingClientRect()
    ptrs.current.set(e.pointerId, [e.clientX - r.left, e.clientY - r.top])
    const pts = [...ptrs.current.values()]
    if (pts.length >= 2 && d.start.length >= 2) {
      const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1])
      const f = dist(pts[0], pts[1]) / Math.max(1, dist(d.start[0], d.start[1]))
      const mid = [(pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2]
      const mid0 = [(d.start[0][0] + d.start[1][0]) / 2, (d.start[0][1] + d.start[1][1]) / 2]
      const lim = limits()
      const k = Math.min(lim.max, Math.max(lim.min, d.view.k * f))
      const wx = (mid0[0] - d.view.x) / d.view.k
      const wy = (mid0[1] - d.view.y) / d.view.k
      d.moved = true
      userMoved.current = true
      commit({ k, x: mid[0] - wx * k, y: mid[1] - wy * k })
      return
    }
    const [sx, sy] = d.start[0]
    const [cx, cy] = pts[0]
    if (!d.moved && Math.hypot(cx - sx, cy - sy) < 4) return
    if (!d.captured) {
      svgRef.current.setPointerCapture?.(e.pointerId)
      d.captured = true
    }
    d.moved = true
    userMoved.current = true
    setTip(null)
    commit({ k: d.view.k, x: d.view.x + cx - sx, y: d.view.y + cy - sy })
  }
  const onPointerUp = (e) => {
    ptrs.current.delete(e.pointerId)
    const d = drag.current
    if (d?.moved) suppressClick.current = true
    if (!ptrs.current.size) drag.current = null
    else if (d) drag.current = { ...d, start: [...ptrs.current.values()].map((p) => [...p]), view: viewRef.current }
  }
  const onClickCapture = (e) => {
    if (suppressClick.current) {
      e.stopPropagation()
      suppressClick.current = false
    }
  }
  const onKeyDown = (e) => {
    const v = viewRef.current
    if (!v) return
    const step = 70
    const pan = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key]
    if (pan) {
      e.preventDefault()
      userMoved.current = true
      commit({ ...v, x: v.x + pan[0], y: v.y + pan[1] })
    } else if (e.key === '+' || e.key === '=') zoomAt(1.5, size.w / 2, size.h / 2, true)
    else if (e.key === '-' || e.key === '_') zoomAt(1 / 1.5, size.w / 2, size.h / 2, true)
    else if (e.key === '0') fit()
    else if (e.key === 'Escape') g.close()
  }

  // --- what is highlighted ---
  const focus = useMemo(() => {
    const f = hover || (sel ? { kind: sel.kind, id: sel.id } : null)
    if (!f) return null
    if (f.kind === 'project') return { projects: new Set([f.id]), overlap: null }
    const o = overlaps.find((x) => x.id === f.id) || (sel?.kind === 'overlap' && sel.id === f.id ? sel.overlap : null)
    return o ? { projects: new Set([o.a, o.b]), overlap: o.id } : null
  }, [hover, sel, overlaps])

  const showTip = (e, content) => {
    const r = wrapRef.current.getBoundingClientRect()
    setTip({ x: e.clientX - r.left, y: e.clientY - r.top, content })
  }

  const k = view?.k || 1
  const loadingMsg = projects.status === 'loading' ? 'Loading projects…' : basemap.status === 'loading' ? 'Loading the map…' : null
  const selectedOverlap = sel?.kind === 'overlap' ? overlaps.find((o) => o.id === sel.id) || sel.overlap : null

  // draw order: other sponsors, Georgia Power, DESC, then whatever is highlighted on top
  const ordered = useMemo(() => {
    const order = { ga: 0, gpc: 1, desc: 2 }
    return [...visible].sort((a, b) => order[toneOf(a.utility)] - order[toneOf(b.utility)])
  }, [visible])
  const front = focus ? ordered.filter((p) => focus.projects.has(p.id)) : []
  const back = focus ? ordered.filter((p) => !focus.projects.has(p.id)) : ordered
  const drawnOverlaps = useMemo(
    () => [...overlaps].filter((o) => byId[o.a] && byId[o.b]).sort((a, b) => (TIER_RANK[b.tier] ?? 4) - (TIER_RANK[a.tier] ?? 4)),
    [overlaps, byId],
  )

  return (
    <div className="gl gl-map" ref={wrapRef} onPointerLeave={() => setTip(null)}>
      <svg
        ref={svgRef}
        className="gl-map__svg"
        width={size.w}
        height={size.h}
        tabIndex={0}
        role="application"
        aria-label="Map of planned transmission projects in South Carolina and Georgia. Drag to pan, scroll or press plus and minus to zoom, 0 to fit."
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onClickCapture={onClickCapture}
        onKeyDown={onKeyDown}
      >
        <defs>
          <radialGradient id="gl-glow">
            <stop offset="0%" stopColor="var(--gl-ring)" stopOpacity="0.3" />
            <stop offset="70%" stopColor="var(--gl-ring)" stopOpacity="0.08" />
            <stop offset="100%" stopColor="var(--gl-ring)" stopOpacity="0" />
          </radialGradient>
        </defs>
        {view && (
          <g transform={`translate(${view.x} ${view.y}) scale(${k})`}>
            <Basemap data={base} proj={proj} />
            <Labels proj={proj} k={k} states={base.states} />
            <g className={`gl-rings${focus ? ' gl-has-focus' : ''}`}>
              {drawnOverlaps.map((o) => (
                <OverlapRing
                  key={o.id}
                  o={o}
                  proj={proj}
                  k={k}
                  on={focus?.overlap === o.id}
                  onEnter={(e) => {
                    if (e.pointerType === 'touch') return
                    setHover({ kind: 'overlap', id: o.id })
                    showTip(e, <OverlapTip o={o} byId={byId} method={g.params.method} />)
                  }}
                  onLeave={() => {
                    setHover(null)
                    setTip(null)
                  }}
                  onClick={() => openOverlap(o)}
                />
              ))}
            </g>
            <g className={`gl-projects${focus ? ' gl-has-focus' : ''}`}>
              {[...back, ...front].map((p) => (
                <ProjectMark
                  key={p.id}
                  p={p}
                  proj={proj}
                  k={k}
                  on={!!focus?.projects.has(p.id)}
                  onEnter={(e) => {
                    if (e.pointerType === 'touch') return
                    setHover({ kind: 'project', id: p.id })
                    showTip(e, <ProjectTip p={p} />)
                  }}
                  onLeave={() => {
                    setHover(null)
                    setTip(null)
                  }}
                  onClick={() => openProject(p.id)}
                />
              ))}
            </g>
            <g className="gl-connectors">
              {drawnOverlaps.map((o) => (
                <Connector key={o.id} o={o} proj={proj} k={k} on={focus?.overlap === o.id} dim={!!focus && focus.overlap !== o.id} onClick={() => openOverlap(o)} />
              ))}
            </g>
            {focus && <FocusLabels ids={[...focus.projects]} byId={byId} proj={proj} view={view} right={size.w - insets(!!sel).r + 30} />}
            {selectedOverlap && <RankBadge o={selectedOverlap} proj={proj} k={k} />}
          </g>
        )}
      </svg>

      {tip && (
        <div className="gl-tip" style={{ left: Math.min(tip.x + 14, size.w - 250), top: Math.max(8, tip.y - 12) }} role="tooltip">
          {tip.content}
        </div>
      )}

      <div className="gl-zoom" role="group" aria-label="Zoom">
        <button type="button" onClick={() => zoomAt(1.6, size.w / 2, size.h / 2, true)} aria-label="Zoom in">
          +
        </button>
        <button type="button" onClick={() => zoomAt(1 / 1.6, size.w / 2, size.h / 2, true)} aria-label="Zoom out">
          −
        </button>
        <button type="button" className="gl-zoom__fit" onClick={fit}>
          Fit
        </button>
      </div>

      <Legend />

      <p className="gl-attrib">
        Outlines: U.S. Census Bureau. Lines and substations: ©{' '}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
          OpenStreetMap
        </a>{' '}
        contributors.
      </p>

      {(loadingMsg || basemap.status === 'error' || projects.status === 'error') && (
        <div className="gl-map__status">
          {loadingMsg && <Loading label={loadingMsg} />}
          {basemap.status === 'error' && <ErrorBanner error={new Error(`Map background: ${basemap.error.message}`)} onRetry={g.loadBasemap} />}
          {projects.status === 'error' && <ErrorBanner error={new Error(`Projects: ${projects.error.message}`)} onRetry={g.loadProjects} />}
        </div>
      )}
      {projects.status === 'ready' && !visible.length && (
        <div className="gl-map__status">
          <p className="muted">No projects for the utilities switched on.</p>
        </div>
      )}
    </div>
  )
}

// The static background: state outlines and OpenStreetMap transmission lines, drawn once.
const Basemap = memo(function Basemap({ data, proj }) {
  const paths = useMemo(() => {
    const d = (line) => line.map(([lon, lat], i) => `${i ? 'L' : 'M'}${proj.w(lon, lat).map((v) => v.toFixed(4)).join(' ')}`).join('')
    const states = stateEntries(data?.states).map(([code, rings]) => [code, polylines(rings).map((r) => `${d(r)}Z`).join('')])
    const lists = { hi: [], mid: [], lo: [] }
    const raw = Array.isArray(data?.lines) ? data.lines : data?.lines?.features || []
    for (const l of raw) {
      const kv = kvOf(l)
      const bucket = kv >= 345 ? 'hi' : kv >= 200 ? 'mid' : 'lo'
      for (const pl of polylines(l)) lists[bucket].push(d(pl))
    }
    return { states, lines: Object.fromEntries(Object.entries(lists).map(([key, v]) => [key, v.join('')])) }
  }, [data, proj])
  return (
    <g className="gl-base" aria-hidden="true">
      {paths.states.map(([code, dd]) => (
        <path key={code} className="gl-state" d={dd} />
      ))}
      {paths.lines.lo && <path className="gl-osm gl-osm--lo" d={paths.lines.lo} />}
      {paths.lines.mid && <path className="gl-osm gl-osm--mid" d={paths.lines.mid} />}
      {paths.lines.hi && <path className="gl-osm gl-osm--hi" d={paths.lines.hi} />}
    </g>
  )
})

function Labels({ proj, k, states }) {
  const stateLabels = useMemo(
    () =>
      stateEntries(states)
        .map(([code, rings]) => {
          const ring = polylines(rings).sort((a, b) => b.length - a.length)[0]
          if (!ring) return null
          const b = boundsOf(ring)
          // nudge toward the interior: SC's label reads best a little north of centre, GA's west
          const [x, y] = proj.w((b[0] + b[2]) / 2 + (code === 'GA' ? -0.35 : 0.1), (b[1] + b[3]) / 2 + (code === 'SC' ? 0.25 : 0.1))
          return { code, x, y }
        })
        .filter(Boolean),
    [states, proj],
  )
  return (
    <g className="gl-labels" aria-hidden="true">
      {stateLabels.map((s) => (
        <text key={s.code} className="gl-label-state" x={s.x} y={s.y} fontSize={13 / k} strokeWidth={4 / k} textAnchor="middle">
          {STATE_NAMES[s.code] || s.code}
        </text>
      ))}
      {CITIES.map(([name, lat, lon]) => {
        const [x, y] = proj.w(lon, lat)
        return (
          <g key={name}>
            <circle className="gl-city-dot" cx={x} cy={y} r={1.6 / k} />
            <text className="gl-label-city" x={x + 4 / k} y={y - 3 / k} fontSize={10.5 / k} strokeWidth={3 / k}>
              {name}
            </text>
          </g>
        )
      })}
    </g>
  )
}

function ProjectMark({ p, proj, k, on, onEnter, onLeave, onClick }) {
  const pts = projectPoints(p).map(([lon, lat]) => proj.w(lon, lat))
  if (!pts.length) return null
  const tone = toneOf(p.utility)
  const conf = p.confidence || 'low'
  const cls = `gl-proj gl-proj--${tone} gl-conf--${conf}${on ? ' gl-on' : ''}`
  const label = `${utilityShort(p.utility)}: ${p.name}`
  const line = pts.length >= 2
  const d = line ? pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x} ${y}`).join('') : null
  return (
    <g className={cls} onPointerEnter={onEnter} onPointerLeave={onLeave} onClick={onClick}>
      <title>{label}</title>
      {line ? (
        <>
          <path className="gl-proj__hit" d={d} />
          <path className="gl-proj__line" d={d} />
          {[pts[0], pts[pts.length - 1]].map(([x, y], i) => (
            <circle key={i} className="gl-proj__end" cx={x} cy={y} r={(on ? 3.4 : 2.6) / k} />
          ))}
        </>
      ) : (
        <>
          <circle className="gl-proj__hit" cx={pts[0][0]} cy={pts[0][1]} r={10 / k} />
          <circle className="gl-proj__pt" cx={pts[0][0]} cy={pts[0][1]} r={(on ? 6 : 4.5) / k} />
        </>
      )}
    </g>
  )
}

function worldPair(o, proj) {
  const cp = o.closest_points
  if (!cp || cp.length < 2) return null
  return cp.map(([lat, lon]) => proj.w(lon, lat))
}

function OverlapRing({ o, proj, k, on, onEnter, onLeave, onClick }) {
  const pair = worldPair(o, proj)
  if (!pair) return null
  const [[x1, y1], [x2, y2]] = pair
  const cx = (x1 + x2) / 2
  const cy = (y1 + y2) / 2
  const half = Math.hypot(x2 - x1, y2 - y1) / 2
  const r = Math.max(half + 8 / k, 13 / k)
  // Uncluttered (user, Sat 08:33: "hard to use the circles … there are so many and they overlap"): a pair is a
  // small mark at its midpoint (the top ten numbered like the list) until it's hovered or selected; only then
  // does its full ring show how close the two projects are.
  if (!on) {
    const rank = o.displayRank ?? o.rank
    return (
      <g className={`gl-ring gl-ring--${o.tier} gl-ring--mark`} onPointerEnter={onEnter} onPointerLeave={onLeave} onClick={onClick}>
        <title>{`Could coordinate: ${o.tier_label}${rank ? ` (#${rank})` : ''}`}</title>
        <circle className="gl-ring__hit" cx={cx} cy={cy} r={11 / k} />
        <circle className="gl-ring__mark" cx={cx} cy={cy} r={3.4 / k} />
        {rank != null && rank <= 10 && (
          <text className="gl-ring__rank" x={cx + 5.5 / k} y={cy - 5.5 / k} fontSize={10 / k}>
            {rank}
          </text>
        )}
      </g>
    )
  }
  return (
    <g className={`gl-ring gl-ring--${o.tier}${on ? ' gl-on' : ''}`} onPointerEnter={onEnter} onPointerLeave={onLeave} onClick={onClick}>
      <title>{`Could coordinate: ${o.tier_label}`}</title>
      <circle className="gl-ring__glow" cx={cx} cy={cy} r={r * 1.35} fill="url(#gl-glow)" />
      <circle className="gl-ring__edge" cx={cx} cy={cy} r={r} />
    </g>
  )
}

// zoomed in (where the ring is hidden) the connector itself is the click target
function Connector({ o, proj, k, on, dim, onClick }) {
  const pair = worldPair(o, proj)
  if (!pair) return null
  const [[x1, y1], [x2, y2]] = pair
  const hit = (Math.hypot(x2 - x1, y2 - y1) / 2 + 8 / k) * k > 110
  return (
    <g className={`gl-conn gl-conn--${o.tier}${on ? ' gl-on' : ''}${dim ? ' gl-dim' : ''}`} aria-hidden="true">
      {hit && <line className="gl-conn__hit" x1={x1} y1={y1} x2={x2} y2={y2} onClick={onClick} />}
      <line x1={x1} y1={y1} x2={x2} y2={y2} />
      <circle cx={x1} cy={y1} r={(on ? 2.8 : 2) / k} />
      <circle cx={x2} cy={y2} r={(on ? 2.8 : 2) / k} />
    </g>
  )
}

// The highlighted project(s), named on the map: at the middle of each line (or beside the point),
// the second one nudged below so a pair that meets doesn't print on top of itself, and right-anchored
// when it would run under the detail card (`right`: the visible map's right edge, screen px).
function FocusLabels({ ids, byId, proj, view, right }) {
  const { k } = view
  const placed = []
  return (
    <g aria-hidden="true">
      {ids.slice(0, 2).map((id) => {
        const p = byId[id]
        const pts = projectPoints(p).map(([lon, lat]) => proj.w(lon, lat))
        if (!pts.length) return null
        const a = pts[0]
        const b = pts[pts.length - 1]
        const mx = (a[0] + b[0]) / 2
        let y = (a[1] + b[1]) / 2 - 7 / k
        const CH = /[a-z]/.test(p.name) ? 6.6 : 7.8 // px per character at 11.5 px (capitals run wider)
        const sx = mx * k + view.x
        const roomR = right - sx - 9
        const roomL = sx - 9
        // right of the anchor unless it doesn't fit there and the left has more room; cut to fit
        const flip = p.name.length * CH > roomR && roomL > roomR
        const fits = Math.max(12, Math.min(42, Math.floor((flip ? roomL : roomR) / CH)))
        const name = p.name.length > fits ? `${p.name.slice(0, fits - 1)}…` : p.name
        const x = flip ? mx - 9 / k : mx + 9 / k
        if (placed.some(([px, py]) => Math.abs(px - x) < 160 / k && Math.abs(py - y) < 16 / k)) y += 20 / k
        placed.push([x, y])
        return (
          <text
            key={id}
            className={`gl-focus-label gl-focus-label--${toneOf(p.utility)}`}
            x={x}
            y={y}
            textAnchor={flip ? 'end' : 'start'}
            fontSize={11.5 / k}
            strokeWidth={3.5 / k}
          >
            {name}
          </text>
        )
      })}
    </g>
  )
}

function RankBadge({ o, proj, k }) {
  const pair = worldPair(o, proj)
  if (!pair) return null
  const [[x1, y1], [x2, y2]] = pair
  const half = Math.hypot(x2 - x1, y2 - y1) / 2
  const r = Math.max(half + 8 / k, 13 / k)
  // lower left of the pair: the project names are labelled up and to the right
  const cx = (x1 + x2) / 2 - r * 0.72
  const cy = (y1 + y2) / 2 + r * 0.72
  const rank = o.displayRank ?? o.rank
  return (
    <g className="gl-badge" aria-hidden="true">
      <circle cx={cx} cy={cy} r={9 / k} strokeWidth={1.5 / k} />
      <text x={cx} y={cy} fontSize={10 / k} dy={3.5 / k} textAnchor="middle">
        {rank}
      </text>
    </g>
  )
}

function ProjectTip({ p }) {
  return (
    <>
      <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
      <strong>{utilityShort(p.utility)}</strong> {p.name}
      <span className="gl-tip__sub">
        {p.in_service ? `In service ${p.in_service}` : 'No in-service date'} · location {p.confidence || 'unknown'} confidence
      </span>
    </>
  )
}

function OverlapTip({ o, byId, method }) {
  return (
    <>
      <strong>
        #{o.displayRank ?? o.rank} · {o.tier_label}
      </strong>
      <span className="gl-tip__sub">
        {fmtPairDistance(o, method)}: {byId[o.a]?.name} and {byId[o.b]?.name}
      </span>
    </>
  )
}

function Legend() {
  const g = useGridlock()
  // open on wide screens, folded on a phone (set once, so the reader's own toggle sticks)
  const [openAtStart] = useState(() => !window.matchMedia?.('(max-width: 760px)').matches)
  const others = ['GTC', 'MEAG', 'DU'].filter((u) => g.params.utilities[u])
  return (
    <details className="gl-legend" open={openAtStart}>
      <summary>Legend</summary>
      <ul>
        {g.params.utilities.DESC && (
          <li>
            <LegendLine tone="desc" /> Dominion Energy South Carolina
          </li>
        )}
        {g.params.utilities.GPC && (
          <li>
            <LegendLine tone="gpc" /> Georgia Power
          </li>
        )}
        {others.length > 0 && (
          <li>
            <LegendLine tone="ga" /> {others.map(utilityShort).join(', ')}
          </li>
        )}
        <li>
          <LegendLine tone="plain" dashed /> Approximate location
        </li>
        <li>
          <svg width="26" height="14" aria-hidden="true">
            <circle cx="13" cy="7" r="3.4" className="gl-legend__mark" />
          </svg>
          A pair that could coordinate (closer is brighter; the top ten numbered like the list; hover for its ring)
        </li>
        <li>
          <LegendLine tone="osm" /> Existing lines, 115 kV and up
        </li>
      </ul>
    </details>
  )
}

function LegendLine({ tone, dashed }) {
  return (
    <svg width="26" height="14" aria-hidden="true">
      <line x1="2" y1="7" x2="24" y2="7" className={`gl-legend__line gl-legend__line--${tone}`} strokeDasharray={dashed ? '4 3' : undefined} />
      {tone !== 'osm' && tone !== 'plain' && <circle cx="24" cy="7" r="2.4" className={`gl-legend__dot gl-legend__line--${tone}`} />}
    </svg>
  )
}
