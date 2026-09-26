// FEATURE: the two numbers under the hit counter (owned by the cost track): how long the lights are out
// and what it costs. The outage length is estimated from the incident's size and the cost is the high end
// of the range (backend/costs.py → headline). One request, no AI, no controls: the full breakdown stays in
// CostCard. Reads the case through useOverload(); needs no props.
import { useEffect, useRef, useState } from 'react'
import { useOverload } from '../../store'
import { Loading } from '../../ui'
import './outage.css'
import { getCost } from './costApi'
import { money, moneyRange } from './money'

export default function OutageCost() {
  const { caseBody, region, grid, site, extraSites, trip, loadFactor } = useOverload()
  const [det, setDet] = useState(null)
  const [status, setStatus] = useState('idle') // idle | loading | done | error
  const [retry, setRetry] = useState(0)
  const req = useRef(0)

  const ready = region !== 'US' && (grid?.meta?.region || 'FL') === region
  const hasCase = ready && !!(site || extraSites.length || trip.length || loadFactor !== 1.0)
  const key = hasCase ? JSON.stringify(caseBody) : ''

  useEffect(() => {
    const id = ++req.current
    if (!key) {
      const t = setTimeout(() => {
        setDet(null)
        setStatus('idle')
      }, 0)
      return () => clearTimeout(t)
    }
    const t = setTimeout(() => {
      setStatus('loading')
      getCost(JSON.parse(key))
        .then((d) => {
          if (id !== req.current) return
          setDet(d)
          setStatus('done')
        })
        .catch(() => id === req.current && setStatus('error'))
    }, 250)
    return () => clearTimeout(t)
  }, [key, retry])

  if (!key) return null
  if (status === 'error')
    return (
      <p className="loss loss--wait" role="alert">
        The cost estimate didn&apos;t load.{' '}
        <button type="button" className="loss__retry" onClick={() => setRetry((n) => n + 1)}>
          Try again
        </button>
      </p>
    )
  if (!det?.headline) return <Loading label="Estimating the outage and its cost…" />

  const h = det.headline
  const blackout = det.lines?.find((l) => l.key === 'blackout')
  const stale = status === 'loading'
  return (
    <section className={`loss${stale ? ' loss--stale' : ''}`} aria-label="Estimated outage time and cost" aria-busy={stale || undefined}>
      {h.kind === 'none' ? (
        <p className="loss__none">No one loses power and no line needs upgrading in this case.</p>
      ) : (
        <>
          <div className="loss__row">
            <span className="loss__k">Time without power</span>
            <span className="loss__time">{h.outage_label}</span>
          </div>
          <div className="loss__row loss__row--money">
            <span className="loss__k">{h.kind === 'upgrades' ? 'Upgrades to stop the overloads' : 'Expected cost'}</span>
            <span className="loss__money">{money(h.cost_high)}</span>
          </div>
          <p className="loss__note">
            High end of typical estimates ({moneyRange(h.cost_low, h.cost_high)}). Estimates on a synthetic grid model.
          </p>
          <details className="loss__how">
            <summary>How we got this</summary>
            <p>{h.outage_basis}</p>
            {blackout && <p>{blackout.assumption}</p>}
          </details>
        </>
      )}
    </section>
  )
}
