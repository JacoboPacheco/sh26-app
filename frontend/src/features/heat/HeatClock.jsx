import { useEffect, useId, useMemo } from 'react'
import { getHeadroom } from '../../api'
import { useOverload } from '../../store'
import './heat.css'
import { HEAT_HINT, HEAT_WAVE, PRESETS, ambienceFor, peakPhrase, presetFor } from './presets'
import useTween from './useTween'

const METER_MAX = 1.1 // the meter's full width, a little past the heat wave; its tick is the summer peak
const WARM_DELAY_MS = 2500
// levels to pre-build on the backend (1.0 is cached at startup), most-clicked first
const WARM_ORDER = PRESETS.map((p) => p.factor)
  .filter((f) => f !== 1)
  .sort((a, b) => (b === HEAT_WAVE) - (a === HEAT_WAVE))

// FEATURE: heat-wave clock. Florida's heat is the story: the same data center that is fine at 4 PM
// can black out a region in a heat wave. Rendered in the top bar (App.jsx). Picks a load level
// with useOverload().setLoadFactor — the store re-solves the case and refetches the heatmap at it —
// and sets html[data-heat] so heat.css can light the map for the hour (CSS only, no map re-render).
export default function HeatClock() {
  const { grid, result, loadFactor, setLoadFactor } = useOverload()
  const id = useId()
  const active = presetFor(loadFactor)
  const mood = ambienceFor(loadFactor)

  // Florida's existing load at this level. The what-if reports it for the level it solved; until
  // that lands (or with no case at all) scale the drawable grid's snapshot load.
  const baseMw = useMemo(() => (grid ? grid.subs.reduce((sum, s) => sum + (s.load_mw || 0), 0) : null), [grid])
  const solvedHere = result?.total_load_mw != null && Number(result.load_factor).toFixed(2) === loadFactor.toFixed(2)
  const totalMw = solvedHere ? result.total_load_mw : baseMw != null ? baseMw * loadFactor : NaN
  const gw = useTween(totalMw / 1000)

  useEffect(() => {
    document.documentElement.dataset.heat = mood
  }, [mood])
  useEffect(
    () => () => {
      delete document.documentElement.dataset.heat
    },
    [],
  )

  // Warm the backend's per-level cache (≈0.6 s to build a level the first time) once the grid is
  // up, so the first click on an hour answers at once. Fire and forget; the real requests report errors.
  useEffect(() => {
    if (!grid) return undefined
    let cancelled = false
    const t = setTimeout(async () => {
      // the heat wave first: it's the demo's click, and the backend builds one level at a time
      for (const f of WARM_ORDER) {
        if (cancelled) return
        await getHeadroom(f).catch(() => null)
      }
    }, WARM_DELAY_MS)
    return () => {
      cancelled = true
      clearTimeout(t)
    }
  }, [grid])

  const peakAt = (1 / METER_MAX) * 100
  const fillTo = (Math.min(loadFactor, 1) / METER_MAX) * 100
  const overTo = (Math.min(Math.max(loadFactor, 1), METER_MAX) / METER_MAX) * 100

  return (
    <div className={`heat heat--${mood}`} title={HEAT_HINT}>
      <span className="heat__label" id={`${id}-label`}>
        Time of day
      </span>
      <div className="heat__seg" role="radiogroup" aria-labelledby={`${id}-label`} aria-describedby={`${id}-hint`}>
        {PRESETS.map((p) => (
          <label key={p.id} className={`heat__opt heat__opt--${p.id}`} title={p.hint}>
            <input
              type="radio"
              name={`${id}-hour`}
              value={p.id}
              checked={active?.id === p.id}
              onChange={() => setLoadFactor(p.factor)}
            />
            <span>{p.label}</span>
          </label>
        ))}
      </div>
      <div className="heat__meter" aria-hidden="true">
        <span className="heat__fill" style={{ width: `${fillTo}%` }} />
        <span className="heat__over" style={{ left: `${peakAt}%`, width: `${overTo - peakAt}%` }} />
        <span className="heat__tick" style={{ left: `${peakAt}%` }} />
      </div>
      {/* the visible number eases; screen readers get the settled value once */}
      <p className="heat__load" aria-hidden="true">
        Florida&apos;s load: <strong>{Number.isFinite(gw) ? `${gw.toFixed(1)} GW` : '…'}</strong>
        <span className="heat__peak"> · {peakPhrase(loadFactor)}</span>
      </p>
      <span className="heat__sr" aria-live="polite">
        {Number.isFinite(totalMw) ? `Florida's load: ${(totalMw / 1000).toFixed(1)} GW, ${peakPhrase(loadFactor)}` : ''}
      </span>
      <span className="heat__sr" id={`${id}-hint`}>
        {HEAT_HINT}
      </span>
    </div>
  )
}
