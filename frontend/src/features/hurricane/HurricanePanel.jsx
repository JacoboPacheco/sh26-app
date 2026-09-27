// Hurricane mode: draw a storm's path (or pick a hypothetical one), make landfall, and watch the
// eye knock out every line within reach — then the regular cascade runs from there.
// The storm itself is drawn by HurricaneLayer; both read hurricaneStore.
import { useEffect, useMemo, useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Button, ErrorBanner, Loading } from '../../ui'
import './hurricane.css'
import { getPresets, trackHits } from './hurricaneApi'
import { runCascade } from '../../api'
import HardenControl from '../harden/HardenControl'
import {
  CATEGORY_REACH_KM,
  STORM_MS,
  categoryInfo,
  getHurricane,
  landfallKmFor,
  liveOverload as live,
  reducedMotion,
  setHurricane,
  useHurricane,
} from './hurricaneStore'
import { MIN_TRACK_KM, distKm, framePoints, lengthKm, simplify, toLonLat } from './trackGeom'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const MIN_START_MS = 100 // a beat for the camera to start moving before the eye sets off — never a wait on the network

let presetsInFlight = false
function loadPresets() {
  if (presetsInFlight) return
  presetsInFlight = true
  setHurricane({ presetsError: null })
  getPresets()
    .then((p) => setHurricane({ presets: p.presets, categories: p.categories }))
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
  const { points, armed, category, presetId, phase, hits, stormAt, presets, categories, presetsError, error } = h
  // the app store's latest value, for handlers that run after an await (a landfall takes a while);
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
    setHurricane({ points: p.points, radiusKm: p.radius_km, category: p.category, presetId: p.id, armed: false, draft: null })
    focus(framePoints(p.points, p.radius_km))
  }

  function setCategory(c) {
    if (getHurricane().phase !== 'none') unland()
    setHurricane({ category: c, radiusKm: CATEGORY_REACH_KM[c] })
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

    // a picked preset with a fresh bake needs no network call at all: instant
    const preset = presets?.find((p) => p.id === s.presetId)
    const baked = preset?.hits && preset.category === s.category && Math.abs(preset.radius_km - s.radiusKm) < 1e-6 ? preset.hits : null
    let res = baked
    if (!res) {
      try {
        res = await trackHits(s.points, s.radiusKm, s.category)
      } catch (err) {
        if (!stale()) setHurricane({ phase: 'none', error: err })
        return
      }
    }
    if (stale()) return
    if (!res.count) {
      setHurricane({ phase: 'none', error: new Error("This storm misses every line. Draw its path across Florida's grid.") })
      return
    }
    await sleep(Math.max(0, MIN_START_MS - (performance.now() - t0)))
    if (stale()) return
    const quick = reducedMotion()
    const landfallKm = landfallKmFor(res.total_km, preset?.id === s.presetId ? preset.landfall_km : null)
    setHurricane({ phase: 'storm', hits: res, stormAt: performance.now(), landfallKm })
    // the cascade computes while the storm crosses, so it's ready the moment it makes landfall
    const pending = runCascade({ ...live.current.caseBody, trip: res.trip })
    pending.catch(() => {}) // startCascade reports the error if it fails
    await sleep(quick ? 400 : STORM_MS + 800) // a beat after landfall before the cascade
    if (stale()) return
    // the storm has passed: its lines join the case, then the regular cascade plays out
    live.current.setTrip(res.trip)
    setHurricane({ phase: 'landed' })
    await live.current.startCascade({ trip: res.trip }, pending)
  }

  function clearStorm() {
    setHurricane((st) => ({ seq: st.seq + 1, phase: 'none', hits: null, error: null, points: [], draft: null, presetId: null, armed: true }))
    if (live.current.trip.length) live.current.setTrip([]) // always empties the knocked-out list
  }

  const preset = presets?.find((p) => p.id === presetId)
  const info = categoryInfo(categories, category)

  return (
    <div className="stack panel-body hz">
      <p className="hz-lede">A hurricane takes out every line within its reach. Drag across the map to draw the storm&apos;s path.</p>

      <div className="row">
        <Button variant="secondary" aria-pressed={armed} onClick={() => setHurricane({ armed: !armed, draft: null })} disabled={busy}>
          {armed ? 'Drawing: drag on the map' : hasTrack ? 'Draw a new path' : 'Draw a path'}
        </Button>
      </div>

      <div className="stack hz-cat" role="group" aria-label="Storm category">
        <h3 className="panel-h">Storm category</h3>
        <div className="hz-cat__btns">
          {[1, 2, 3, 4, 5].map((c) => (
            <button key={c} type="button" className="hz-cat__btn" aria-pressed={category === c} disabled={busy} onClick={() => setCategory(c)}>
              {c}
            </button>
          ))}
        </div>
        <p className="muted hz-cat__blurb">
          {info.label}: {info.blurb}
        </p>
      </div>

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
        {preset && phase === 'none' && <p className="muted hz-desc">{preset.description}</p>}
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
      {hits && phase === 'landed' && cascade && <HardenControl hits={hits} />}
    </div>
  )
}

// One compact, plain-sentence strip: category, how far along the eye is, lines down so far. Everything
// else (people hit, cost, towns) already lives in the results column, same as any other case.
function StormResult({ hits, phase, cascade, failed }) {
  const count = useStormCount(hits, phase)
  const pct = phase === 'storm' ? Math.min(100, Math.round((count / Math.max(1, hits.count)) * 100)) : 100
  const after = cascade?.steps?.[0]?.n === 0 ? cascade.total_steps : null
  return (
    <div className="hz-result" aria-live="polite">
      <p className="hz-result__line">
        Category {hits.category} · {phase === 'storm' ? `${pct}% across the track` : 'made landfall'} ·{' '}
        <span className="hz-count">{fmt(count)}</span> {count === 1 ? 'line' : 'lines'} down
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
