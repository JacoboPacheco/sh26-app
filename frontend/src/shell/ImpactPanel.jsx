import Bulletin from '../features/bulletin/Bulletin'
import MapLegend from '../features/flow/MapLegend'
import TownsFeed from '../features/impact/TownsFeed'
import { fmt } from '../geo'
import { useOverload } from '../store'
import useCountUp from './useCountUp'

// The right-hand column: who is affected. The giant counter is the one loud thing on the screen.
export default function ImpactPanel() {
  const { view, cascade, step, result } = useOverload()
  const homes = useCountUp(view?.homes || 0)
  const n = cascade?.steps.length || 0
  const done = cascade && n > 0 && step >= n
  return (
    <div className="stack panel-body impact">
      <div className="counter" aria-live="polite">
        <span className={`counter__n${homes > 0 ? ' counter__n--dark' : ''}`}>{fmt(homes)}</span>
        <span className="counter__label">Homes without power (estimate, ~1.4 kW per home)</span>
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
