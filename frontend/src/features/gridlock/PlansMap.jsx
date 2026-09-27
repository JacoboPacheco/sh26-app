import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ErrorBanner, Loading } from '../../ui'
import { useGridlock } from './context'
import { OUTLINES, OUTLINES_BBOX } from './outlines'
import { boundsOf, displayName, fmtPairDistance, projectPoints, toneOf, utilityShort } from './format'
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
// where a badge's list of pairs goes: below the badge (its text baseline at y; the pill spans y-10..y+4) when there is
// room, else on the side with more room, anchored to the badge so a shorter list stays next to it; capped at 360 px
function popPlace(cluster, h) {
  const want = Math.min(360, 44 + cluster.ids.length * 58)
  const below = h - 16 - (cluster.y + 10)
  const above = cluster.y - 14 - 8
  if (below >= want || below >= above) return { top: cluster.y + 10, maxHeight: Math.max(120, Math.min(want, below)) }
  return { bottom: h - (cluster.y - 14), maxHeight: Math.min(want, above) }
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
  const { basemap, projects, sel, hover, setHover, openOverlap, openProject, registerMap, byId, cover } = g
  // Sperry's worked example as the start (g.sperryMap): only their ten projects and six pairs, with their own numbers
  const visible = g.sperryMap ? g.sperryMap.projects : g.visible
  const overlaps = g.sperryMap ? g.sperryMap.overlaps : g.overlaps
  // after expanding to the full filings, Sperry's six stay marked (and numbered) wherever they rank
  const marks = !g.sperryMap && g.sperryMarks
  // the sheet covers the map's right side: camera moves frame what is left visible (read through a ref so a
  // fly that runs in the same commit as the sheet's first measurement already sees it)
  const coverRef = useRef(cover)
  useLayoutEffect(() => {
    coverRef.current = cover
  }, [cover])
  const wrapRef = useRef(null)
  const svgRef = useRef(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [view, setView] = useState(null) // {k, x, y}: screen = world * k + (x, y)
  const viewRef = useRef(null)
  const userMoved = useRef(false)
  const anim = useRef(0)
  const [tip, setTip] = useState(null)
  // several top-ten pairs meeting at one place share one badge ("1 +3"); hovering (or tapping) it lists them
  const [cluster, setCluster] = useState(null) // {x, y, ids}: screen px
  const clusterTimer = useRef(0)
  const keepCluster = () => clearTimeout(clusterTimer.current)
  const dropCluster = () => {
    clearTimeout(clusterTimer.current)
    clusterTimer.current = setTimeout(() => setCluster(null), 220)
  }
  useEffect(() => () => clearTimeout(clusterTimer.current), [])

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
    // a hidden map (printing, a collapsed parent) measures 0: keep the last real size so the view survives
    const measure = () => el.clientWidth && el.clientHeight && setSize({ w: el.clientWidth, h: el.clientHeight })
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

  // the visible part of the map: the sheet (or, without it, a project's card) covers the right side
  const insets = useCallback(
    (card) => {
      const wide = size.w > 700
      const c = coverRef.current
      const r = c > 0 && c < size.w - 160 ? c + 36 : card && wide ? Math.min(420, size.w * 0.45) : 40
      return { l: 64, t: 36, r, b: wide ? 64 : 40 }
    },
    [size.w],
  )

  const flyTo = useCallback(
    (b, { card = false, instant = false } = {}) => {
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
      const target = { k, x: p.l + aw / 2 - cx * k, y: p.t + ah / 2 - cy * k }
      if (instant) {
        cancelAnimationFrame(anim.current)
        commit(target)
      } else animateTo(target)
    },
    [proj, size, insets, limits, animateTo, commit],
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
    setCluster(null)
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
    else if (e.key === 'Escape' && cluster) setCluster(null)
    else if (e.key === 'Escape' && !g.draft) g.close()
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
  // the right edge of the part of the map not under the sheet (or a project's card), screen px
  const visibleRight =
    cover > 0 && cover < size.w - 160 ? size.w - cover - 6 : sel?.kind === 'project' && !g.draft && size.w > 700 ? size.w - Math.min(420, size.w * 0.45) + 30 : size.w - 10
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
  const stationOverlaps = useMemo(
    () => drawnOverlaps.filter((o) => o.shared_station).sort((a, b) => (b.displayRank ?? b.rank ?? 0) - (a.displayRank ?? a.rank ?? 0)),
    [drawnOverlaps],
  )
  // a pair's mark or ring: hover shows its tip (not on touch, where a tap opens it), a click opens it
  const ringHandlers = (o) => ({
    onEnter: (e) => {
      if (e.pointerType === 'touch') return
      setHover({ kind: 'overlap', id: o.id })
      showTip(e, <OverlapTip o={o} byId={byId} method={g.params.method} />)
    },
    onLeave: () => {
      setHover(null)
      setTip(null)
    },
    onClick: () => openOverlap(o),
  })
  const rankSpots = useMemo(() => placeRanks(drawnOverlaps, proj, k, marks), [drawnOverlaps, proj, k, marks])
  const focusPlaced = focus && view ? placeFocusLabels([...focus.projects], byId, proj, view, visibleRight) : []
  // the highlighted pair's shared substation (a same-station pair): marked on the map and named
  const focusOverlap = focus?.overlap ? overlaps.find((x) => x.id === focus.overlap) || (selectedOverlap?.id === focus.overlap ? selectedOverlap : null) : null
  const station = focusOverlap?.shared_station && view ? placeStation(focusOverlap, byId, proj, k) : null

  return (
    <div className="gl gl-map" ref={wrapRef} onPointerLeave={() => setTip(null)} style={{ '--gl-cover': `${cover || 0}px` }}>
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
        // a click (or tap) anywhere else on the map closes a badge's list (the badge's own click doesn't reach here)
        onClick={() => cluster && setCluster(null)}
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
                <OverlapRing key={o.id} o={o} proj={proj} k={k} on={focus?.overlap === o.id} marked={marks && !!o.sperry} {...ringHandlers(o)} />
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
            {/* same-station pairs sit above the project marks: a project located at one point (DESC's Hooks - Thurmond
                tie has one located endpoint) is drawn exactly on the station and would take the pair's hover and click.
                The target stays while the pair is highlighted (StationMark draws the square then), so the pointer never
                falls through to that project; the best rank is drawn last, on top of a pair at the same station. */}
            {stationOverlaps.length > 0 && (
              <g className={`gl-rings gl-rings--station${focus ? ' gl-has-focus' : ''}`}>
                {stationOverlaps.map((o) => (
                  <StationPairMark key={o.id} o={o} proj={proj} k={k} on={focus?.overlap === o.id} {...ringHandlers(o)} />
                ))}
              </g>
            )}
            {/* the top ten's ranks above every line and mark (with a dark halo), so none hides under a project; pairs that
                meet at one place share one badge ("1 +3") that lists them on hover */}
            <g className={`gl-ranks${focus ? ' gl-has-focus' : ''}`} aria-hidden="true">
              {[...rankSpots].map(([id, spot]) =>
                spot.ids ? (
                  <g
                    key={id}
                    className={`gl-cluster${cluster?.key === id ? ' is-open' : ''}`}
                    onPointerEnter={() => {
                      keepCluster()
                      setTip(null)
                      setCluster({ key: id, ids: spot.ids, x: spot.x + (view?.x || 0), y: spot.y + (view?.y || 0) })
                    }}
                    // a touch sends pointerleave right after the tap: only a mouse leaving closes the list (a tap
                    // elsewhere on the map closes it on touch)
                    onPointerLeave={(e) => e.pointerType === 'mouse' && dropCluster()}
                    onClick={(e) => {
                      e.stopPropagation()
                      keepCluster()
                      setCluster({ key: id, ids: spot.ids, x: spot.x + (view?.x || 0), y: spot.y + (view?.y || 0) })
                    }}
                  >
                    <rect
                      className="gl-cluster__pill"
                      x={(spot.end ? spot.x - spot.w : spot.x) / k}
                      y={(spot.y - 10) / k}
                      width={spot.w / k}
                      height={14 / k}
                      rx={7 / k}
                      strokeWidth={1 / k}
                    />
                    <text
                      className="gl-cluster__text"
                      x={(spot.end ? spot.x - spot.w / 2 : spot.x + spot.w / 2) / k}
                      y={spot.y / k}
                      fontSize={10 / k}
                      textAnchor="middle"
                    >
                      {spot.text}
                    </text>
                  </g>
                ) : focus?.overlap === id ? null : (
                  <text
                    key={id}
                    className="gl-ring__rank"
                    x={spot.x / k}
                    y={spot.y / k}
                    fontSize={10 / k}
                    strokeWidth={3 / k}
                    textAnchor={spot.end ? 'end' : 'start'}
                  >
                    {spot.text}
                  </text>
                ),
              )}
            </g>
            {station && <StationMark st={station} k={k} />}
            {focus && <FocusLabels placed={focusPlaced} k={k} />}
            {selectedOverlap && <RankBadge o={selectedOverlap} proj={proj} k={k} avoid={station ? [...focusPlaced, station] : focusPlaced} />}
          </g>
        )}
      </svg>

      {tip && (
        <div className="gl-tip" style={{ left: Math.max(8, Math.min(tip.x + 14, size.w - (cover > 0 ? cover : 0) - 258)), top: Math.max(8, tip.y - 12) }} role="tooltip">
          {tip.content}
        </div>
      )}
      {cluster && (
        <div
          className="gl-cluster-pop"
          style={{
            left: Math.max(8, Math.min(cluster.x - 16, size.w - (cover > 0 ? cover : 0) - 300)),
            // below the badge when it fits, else above it; never over it (a tap's click then landed on the list and
            // opened a pair). Past the room it has, the list scrolls.
            ...popPlace(cluster, size.h),
          }}
          onPointerEnter={keepCluster}
          onPointerLeave={(e) => e.pointerType === 'mouse' && dropCluster()}
        >
          <p className="gl-cluster-pop__h">{cluster.ids.length} top pairs meet here</p>
          <ul>
            {cluster.ids.map((id) => {
              const o = overlaps.find((x) => x.id === id)
              if (!o) return null
              return (
                <li key={id}>
                  <button
                    type="button"
                    onClick={() => {
                      setCluster(null)
                      openOverlap(o)
                    }}
                    onFocus={keepCluster}
                  >
                    <strong>#{rankOf(o)}</strong>
                    <span>
                      {displayName(byId[o.a]?.name)} <span aria-hidden="true">×</span> {displayName(byId[o.b]?.name)}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      <div className="gl-zoom" role="group" aria-label="Zoom">
        <button type="button" onClick={() => zoomAt(1.6, (size.w - cover) / 2, size.h / 2, true)} aria-label="Zoom in" title="Zoom in">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 3v10M3 8h10" />
          </svg>
        </button>
        <button type="button" onClick={() => zoomAt(1 / 1.6, (size.w - cover) / 2, size.h / 2, true)} aria-label="Zoom out" title="Zoom out">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3 8h10" />
          </svg>
        </button>
        <button type="button" onClick={fit} aria-label="Fit both states" title="Fit both states">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10" />
          </svg>
        </button>
      </div>

      <div className="gl-mapfoot">
        <MapKey />
        <LegendChip />
        <p className="gl-attrib">
          Outlines: U.S. Census Bureau. Lines and substations: ©{' '}
          <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
            OpenStreetMap
          </a>{' '}
          contributors.
        </p>
      </div>

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
  const label = `${utilityShort(p.utility)}: ${displayName(p.name)}`
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

// the number a pair carries on the map: Sperry's own (OVL_n) at the start, else its rank in the list
const rankOf = (o) => o.mapRank ?? o.displayRank ?? o.rank
const rankText = (o) => (o.mapRank != null && o.sperry ? `Sperry ${o.sperry}` : `#${rankOf(o)}`)

function worldPair(o, proj) {
  const cp = o.closest_points
  if (!cp || cp.length < 2) return null
  return cp.map(([lat, lon]) => proj.w(lon, lat))
}

function OverlapRing({ o, proj, k, on, marked, onEnter, onLeave, onClick }) {
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
    // a same-station pair rests as a small square above the project marks (StationPairMark), every other pair as a dot
    if (o.shared_station) return null
    return (
      <g className={`gl-ring gl-ring--${o.tier} gl-ring--mark${marked ? ' gl-ring--sperry' : ''}`} onPointerEnter={onEnter} onPointerLeave={onLeave} onClick={onClick}>
        <title>{`Could coordinate: ${o.tier_label} (${rankText(o)})`}</title>
        <circle className="gl-ring__hit" cx={cx} cy={cy} r={11 / k} />
        {marked && <circle className="gl-ring__sperry" cx={cx} cy={cy} r={7 / k} />}
        <circle className="gl-ring__mark" cx={cx} cy={cy} r={3.4 / k} />
      </g>
    )
  }
  return (
    <g className={`gl-ring gl-ring--${o.tier}${on ? ' gl-on' : ''}`} onPointerEnter={onEnter} onPointerLeave={onLeave} onClick={onClick}>
      <title>{o.shared_station ? `Same station: ${o.shared_station.name}` : `Could coordinate: ${o.tier_label}`}</title>
      <circle className="gl-ring__glow" cx={cx} cy={cy} r={r * 1.35} fill="url(#gl-glow)" />
      <circle className="gl-ring__edge" cx={cx} cy={cy} r={r} />
    </g>
  )
}

// A same-station pair's target, drawn above the project marks: a small square (a substation) at rest; while the pair
// is highlighted only its invisible target stays (StationMark draws the station), so hovering never flickers onto the
// project point underneath. Slightly smaller than a dot's target so a point project there keeps a sliver of its own.
function StationPairMark({ o, proj, k, on, onEnter, onLeave, onClick }) {
  const pair = worldPair(o, proj)
  if (!pair) return null
  const [[x1, y1], [x2, y2]] = pair
  const cx = (x1 + x2) / 2
  const cy = (y1 + y2) / 2
  return (
    <g
      className={`gl-ring gl-ring--${o.tier} gl-ring--station${on ? ' gl-on' : ' gl-ring--mark'}`}
      onPointerEnter={onEnter}
      onPointerLeave={onLeave}
      onClick={onClick}
    >
      <title>{`Same station: ${o.shared_station.name} (${rankText(o)})`}</title>
      <circle className="gl-ring__hit" cx={cx} cy={cy} r={9 / k} />
      {!on && <rect className="gl-ring__mark" x={cx - 3.6 / k} y={cy - 3.6 / k} width={7.2 / k} height={7.2 / k} />}
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

// The top ten pairs carry their rank beside their mark. Placed in screen px at this zoom, best rank first: the first
// of six spots around the mark that overlaps no rank placed before it, no other mark and no city label; with none
// free, the rank joins the label it would have covered ("1, 3": two pairs meet there), so digits never run together.
const RANK_SPOTS = [
  [5.5, -5.5, false],
  [-5.5, -5.5, true],
  [5.5, 12, false],
  [-5.5, 12, true],
  [7, 3.5, false],
  [-7, 3.5, true],
]
function placeRanks(overlaps, proj, k, keepSperry = false) {
  const out = new Map()
  const top = overlaps
    .map((o) => ({ o, rank: rankOf(o), pair: worldPair(o, proj) }))
    .filter((x) => x.pair && x.rank != null && (x.rank <= 10 || (keepSperry && x.o.sperry)))
    .sort((a, b) => a.rank - b.rank)
  if (!top.length) return out
  const hits = (a, b) => a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]
  const cities = CITIES.flatMap(([name, lat, lon]) => {
    const [x, y] = proj.w(lon, lat)
    const sx = x * k
    const sy = y * k
    return [
      [sx + 3, sy - 13, sx + 4 + name.length * 5.9, sy - 1],
      [sx - 2.5, sy - 2.5, sx + 2.5, sy + 2.5],
    ]
  })
  const marks = top.map(({ pair: [[x1, y1], [x2, y2]] }) => [((x1 + x2) / 2) * k, ((y1 + y2) / 2) * k])
  const placed = [] // {box, spot}
  // marks within a few px of each other at this zoom are one place: one badge for all of them, best rank first
  const CLUSTER_PX = 15
  const groups = []
  top.forEach((x, i) => {
    const near = groups.find((gr) => gr.members.some((j) => Math.hypot(marks[j][0] - marks[i][0], marks[j][1] - marks[i][1]) < CLUSTER_PX))
    if (near) near.members.push(i)
    else groups.push({ members: [i] })
  })
  const lead = new Map(groups.filter((gr) => gr.members.length > 1).map((gr) => [gr.members[0], gr.members]))
  const inCluster = new Set(groups.filter((gr) => gr.members.length > 1).flatMap((gr) => gr.members.slice(1)))
  top.forEach(({ o, rank }, i) => {
    if (inCluster.has(i)) return
    const members = lead.get(i)
    if (members) {
      const cx = members.reduce((t, j) => t + marks[j][0], 0) / members.length
      const cy = members.reduce((t, j) => t + marks[j][1], 0) / members.length
      const text = `${rank} +${members.length - 1}`
      const w = text.length * 6.2 + 10
      const at = ([dx, dy, end]) => [end ? cx + dx - w : cx + dx, cy + dy - 10, end ? cx + dx : cx + dx + w, cy + dy + 4]
      const pick = RANK_SPOTS.find((c) => !placed.some((p) => hits(at(c), p.box)) && !cities.some((cb) => hits(at(c), cb))) || RANK_SPOTS[0]
      const spot = { x: cx + pick[0], y: cy + pick[1], end: pick[2], text, w, ids: members.map((j) => top[j].o.id) }
      out.set(o.id, spot)
      placed.push({ box: at(pick), spot })
      return
    }
    const [mx, my] = marks[i]
    const w = String(rank).length * 6.2 + 1
    const boxAt = ([dx, dy, end]) => [end ? mx + dx - w : mx + dx, my + dy - 8, end ? mx + dx : mx + dx + w, my + dy + 2]
    const clearOfRanks = (c) => !placed.some((p) => hits(boxAt(c), p.box))
    const clearOfMap = (c) => !cities.some((cb) => hits(boxAt(c), cb)) && !marks.some(([x, y], j) => j !== i && hits(boxAt(c), [x - 4, y - 4, x + 4, y + 4]))
    const pick = RANK_SPOTS.find((c) => clearOfRanks(c) && clearOfMap(c)) || RANK_SPOTS.find(clearOfRanks)
    if (pick) {
      const spot = { x: mx + pick[0], y: my + pick[1], end: pick[2], text: String(rank) }
      out.set(o.id, spot)
      placed.push({ box: boxAt(pick), spot })
      return
    }
    // every spot covers a rank already placed (pairs that meet at one place): join that label
    const near = placed.find((p) => hits(boxAt(RANK_SPOTS[0]), p.box)) || placed.find((p) => RANK_SPOTS.some((c) => hits(boxAt(c), p.box)))
    near.spot.text = `${near.spot.text}, ${rank}`
    const add = (String(rank).length + 2) * 6.2
    near.box = near.spot.end ? [near.box[0] - add, near.box[1], near.box[2], near.box[3]] : [near.box[0], near.box[1], near.box[2] + add, near.box[3]]
  })
  return out
}

// The highlighted project(s), named on the map: at the middle of each line (or beside the point),
// the second one nudged below so a pair that meets doesn't print on top of itself, and right-anchored
// when it would run under the detail card (`right`: the visible map's right edge, screen px).
// Returns each label with its box in world units (the selected pair's badge keeps clear of them).
function placeFocusLabels(ids, byId, proj, view, right) {
  const { k } = view
  const placed = []
  const out = []
  for (const id of ids.slice(0, 2)) {
    const p = byId[id]
    const pts = projectPoints(p).map(([lon, lat]) => proj.w(lon, lat))
    if (!pts.length) continue
    const a = pts[0]
    const b = pts[pts.length - 1]
    const mx = (a[0] + b[0]) / 2
    let y = (a[1] + b[1]) / 2 - 7 / k
    const CH = 6.6 // px per character at 11.5 px (names are title-cased for display)
    const sx = mx * k + view.x
    const roomR = right - sx - 9
    const roomL = sx - 9
    // right of the anchor unless it doesn't fit there and the left has more room; cut to fit
    const full = displayName(p.name)
    const flip = full.length * CH > roomR && roomL > roomR
    const fits = Math.max(12, Math.min(42, Math.floor((flip ? roomL : roomR) / CH)))
    const name = full.length > fits ? `${full.slice(0, fits - 1)}…` : full
    const x = flip ? mx - 9 / k : mx + 9 / k
    // the text's extent (a flipped label ends at x): a second label that would overlap the first moves below it
    const w = (name.length * CH) / k
    const x0 = flip ? x - w : x
    const x1 = flip ? x : x + w
    const hit = placed.find(([a0, a1, py]) => x0 < a1 && x1 > a0 && Math.abs(py - y) < 17 / k)
    if (hit) y = hit[2] + 19 / k
    placed.push([x0, x1, y])
    out.push({ id, tone: toneOf(p.utility), name, x, y, flip, box: [x0, y - 10 / k, x1, y + 3.5 / k] })
  }
  return out
}

function FocusLabels({ placed, k }) {
  return (
    <g aria-hidden="true">
      {placed.map((l) => (
        <text
          key={l.id}
          className={`gl-focus-label gl-focus-label--${l.tone}`}
          x={l.x}
          y={l.y}
          textAnchor={l.flip ? 'end' : 'start'}
          fontSize={11.5 / k}
          strokeWidth={3.5 / k}
        >
          {l.name}
        </text>
      ))}
    </g>
  )
}

// A same-station pair's shared substation: where it is, and its name under it, on the side away from the two
// projects (so the label doesn't sit on their lines). Returns the label's box in world units for the rank badge.
const STATION_SUB = 'Same station in both filings'
function placeStation(o, byId, proj, k) {
  const s = o.shared_station
  const [x, y] = proj.w(s.lon, s.lat)
  const pts = [...projectPoints(byId[o.a]), ...projectPoints(byId[o.b])].map(([lon, lat]) => proj.w(lon, lat))
  const left = pts.reduce((t, p) => t + (p[0] - x), 0) > 0
  const lx = left ? x - 13 / k : x + 13 / k
  const w = Math.max(s.name.length * 6.8, STATION_SUB.length * 5.4) / k
  const box = left ? [lx - w, y + 10 / k, lx, y + 38 / k] : [lx, y + 10 / k, lx + w, y + 38 / k]
  return { x, y, lx, left, name: s.name, box }
}

function StationMark({ st, k }) {
  const h = 5.5 / k
  const anchor = st.left ? 'end' : 'start'
  return (
    <g className="gl-station" aria-hidden="true">
      <circle className="gl-station__halo" cx={st.x} cy={st.y} r={17 / k} />
      <line className="gl-station__tick" x1={st.x} y1={st.y + h} x2={st.lx + (st.left ? 2 / k : -2 / k)} y2={st.y + 16 / k} strokeWidth={1 / k} />
      <rect className="gl-station__sq" x={st.x - h} y={st.y - h} width={2 * h} height={2 * h} strokeWidth={1.6 / k} />
      <text className="gl-station__name" x={st.lx} y={st.y + 22 / k} textAnchor={anchor} fontSize={11.5 / k} strokeWidth={3.5 / k}>
        {st.name}
      </text>
      <text className="gl-station__sub" x={st.lx} y={st.y + 35 / k} textAnchor={anchor} fontSize={10 / k} strokeWidth={3 / k}>
        {STATION_SUB}
      </text>
    </g>
  )
}

function RankBadge({ o, proj, k, avoid = [] }) {
  const pair = worldPair(o, proj)
  if (!pair) return null
  const [[x1, y1], [x2, y2]] = pair
  const half = Math.hypot(x2 - x1, y2 - y1) / 2
  // a same-station pair: out on the station's halo, clear of its square
  const r = Math.max(half + 8 / k, (o.shared_station ? 26 : 13) / k)
  // lower left of the pair (the project names are labelled up and to the right), else the first corner of the ring
  // clear of both names
  const mx = (x1 + x2) / 2
  const my = (y1 + y2) / 2
  const R = 10.5 / k
  const clear = ([cx, cy]) => !avoid.some(({ box }) => cx - R < box[2] && cx + R > box[0] && cy - R < box[3] && cy + R > box[1])
  const corners = [
    [-1, 1],
    [1, 1],
    [-1, -1],
    [1, -1],
  ].map(([sx, sy]) => [mx + sx * r * 0.72, my + sy * r * 0.72])
  const [cx, cy] = corners.find(clear) || corners[0]
  const rank = rankOf(o)
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
      <strong>{utilityShort(p.utility)}</strong> {displayName(p.name)}
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
        {rankText(o)} · {o.shared_station ? `Same station: ${o.shared_station.name}` : o.tier_label}
      </strong>
      <span className="gl-tip__sub">
        {fmtPairDistance(o, method)}: {displayName(byId[o.a]?.name)} and {displayName(byId[o.b]?.name)}
      </span>
    </>
  )
}

// The two utilities' colors, always in view beside the map key (the key itself opens for the rest).
function LegendChip() {
  const g = useGridlock()
  const georgia = ['GPC', 'GTC', 'MEAG', 'DU'].filter((u) => g.params.utilities[u])
  return (
    <p className="gl-legendchip" aria-label="Colors on the map">
      {g.params.utilities.DESC && (
        <span title="Dominion Energy South Carolina">
          <span className="gl-swatch gl-swatch--desc" aria-hidden="true" /> DESC (SC)
        </span>
      )}
      {georgia.includes('GPC') && (
        <span>
          <span className="gl-swatch gl-swatch--gpc" aria-hidden="true" /> Georgia Power (GA)
        </span>
      )}
      {georgia.some((u) => u !== 'GPC') && (
        <span>
          <span className="gl-swatch gl-swatch--ga" aria-hidden="true" />{' '}
          {georgia
            .filter((u) => u !== 'GPC')
            .map((u) => utilityShort(u))
            .join(', ')}{' '}
          (GA)
        </span>
      )}
    </p>
  )
}

// The map key: a small chip, closed by default, that opens upward over the map's lower left.
function MapKey() {
  const g = useGridlock()
  const others = ['GTC', 'MEAG', 'DU'].filter((u) => g.params.utilities[u])
  return (
    <details className="gl-key">
      <summary>Map key</summary>
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
          A pair that could build together (brighter is closer; the top ten carry their rank, a badge like &ldquo;1 +3&rdquo; where several meet)
        </li>
        <li>
          <svg width="26" height="14" aria-hidden="true">
            <rect x="9.4" y="3.4" width="7.2" height="7.2" className="gl-legend__mark" />
          </svg>
          A substation both filings work at (listed first in its group)
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
