import Bulletin from '../features/bulletin/Bulletin'
import MapLegend from '../features/flow/MapLegend'
import TownsFeed from '../features/impact/TownsFeed'
import { fmt } from '../geo'
import { STEP_MS, useOverload } from '../store'
import useCountUp from './useCountUp'

// The right-hand column: who is affected. The giant counter is the one loud thing on the screen.
export default function ImpactPanel() {
  const { view, cascade, step, result, playing } = useOverload()
  const n = cascade?.steps.length || 0
  const done = cascade && n > 0 && step >= n
  // people once the engine reports them, homes until then
  const usePeople = view?.people !== undefined
  const key = usePeople ? 'people' : 'homes'
  // the peak so far: a cascade can end with fewer people dark than at its worst, but the counter
  // never counts down mid-replay (the result sentence gives the final number)
  const peak = cascade && step > 0 ? Math.max(...cascade.steps.slice(0, step).map((s) => s[key] ?? 0)) : (view?.[key] ?? view?.homes ?? 0)
  const final = done ? cascade.steps[n - 1][key] ?? 0 : null
  // While playing, the counter climbs steadily from 0 to the cascade's worst across the whole
  // replay (never below what has really happened so far) and lands exactly on it at the end —
  // a continuous count, not the model's step-by-step jumps. Paused or scrubbed: the exact value.
  const worst = n ? Math.max(...cascade.steps.map((s) => s[key] ?? 0)) : 0
  const target = playing && n ? Math.max(peak, Math.round((worst * Math.min(step + 1, n)) / n)) : peak
  const homes = useCountUp(target, playing ? STEP_MS : 500, { linear: playing })
  return (
    <div className="stack panel-body impact">
      <div className="counter" aria-live="polite">
        <span className={`counter__n${homes > 0 ? ' counter__n--dark' : ''}`}>{fmt(homes)}</span>
        <span className="counter__label">
          {usePeople ? 'People without power (estimate)' : 'Homes without power (estimate, ~1.4 kW per home)'}
          {final !== null && final < peak && ` · at its worst; ${fmt(final)} at the end`}
        </span>
      </div>
      {done && (
        <p className={cascade.outcome === 'islanded' ? 'verdict verdict--bad' : 'verdict'}>
          {cascade.outcome === 'islanded'
            ? `The grid split after ${n} ${n === 1 ? 'step' : 'steps'}: ${fmt(cascade.lost_mw)} MW of existing load lost.`
            : `Settled after ${n} ${n === 1 ? 'step' : 'steps'}.`}
          {cascade.capped && ' It was still spreading when the model stopped at 30 steps.'}
          {cascade.site_dark_mw > 0.5 && ` The data centers' own ${fmt(cascade.site_dark_mw)} MW lost power too.`}
        </p>
      )}
      <TownsFeed />
      <Bulletin />
      {!result && !cascade && <MapLegend />}
    </div>
  )
}
