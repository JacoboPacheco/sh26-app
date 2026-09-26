// Hurricane mode: draw a storm's path (or pick a hypothetical one), make landfall, and watch the
// eye knock out every line within reach — then the regular cascade runs from there.
// The storm itself is drawn by HurricaneLayer; both read hurricaneStore.
import { useEffect, useMemo, useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Button, ErrorBanner, Field, Loading } from '../../ui'
import './hurricane.css'
import { getPresets, trackHits } from './hurricaneApi'
import { STORM_MS, getHurricane, liveOverload as live, reducedMotion, setHurricane, useHurricane } from './hurricaneStore'
import { MIN_TRACK_KM, distKm, framePoints, lengthKm, simplify, toLonLat } from './trackGeom'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const CAMERA_MS = 700 // let the camera arrive before the eye sets off

let presetsInFlight = false
function loadPresets() {
  if (presetsInFlight) return
  presetsInFlight = true
  setHurricane({ presetsError: null })
  getPresets()
    .then((p) => setHurricane({ presets: p.presets }))
    .catch((err) => setHurricane({ presetsError: err }))
    .finally(() => (presetsInFlight = false))
}

// The storm changed after landfall (new path, new size, cleared): its lines no longer match the
// case, so take them out of it. `live` is the app store's latest value (hurricaneStore).
function unland() {
  const { phase } = getHurricane()
  setHurricane((st) => ({ seq: st.seq + 1, phase: 'none', hits: null, error: null }))
  if (phase !== 'none' && live.current.trip.length) live.current.setTrip([])
}

export default function HurricanePanel() {
  const o = useOverload()
  const { trip, setMapTool, focus, cascade, cascadeError } = o
  const h = useHurricane()
  const { points, armed, radiusKm, presetId, phase, hits, stormAt, presets, presetsError, error } = h
  // the app store's latest value, for handlers that run after an await (a landfall takes ~3 s);
  // HurricaneLayer keeps it fresh too, after this panel unmounts
  useEffect(() => {
    live.current = o
  })
  const hasTrack = points.length >= 2
  const busy = phase === 'fetching' || phase === 'storm'

  // presets once per page; a first visit with no storm yet arms the map for drawing
  useEffect(() => {
    const s = getHurricane()
    if (!s.presets && !s.presetsError) loadPresets()
    if (!s.points.length && s.phase === 'none') setHurricane({ armed: true })
  }, [])

  // ---------------------------------------------------------------- drawing tool (owns the map pointer while armed)
  const tool = useMemo(
    () => ({
      cursor: 'crosshair',
      down(p) {
        setHurricane({ draft: [toLonLat(p)], error: null })
      },
      move(p, pressed) {
        const { draft } = getHurricane()
        if (!pressed || !draft) return
        const pt = toLonLat(p)
        if (distKm(draft[draft.length - 1], pt) >= 2) setHurricane({ draft: [...draft, pt] })
      },
      up(p) {
        const { draft } = getHurricane()
        if (!draft) return
        const path = [...draft, toLonLat(p)]
        if (lengthKm(path) < MIN_TRACK_KM) {
          setHurricane({ draft: null, error: new Error('Drag across the map to draw the path — a click alone is too short.') })
          return
        }
        unland()
        setHurricane({ draft: null, points: simplify(path), presetId: null, armed: false })
      },
    }),
    [],
  )
  useEffect(() => {
    if (!armed) return undefined
    setMapTool(tool)
    return () => {
      setMapTool((t) => (t === tool ? null : t))
      setHurricane({ draft: null })
    }
  }, [armed, tool, setMapTool])

  // ---------------------------------------------------------------- actions
  function pickPreset(p) {
    unland()
    setHurricane({ points: p.points, radiusKm: p.radius_km, presetId: p.id, armed: false, draft: null })
    focus(framePoints(p.points, p.radius_km))
  }

  function setWidth(widthKm) {
    if (getHurricane().phase !== 'none') unland()
    setHurricane({ radiusKm: widthKm / 2 })
  }

  async function makeLandfall() {
    const s = getHurricane()
    if (s.points.length < 2) return
    const seq = s.seq + 1
    const stale = () => getHurricane().seq !== seq
    if (live.current.trip.length) live.current.setTrip([]) // an earlier storm's lines come back first
    setHurricane({ seq, phase: 'fetching', hits: null, error: null, armed: false })
    const t0 = performance.now()
    focus(framePoints(s.points, s.radiusKm))
    let res
    try {
      res = await trackHits(s.points, s.radiusKm)
    } catch (err) {
      if (!stale()) setHurricane({ phase: 'none', error: err })
      return
    }
    if (stale()) return
    if (!res.count) {
      setHurricane({ phase: 'none', error: new Error("This storm misses every line. Draw its path across Florida's grid.") })
      return
    }
    await sleep(Math.max(0, CAMERA_MS - (performance.now() - t0)))
    if (stale()) return
    const quick = reducedMotion()
    setHurricane({ phase: 'storm', hits: res, stormAt: performance.now() })
    await sleep(quick ? 400 : STORM_MS + 800) // a beat after landfall before the cascade
    if (stale()) return
    // the storm has passed: its lines join the case, then the regular cascade plays out
    live.current.setTrip(res.trip)
    setHurricane({ phase: 'landed' })
    await live.current.startCascade({ trip: res.trip })
  }

  function clearStorm() {
    setHurricane((st) => ({ seq: st.seq + 1, phase: 'none', hits: null, error: null, points: [], draft: null, presetId: null, armed: true }))
    if (live.current.trip.length) live.current.setTrip([]) // always empties the knocked-out list
  }

  const preset = presets?.find((p) => p.id === presetId)
  const width = Math.round(radiusKm * 2)

  return (
    <div className="stack panel-body hz">
      <p className="hz-lede">A hurricane takes out every line within its reach. Drag across the map to draw the storm&apos;s path.</p>

      <div className="row">
        <Button variant="secondary" aria-pressed={armed} onClick={() => setHurricane({ armed: !armed, draft: null })} disabled={busy}>
          {armed ? 'Drawing: drag on the map' : hasTrack ? 'Draw a new path' : 'Draw a path'}
        </Button>
      </div>

      <Field
        label="Storm width (km)"
        type="range"
        min={20}
        max={240}
        step={10}
        value={width}
        disabled={busy}
        onChange={(e) => setWidth(Number(e.target.value))}
        hint={`${fmt(width)} km wide: every line within ${fmt(radiusKm)} km of the eye's path goes down`}
      />

      <div className="stack hz-presets">
        <h3 className="panel-h">Hypothetical storms</h3>
        <ErrorBanner error={presetsError} onRetry={loadPresets} />
        {!presets ? (
          !presetsError && <Loading label="Loading storm tracks…" />
        ) : (
          <ul className="chips">
            {presets.map((p) => (
              <li key={p.id}>
                <Button variant="secondary" aria-pressed={p.id === presetId} onClick={() => pickPreset(p)} disabled={busy}>
                  {p.name}
                </Button>
              </li>
            ))}
          </ul>
        )}
        {preset && <p className="muted hz-desc">{preset.description}</p>}
      </div>

      <div className="row hz-actions">
        <Button onClick={makeLandfall} disabled={!hasTrack} busy={busy}>
          {phase === 'fetching' ? 'Tracking the storm…' : phase === 'storm' ? 'Making landfall…' : phase === 'landed' ? 'Make landfall again' : 'Make landfall'}
        </Button>
        <Button variant="secondary" onClick={clearStorm} disabled={!hasTrack && !trip.length}>
          Clear storm
        </Button>
      </div>

      <ErrorBanner error={error} />
      {hits && (phase === 'storm' || phase === 'landed') && <StormResult key={stormAt} hits={hits} phase={phase} cascade={cascade} failed={!!cascadeError && !cascade} />}
    </div>
  )
}

