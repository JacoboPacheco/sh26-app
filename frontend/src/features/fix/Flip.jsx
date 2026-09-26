import { useLayoutEffect, useMemo, useRef } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Button, Loading } from '../../ui'
import MapOverlay from '../briefing/MapOverlay'
import { bodyFor } from '../briefing/stage'
import { LABEL, cascadePeople, homesOf } from '../cost/figures'
import { money } from '../cost/money'
import { useLossRate } from '../impact/caseCost'
import './flip.css'
import { fixLine, flipSide, plantsOut, runWithFix, showWith, showWithout, useBestFix, useFlip } from './flipCase'

const L = LABEL.en

const sameUps = (a, b) => {
  const ea = Object.entries(a || {})
  return ea.length === Object.keys(b || {}).length && ea.every(([id, v]) => Math.abs(Number(b[id]) - Number(v)) < 0.05)
}

// the most loaded line of a what-if, in % of its rating
function busiest(result) {
  let max = 0
  for (const o of result?.overloaded || []) if (o.pct > max) max = o.pct
  const all = result?.loading_pct || []
  for (let i = 0; i < all.length; i++) if (all[i] > max) max = all[i]
  return max
}

// After the cascade, under the toll: one click runs the same case again with the best verified fix (the one the
// presentation's bottom line names). Offered only when a verified fix exists: a storm's "no fix" gets no button.
export function FlipOffer({ rate }) {
  const O = useOverload()
  const flip = useFlip()
  // a plant outage on the map (Plants tab) is a different case: the report's fix is for every plant running
  const plantCase = plantsOut(O.cascade)
  const body = useMemo(() => (O.cascade && !plantCase ? bodyFor(O.cascade, O.caseBody) : null), [O.cascade, O.caseBody, plantCase])
  const { fix, report, loading } = useBestFix(body, !!body)
  // this case was flipped before: the kept fix, and a switch that is instant
  const known = !plantCase && flipSide(flip, O.caseBody) === 'base' ? flip.fix : null
  const f = known || fix
  if (plantCase) return null
  if (!f) return loading ? <p className="flip__wait">Checking the fixes the engine verified…</p> : null
  const busy = flip.status === 'running' && O.cascading
  return (
    <section className="flip" aria-label="Run it again with the fix">
      <Button onClick={() => (known ? showWith(O) : runWithFix(O, f, { base: body, report, rate }))} busy={busy}>
        Run it again with the fix
      </Button>
      <p className="flip__what">{fixLine(f)}</p>
    </section>
  )
}

// The same case with the fix in place, once its cascade is in: the toll at zero (or what is left), the grid's
// strain, the before/after in one line, and the way back. The upgraded lines draw in green on the map and the
// lines that were over their limit cool to green; nothing else moves.
export function FlipResult() {
  const O = useOverload()
  const flip = useFlip()
  const rate = useLossRate()
  const { cascade, cascading, result, solving } = O
  const f = flip.fix
  const b = flip.base
  const rootRef = useRef(null)
  const now = cascadePeople(cascade)
  const nobody = !!cascade && now.hit === 0 && now.stillOut === 0
  // the map: the upgrades drawn in, the lines that were over their limit cooling to green
  const lines = useMemo(() => {
    if (!f || !cascade) return []
    const ups = new Set((f.upgrades || []).map(Number))
    const out = [...ups].map((id) => ({ id, tone: 'fix' }))
    ;(b?.over || []).forEach((id) => !ups.has(Number(id)) && out.push({ id, tone: 'cool' }))
    return out
  }, [f, b, cascade])
  // one short fade for the block that changed (no bounce)
  useLayoutEffect(() => {
    const el = rootRef.current
    if (!el || !cascade || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    el.animate([{ opacity: 0.35 }, { opacity: 1 }], { duration: 260, easing: 'ease-out' })
  }, [cascade])

  if (!f) return null
  if (!cascade && !cascading && O.cascadeError)
    // the re-run failed (the timeline shows why): the way back stays one click away
    return (
      <section className="flip-res" aria-label="With the fix">
        <p className="flip-res__k">With the fix</p>
        <p className="flip-res__what">{fixLine(f)}</p>
        <p className="flip-res__strain">The run with the fix did not finish. Run it again from the timeline, or go back.</p>
        <div className="flip-res__back">
          <Button variant="secondary" onClick={() => showWithout(O)} disabled={!b}>
            Show it without the fix
          </Button>
        </div>
      </section>
    )
  if (!cascade || cascading) return <Loading label="Running the same case again with the fix…" />
  // the strain line reads the fixed case's own what-if (its echo matches the case on the map), never the last one's
  const fresh = !solving && !!result && sameUps(result.upgrades, O.upgrades) && (result.mw == null || Math.abs(result.mw - O.mw) < 0.5 || !O.site)
  const peak = fresh ? busiest(result) : 0
  const costNow = !nobody && rate ? now.hit * rate.high : 0
  const before = b ? `${fmt(b.hit)} people hit${b.cost ? `, ${money(b.cost.high)}` : ''}` : null
  const fixCost = f.cost ? `, for ${money(f.cost.high)}` : ''
  return (
    <section className="flip-res" aria-label="With the fix" aria-live="polite" ref={rootRef}>
      <p className="flip-res__k">With the fix</p>
      <p className="flip-res__what">{fixLine(f)}</p>
      <div className={`toll${nobody ? ' toll--calm toll--zero' : ''}`}>
        <div className="toll__figs">
          <div className="toll__fig">
            <span className="toll__k">{L.hit}</span>
            <span className={`toll__n toll__n--people${nobody ? ' flip-res__zero' : ''}`}>{fmt(now.hit)}</span>
          </div>
          <div className="toll__fig toll__fig--money">
            <span className="toll__k">{L.cost}</span>
            <span className={`toll__n toll__n--money${nobody ? ' flip-res__zero' : ''}`}>{money(costNow)}</span>
          </div>
        </div>
      </div>
      <p className="flip-res__head">{nobody ? 'Nobody loses power.' : `${fmt(now.stillOut)} ${L.stillOut} · ${fmt(homesOf(now.stillOut))} homes`}</p>
      {peak > 0 && (
        <p className="flip-res__strain">
          {peak <= 100 ? 'Every line stays within its limit: the busiest runs at ' : 'The busiest line still runs at '}
          <b>{Math.round(peak)} %</b> of its rating.
        </p>
      )}
      {before && (
        <p className="flip-res__vs">
          <span className="flip-res__was">Without the fix: {before} (estimates).</span>{' '}
          <span className="flip-res__is">
            With it: {nobody ? 'nobody loses power' : `${fmt(now.hit)} people hit`}
            {fixCost}.
          </span>
        </p>
      )}
      <div className="flip-res__back">
        <Button variant="secondary" onClick={() => showWithout(O)} disabled={!b}>
          Show it without the fix
        </Button>
      </div>
      <MapOverlay lines={lines} layerKey={f.key} />
    </section>
  )
}
