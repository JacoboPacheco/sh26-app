// FEATURE: "Harden before the storm" on the map (SVG inside the camera; App.jsx mounts it with the other layers).
// The plan's lines draw in green, then the storm crosses again: the eye travels its path and every line it reaches
// goes down in red, except the hardened ones, which hold. Once it has passed, the areas the plan keeps on light up
// green and the ones still out stay red. CSS animations delayed by where along the path the storm reaches each line
// (the backend's along_km); Pause freezes them (animation-play-state) and the eye. Reduced motion: the end state.
// Everything drawn comes from the plan's result (backend/harden.py): the engine's re-run of the storm with it.
import { useEffect, useMemo, useRef } from 'react'
import { useMapView } from '../../GridMap'
import { useOverload } from '../../store'
import { useHurricane } from '../hurricane/hurricaneStore'
import { MU_PER_KM, pathOnMap } from '../hurricane/trackGeom'
import './harden.css'
import { SWEEP_MS, closeMap, planOf, reducedMotion, replayAt, resetHarden, useHarden } from './hardenStore'

export default function HardenLayer() {
  const O = useOverload()
  const { branchById, subPos, cascade, mode } = O
  const { k, project } = useMapView()
  const h = useHarden()
  const { hits } = useHurricane()

  // the plan belongs to the storm and the case it was made for: a new landfall, a cleared storm, a changed case
  // (a campus moved, the time of day) drops it; the Strengthen page hides it
  useEffect(() => {
    if (h.status === 'idle') return
    if (h.forHits !== hits || (h.forCascade && cascade && h.forCascade !== cascade) || (!cascade && h.status === 'done')) resetHarden()
  }, [h.status, h.forHits, h.forCascade, hits, cascade])
  // its controls live in hurricane mode's panel: leaving the mode takes the replay off the map
  useEffect(() => {
    if (mode !== 'hurricane' && h.shown) closeMap()
  }, [mode, h.shown])
  // Escape takes it off the map, unless a modal owns Escape or a field is being typed in
  useEffect(() => {
    if (!h.shown) return undefined
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.defaultPrevented || document.querySelector('[aria-modal="true"]')) return
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName || '') || e.target?.isContentEditable) return
      closeMap()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [h.shown])

  const res = h.result
  const plan = h.shown ? planOf(res, h.which) : null
  const quick = reducedMotion()

  const path = useMemo(() => (res ? pathOnMap(res.storm.points) : null), [res])
  const lines = useMemo(() => {
    if (!res || !plan) return []
    const held = new Set(plan.lines)
    const total = res.storm.total_km
    return res.storm.trip
      .map((id, i) => {
        const b = branchById.get(id)
        const a = b && subPos(b.from_sub)
        const z = b && subPos(b.to_sub)
        if (!a || !z) return null
        const [x1, y1] = project(a[0], a[1])
        const [x2, y2] = project(z[0], z[1])
        const delay = quick || !(total > 0) ? 0 : Math.round((res.storm.along_km[i] / total) * SWEEP_MS)
        return { id, x1, y1, x2, y2, held: held.has(id), delay, xf: b.from_sub === b.to_sub }
      })
      .filter(Boolean)
  }, [res, plan, branchById, subPos, project, quick])
  const areas = useMemo(() => {
    if (!res || !plan) return { kept: [], out: [] }
    const still = new Set(plan.out_subs)
    const at = (sid) => {
      const p = subPos(sid)
      return p ? { sid, xy: project(p[0], p[1]) } : null
    }
    return {
      kept: res.none.out_subs.filter((s) => !still.has(s)).map(at).filter(Boolean),
      out: plan.out_subs.map(at).filter(Boolean),
    }
  }, [res, plan, subPos, project])

  if (!res || !plan || !path) return null
  const paused = h.clock.pausedAt != null
  const r = res.storm.radius_km * MU_PER_KM
  return (
    <g className={`hd-map${paused ? ' hd-map--paused' : ''}${quick ? ' hd-map--still' : ''}`} aria-hidden="true" key={h.clock.key}>
      <rect className="hd-map__dim" x={-20000} y={-20000} width={60000} height={60000} />
      <path className="hd-band" d={path.d} strokeWidth={2 * r} pathLength={1} strokeDasharray="1 1" style={{ animationDuration: `${SWEEP_MS}ms` }} />
      {lines
        .filter((l) => !l.held)
        .map((l) =>
          l.xf ? (
            <circle key={l.id} className="hd-hit" cx={l.x1} cy={l.y1} r={5 / k} style={{ animationDelay: `${l.delay}ms` }} />
          ) : (
            <line key={l.id} className="hd-hit" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} style={{ animationDelay: `${l.delay}ms` }} />
          ),
        )}
      {areas.out.map(({ sid, xy }) => (
        <circle key={`o${sid}`} className="hd-area hd-area--out" cx={xy[0]} cy={xy[1]} r={2.6 / k} style={{ animationDelay: `${SWEEP_MS + 700}ms` }} />
      ))}
      {areas.kept.map(({ sid, xy }) => (
        <circle key={`k${sid}`} className="hd-area hd-area--kept" cx={xy[0]} cy={xy[1]} r={3.2 / k} style={{ animationDelay: `${SWEEP_MS + 200}ms` }} />
      ))}
      {lines
        .filter((l) => l.held)
        .map((l) =>
          l.xf ? (
            <circle key={l.id} className="hd-held" cx={l.x1} cy={l.y1} r={6 / k} style={{ '--at': `${l.delay}ms` }} />
          ) : (
            <line key={l.id} className="hd-held" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} pathLength="1" style={{ '--at': `${l.delay}ms` }} />
          ),
        )}
      <Eye path={path} k={k} clock={h.clock} quick={quick} />
    </g>
  )
}

// The storm's eye crossing the path again; it stops where it is while paused and leaves once it has passed.
function Eye({ path, k, clock, quick }) {
  const ref = useRef(null)
  useEffect(() => {
    const g = ref.current
    if (!g) return undefined
    const place = () => {
      const f = Math.min(1, replayAt(clock) / SWEEP_MS)
      const [x, y] = path.at(f)
      g.setAttribute('transform', `translate(${x} ${y})`)
      g.style.opacity = f >= 1 ? '0' : ''
      return f
    }
    if (quick) {
      g.style.opacity = '0'
      return undefined
    }
    let raf = 0
    const tick = () => {
      if (place() < 1 && clock.pausedAt == null) raf = requestAnimationFrame(tick)
    }
    tick()
    return () => cancelAnimationFrame(raf)
  }, [path, clock, quick])
  const [x0, y0] = path.xy[0]
  return (
    <g ref={ref} className="hd-eye" transform={`translate(${x0} ${y0})`}>
      <g transform={`scale(${1 / k})`}>
        <g className={`hz-glyph${clock.pausedAt == null ? ' hz-glyph--spin' : ''}`}>
          <circle className="hz-ring" r={5.2} />
          <path className="hz-arm" d="M5.2 0C5.2 -7.5 0.6 -12.5 -7 -13.5" />
          <path className="hz-arm" d="M-5.2 0C-5.2 7.5 -0.6 12.5 7 13.5" />
        </g>
      </g>
    </g>
  )
}