// "A hurricane takes out this corridor" — the count climbs as the eye passes each line.
function StormResult({ hits, phase, cascade, failed }) {
  const count = useStormCount(hits, phase)
  const after = cascade?.steps?.[0]?.n === 0 ? cascade.total_steps : null
  return (
    <div className="hz-result" aria-live="polite">
      <p className="hz-result__h">A hurricane takes out this corridor</p>
      <p className="hz-result__n">
        <span className="hz-count">{fmt(count)}</span> {count === 1 ? 'line' : 'lines'} knocked out
      </p>
      {hits.capped && <p className="muted">The model stops at {fmt(hits.count)} lines, nearest the eye first.</p>}
      {phase === 'landed' && failed && (
        <p className="muted">The model couldn&apos;t re-solve the grid after this storm. Try one of the hypothetical storms or another path.</p>
      )}
      {phase === 'landed' && after !== null && (
        <p className="muted">{after > 0 ? `Power reroutes, and overloads trip ${fmt(after)} more.` : 'Power reroutes, and the rest of the grid holds.'}</p>
      )}
    </div>
  )
}

// Lines the eye has reached so far (re-renders only when the number changes). Keyed per storm.
function useStormCount(hits, phase) {
  const counting = phase === 'storm' && !reducedMotion()
  const [count, setCount] = useState(0)
  useEffect(() => {
    if (!counting) return undefined
    let raf = 0
    const tick = () => {
      const { stormAt } = getHurricane()
      const reach = Math.min(1, (performance.now() - stormAt) / STORM_MS) * hits.total_km
      let n = 0
      while (n < hits.along_km.length && hits.along_km[n] <= reach) n++
      setCount(n)
      if (n < hits.count) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [hits, counting])
  return counting ? count : hits.count
}
