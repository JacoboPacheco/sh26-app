// Hurricane mode on the map (SVG inside the camera, map units): the storm's reach as a translucent
// band, its path dashed with an arrow for the heading, and the eye. On landfall the eye travels the
// path, the band fills in behind it, and each line it reaches flashes — CSS animations delayed by
// where along the path the backend says the storm first reaches it. Only the eye's position moves
// per frame (one attribute, no re-render), so the grid's 3,300 lines never re-render.
import { useEffect, useMemo, useRef } from 'react'
import { useMapView } from '../../GridMap'
import { useOverload } from '../../store'
import './hurricane.css'
import { STORM_MS, getHurricane, liveOverload, reducedMotion, setHurricane, useHurricane } from './hurricaneStore'
import { MU_PER_KM, pathOnMap } from './trackGeom'

export default function HurricaneLayer() {
  const { k, project } = useMapView()
  const o = useOverload()
  const { mode, trip, branchById, subPos, resetCount } = o
  const { points, draft, radiusKm, phase, hits, stormAt } = useHurricane()

  // a landfall in flight reads the case from here once the panel has unmounted (hurricaneStore)
  useEffect(() => {
    liveOverload.current = o
  })

  // the knocked-out lines were emptied elsewhere after landfall (the scenario bar's ×, say):
  // the storm is no longer in the case
  const prevTrip = useRef(trip.length)
  useEffect(() => {
    if (prevTrip.current > 0 && trip.length === 0 && getHurricane().phase === 'landed') setHurricane({ phase: 'none', hits: null })
    prevTrip.current = trip.length
  }, [trip.length])

  // "Start over" (store.resetAll): the storm goes too, and a landfall in flight stops
  const seenReset = useRef(resetCount)
  useEffect(() => {
    if (resetCount === seenReset.current) return
    seenReset.current = resetCount
    setHurricane((s) => ({ seq: s.seq + 1, points: [], draft: null, phase: 'none', hits: null, presetId: null, error: null, armed: false }))
  }, [resetCount])

  const path = useMemo(() => (points.length >= 2 ? pathOnMap(points) : null), [points])
  const draftPath = useMemo(() => (draft && draft.length >= 2 ? pathOnMap(draft) : null), [draft])
  const hitLines = useMemo(() => {
    if (!hits) return []
    const quick = reducedMotion()
    return hits.trip
      .map((id, i) => {
        const b = branchById.get(id)
        const a = b && subPos(b.from_sub)
        const c = b && subPos(b.to_sub)
        if (!a || !c) return null
        const [x1, y1] = project(a[0], a[1])
        const [x2, y2] = project(c[0], c[1])
        const delay = quick || !(hits.total_km > 0) ? 0 : (hits.along_km[i] / hits.total_km) * STORM_MS
        return { id, x1, y1, x2, y2, delay: Math.round(delay) }
      })
      .filter(Boolean)
  }, [hits, branchById, subPos, project])

  const storming = phase === 'storm'
  if (mode !== 'hurricane' && phase !== 'landed' && !storming) return null
  if (!path && !draftPath) return null
  const r = radiusKm * MU_PER_KM
  const end = path?.xy[path.xy.length - 1]

  return (
    <g className={`hz-layer${mode !== 'hurricane' ? ' hz-layer--quiet' : ''}`} aria-hidden="true">
      {path && (
        <>
          <path className="hz-band" d={path.d} strokeWidth={2 * r} />
          {storming && (
            <path
              key={stormAt}
              className="hz-band hz-band--swept"
              d={path.d}
              strokeWidth={2 * r}
              pathLength={1}
              strokeDasharray="1 1"
              style={{ animationDuration: `${STORM_MS}ms` }}
            />
          )}
          {phase === 'landed' && <path className="hz-band hz-band--done" d={path.d} strokeWidth={2 * r} />}
          <path className="hz-track" d={path.d} strokeWidth={1.6 / k} strokeDasharray={`${6 / k} ${5 / k}`} />
          <path className="hz-arrow" d="M-7 -5.5L2 0L-7 5.5" transform={`translate(${end[0]} ${end[1]}) rotate(${path.heading}) scale(${1 / k})`} />
        </>
      )}
      {storming && (
        <g key={stormAt} className="hz-hits">
          {hitLines.map((l) => (
            <line key={l.id} className="hz-hit" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} style={{ animationDelay: `${l.delay}ms` }} />
          ))}
        </g>
      )}
      {draftPath && <path className="hz-track hz-track--draft" d={draftPath.d} strokeWidth={2 / k} />}
      {path && phase !== 'landed' && <Eye path={path} r={r} k={k} moving={storming} spinning={storming || phase === 'fetching'} stormAt={stormAt} />}
    </g>
  )
}

// The storm: a spinning hurricane glyph (constant size on screen) and, while it moves, its reach.
// React renders it at the start of the path and leaves the attribute alone while that doesn't
// change, so a re-render mid-storm (a zoom) never yanks the eye back from where the frame put it.
function Eye({ path, r, k, moving, spinning, stormAt }) {
  const ref = useRef(null)

  useEffect(() => {
    const g = ref.current
    if (!g) return undefined
    const place = (f) => {
      const [x, y] = path.at(f)
      g.setAttribute('transform', `translate(${x} ${y})`)
    }
    if (!moving || reducedMotion()) {
      place(moving ? 1 : 0)
      return undefined
    }
    let raf = 0
    const tick = () => {
      const f = Math.min(1, (performance.now() - stormAt) / STORM_MS)
      place(f)
      if (f < 1) raf = requestAnimationFrame(tick)
    }
    tick()
    return () => cancelAnimationFrame(raf)
  }, [path, moving, stormAt])

  const [x0, y0] = path.xy[0]
  return (
    <g ref={ref} className="hz-eye" transform={`translate(${x0} ${y0})`}>
      {moving && <circle className="hz-wind" r={r} strokeWidth={1.2 / k} strokeDasharray={`${3 / k} ${4 / k}`} />}
      <g transform={`scale(${1 / k})`}>
        <circle className="hz-glow" r={19} />
        <g className={spinning ? 'hz-glyph hz-glyph--spin' : 'hz-glyph'}>
          <circle className="hz-ring" r={5.2} />
          <path className="hz-arm" d="M5.2 0C5.2 -7.5 0.6 -12.5 -7 -13.5" />
          <path className="hz-arm" d="M-5.2 0C-5.2 7.5 -0.6 12.5 7 13.5" />
        </g>
      </g>
    </g>
  )
}
