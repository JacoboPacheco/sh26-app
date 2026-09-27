// Hurricane mode on the map (SVG inside the camera, map units): a rotating cyclone with a calm eye and
// an eyewall, spiral rainbands turning counterclockwise (Northern Hemisphere), a widening forecast cone
// along the untravelled path, a landfall marker, and the wind field's reach shrinking after landfall.
// Each line the storm reaches flashes and holds red, delayed by where along the path the backend says
// the storm first reaches it. Only the eye's position (and the wind circle it carries) move per frame
// — one attribute each, no re-render — so the grid's 3,300 lines never re-render.
import { useEffect, useMemo, useRef } from 'react'
import { useMapView } from '../../GridMap'
import { useOverload } from '../../store'
import './hurricane.css'
import { STORM_MS, getHurricane, liveOverload, reducedMotion, setHurricane, useHurricane, weakenAt } from './hurricaneStore'
import { MU_PER_KM, conePath, pathOnMap } from './trackGeom'

export default function HurricaneLayer() {
  const { k, project } = useMapView()
  const o = useOverload()
  const { mode, trip, branchById, subPos, resetCount } = o
  const { points, draft, radiusKm, phase, hits, stormAt, landfallKm } = useHurricane()

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
  const rMap = radiusKm * MU_PER_KM
  const cone = useMemo(() => (path ? conePath(path.xy, rMap * 0.12, rMap * 0.95) : ''), [path, rMap])
  const totalKm = hits?.total_km ?? null
  const landfallFrac = totalKm && totalKm > 0 && landfallKm != null ? Math.min(1, landfallKm / totalKm) : null
  const landfallXY = path && landfallFrac != null ? path.at(landfallFrac) : null

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
  const end = path?.xy[path.xy.length - 1]

  return (
    <g className={`hz-layer${mode !== 'hurricane' ? ' hz-layer--quiet' : ''}`} aria-hidden="true">
      {path && (
        <>
          {cone && <path className="hz-cone" d={cone} />}
          <path className="hz-track hz-track--future" d={path.d} strokeWidth={1.4 / k} strokeDasharray={`${6 / k} ${5 / k}`} />
          {storming && (
            <path
              key={stormAt}
              className="hz-track hz-track--past"
              d={path.d}
              strokeWidth={2.2 / k}
              pathLength={1}
              strokeDasharray="1 1"
              style={{ animationDuration: `${STORM_MS}ms` }}
            />
          )}
          {phase === 'landed' && <path className="hz-track hz-track--past hz-track--done" d={path.d} strokeWidth={2.2 / k} pathLength={1} />}
          {landfallXY && (
            <g className="hz-landfall" transform={`translate(${landfallXY[0]} ${landfallXY[1]}) scale(${1 / k})`}>
              <line x1={0} y1={-6} x2={0} y2={6} />
              <line x1={-6} y1={0} x2={6} y2={0} />
            </g>
          )}
          <path className="hz-arrow" d="M-7 -5.5L2 0L-7 5.5" transform={`translate(${end[0]} ${end[1]}) rotate(${path.heading}) scale(${1 / k})`} />
        </>
      )}
      {(storming || phase === 'landed') && (
        <g key={stormAt} className="hz-hits">
          {hitLines.map((l) => (
            <line key={l.id} className="hz-hit" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} style={{ animationDelay: `${l.delay}ms` }} />
          ))}
        </g>
      )}
      {draftPath && <path className="hz-track hz-track--draft" d={draftPath.d} strokeWidth={2 / k} />}
      {path && phase !== 'landed' && (
        <Eye path={path} r={rMap} k={k} moving={storming} spinning={storming || phase === 'fetching'} stormAt={stormAt} landfallKm={landfallKm} totalKm={totalKm} />
      )}
    </g>
  )
}

// The storm: a spinning cyclone (constant size on screen) with a calm eye, an eyewall and four spiral
// rainbands, and, while it moves, the wind field it carries — which visibly shrinks once the storm has
// weakened past its landfall point. React renders it at the start of the path and leaves the attributes
// alone while they don't change, so a re-render mid-storm (a zoom) never yanks the eye back from where
// the frame put it.
function Eye({ path, r, k, moving, spinning, stormAt, landfallKm, totalKm }) {
  const ref = useRef(null)
  const windRef = useRef(null)

  useEffect(() => {
    const g = ref.current
    if (!g) return undefined
    const place = (f) => {
      const [x, y] = path.at(f)
      g.setAttribute('transform', `translate(${x} ${y})`)
      const wind = windRef.current
      if (wind && totalKm > 0 && landfallKm != null) {
        // the reach ring stays at the storm's full radius — the backend hits any line within it at
        // any point along the track, so a hit must never land outside the ring the judge can see;
        // only its opacity fades as the storm weakens past landfall
        const w = weakenAt(f * totalKm, landfallKm)
        wind.style.opacity = String(0.18 + 0.32 * w)
      }
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
  }, [path, moving, stormAt, landfallKm, totalKm, r])

  const [x0, y0] = path.xy[0]
  return (
    <g ref={ref} className="hz-eye" transform={`translate(${x0} ${y0})`}>
      {moving && <circle ref={windRef} className="hz-wind" r={r} strokeWidth={1.2 / k} strokeDasharray={`${3 / k} ${4 / k}`} />}
      <g transform={`scale(${1 / k})`}>
        <circle className="hz-glow" r={26} />
        <g className={spinning ? 'hz-cyclone hz-cyclone--spin' : 'hz-cyclone'}>
          {[0, 90, 180, 270].map((deg) => (
            <path key={deg} className="hz-arm" d="M1 0C6 -9 14 -11 21 -8" transform={`rotate(${deg})`} />
          ))}
        </g>
        <circle className="hz-eyewall" r={7.5} />
        <circle className="hz-eye-dot" r={2.2} />
      </g>
    </g>
  )
}
