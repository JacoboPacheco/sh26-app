// FEATURE: the figures under the toll (owned by the cost track): how long the lights are out and, before a run,
// what it would cost. The outage length is estimated from the incident's size and the cost is the high end of
// the range (backend/costs.py → headline). No AI, no controls: the full breakdown stays in CostCard.
//
// Once a cascade is on screen the cost of the blackout is the toll's own second figure (shell/ImpactPanel), so
// this block keeps only the time without power (an upgrades-only case, where nobody loses power, keeps its cost
// here). The estimate is fetched once for everyone (features/impact/caseCost.js): Florida (the demo state) is
// eager, every other state asks only for the arithmetic on its cascade's own numbers once it exists, and shows
// them here after the replay.
import { useOverload } from '../../store'
import { Loading } from '../../ui'
import { outageText } from './figures'
import './outage.css'
import { money, moneyParts, moneyRange } from './money'
import { useSteadyCaseCost, useSteadyLossRate } from './steady'

// a person's share, in whole dollars ("$849", "$42"; under a dollar in cents)
const perPerson = (v) => (v >= 1 ? `$${Math.round(v).toLocaleString('en-US')}` : money(v))

export default function OutageCost() {
  const { cascade, step, playing, fx } = useOverload()
  // (steady: after a flip, switching back shows this case's own estimate at once, not the other case's)
  const { det, status, lazy, hasCase, key, retry } = useSteadyCaseCost()
  const rate = useSteadyLossRate()
  const n = cascade?.steps?.length || 0
  const played = !!cascade && step >= n && !(fx && playing)

  if (!hasCase) return null
  if (lazy && !played)
    // lazy and the cascade has not finished: one quiet line before it runs, nothing during the replay
    return cascade ? null : <p className="loss loss--wait">Run the cascade to see the time without power and the cost.</p>
  if (!key) return null
  if (status === 'error')
    return (
      <p className="loss loss--wait" role="alert">
        The cost estimate didn&apos;t load.{' '}
        <button type="button" className="loss__retry" onClick={retry}>
          Try again
        </button>
      </p>
    )
  if (!det?.headline) return <Loading label="Estimating the outage and its cost…" />

  const h = det.headline
  const blackout = det.lines?.find((l) => l.key === 'blackout')
  const stale = status === 'loading'
  // the toll above already shows the blackout's cost, leaping with the replay
  const onToll = !!cascade && h.kind === 'blackout'
  return (
    <section className={`loss${stale ? ' loss--stale' : ''}`} aria-label="Estimated outage time and cost" aria-busy={stale || undefined}>
      {h.kind === 'none' ? (
        <p className="loss__none">{lazy ? 'No one loses power in this case.' : 'No one loses power and no line needs upgrading in this case.'}</p>
      ) : (
        <>
          <div className="loss__row">
            <span className="loss__k">Time without power</span>
            {/* one format everywhere: "about 22 hours" (backend/costs.py outage_label; the same rule here); "reads
                like a forecast" (HOW-IT-WORKS.md gap #10) -> say plainly it's a rule of thumb, on the long side */}
            <span className="loss__time">
              {h.outage_hours > 0 ? outageText(h.outage_hours) : h.outage_label}{' '}
              <span className="loss__unit" title="A stated rule of thumb by the incident's size, on the long side — not a forecast.">
                (rule of thumb)
              </span>
            </span>
          </div>
          {!onToll && (
            <>
              <div className="loss__row loss__row--money">
                <span className="loss__k">{h.kind === 'upgrades' ? 'Upgrades to stop the overloads' : 'Expected cost'}</span>
                <span className="loss__money">
                  {moneyParts(h.cost_high).figure}
                  {moneyParts(h.cost_high).unit && <span className="loss__unit"> {moneyParts(h.cost_high).unit}</span>}
                </span>
              </div>
              <p className="loss__note">
                High end of typical estimates ({moneyRange(h.cost_low, h.cost_high)}). Estimates on a synthetic grid model.
              </p>
            </>
          )}
          <details className="loss__how">
            <summary>How we got this</summary>
            <p>{h.outage_basis}</p>
            {blackout && <p>{blackout.assumption}</p>}
            {onToll && <p>High end of typical estimates ({moneyRange(h.cost_low, h.cost_high)}). Estimates on a synthetic grid model.</p>}
            {onToll && rate && (
              <p>
                The toll&apos;s money: the blackout&apos;s cost (lost power × hours without it × the value of lost load, high end)
                {rate.basis === 'hit' ? ' shared out by the people hit' : ' per person who loses power'}, about {perPerson(rate.high)} a person.
              </p>
            )}
          </details>
        </>
      )}
    </section>
  )
}
