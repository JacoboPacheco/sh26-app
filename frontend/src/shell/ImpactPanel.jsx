import { memo, useLayoutEffect, useMemo, useRef, useState } from 'react'
import Bulletin from '../features/bulletin/Bulletin'
import MapLegend from '../features/flow/MapLegend'
import PresentDamage from '../features/briefing/PresentDamage'
import CostCard from '../features/cost/CostCard'
import OutageCost from '../features/cost/OutageCost'
import { money, moneyParts, moneyRange } from '../features/cost/money'
import TownsFeed from '../features/impact/TownsFeed'
import { useLossRate } from '../features/impact/caseCost'
import { byIntensity, leapIntensity } from '../features/impact/intensity'
import { hitTowns, roundPeople, useHitEvents, useReducedMotion } from '../features/impact/towns'
import { fmt } from '../geo'
import { useOverload } from '../store'
import './bomb.css'
import { leapIndexAt, titleCase } from './cascadeSchedule'

// The right-hand column: who is affected and what it costs. Before the cascade runs, "Where the people
// are": how hard the grid is strained, the overloaded lines and the people their power reaches. Once it
// runs, the toll: people hit and the cost of the outage, two plain figures that only LEAP, each time the
// blast front reaches a town (estimates, from the engine: backend powerflow.hits, and the case's cost:
// backend/costs.py). Incident-room look: no pops, no glow, one short hard jolt per leap.
export default function ImpactPanel() {
  const { view, cascade, step, result, fx, playing } = useOverload()
  const rate = useLossRate()
  const n = cascade?.steps.length || 0
  const live = !!(fx && playing)
  const done = !!cascade && n > 0 && step >= n && !live
  // nobody lost power in the end (the grid rerouted around every failure): the toll stays neutral
  const calm = !!cascade && (cascade.people_zone ?? cascade.people ?? 0) === 0
  // the rest (full cost breakdown, towns going dark, the map key) sits behind one closed fold, mounted only once opened
  const [more, setMore] = useState(false)
  return (
    <div className="stack panel-body impact">
      {result && !cascade ? (
        <WhereThePeopleAre rate={rate} />
      ) : live ? (
        <LiveCounter key={fx.startedAt} fx={fx} calm={calm} rate={calm ? null : rate} />
      ) : (
        <Counter view={view} done={done} ran={!!cascade} calm={calm} rate={calm ? null : rate} />
      )}
      {done && (
        <p className={cascade.outcome === 'islanded' ? 'verdict verdict--bad' : 'verdict'}>
          {cascade.outcome === 'islanded'
            ? `The grid split after ${n} ${n === 1 ? 'step' : 'steps'}: ${fmt(cascade.lost_mw)} MW of load lost.`
            : `Settled after ${n} ${n === 1 ? 'step' : 'steps'}.`}
          {cascade.capped && ' It was still spreading when the model stopped at 30 steps.'}
          {cascade.site_dark_mw > 0.5 &&
            ` ${(cascade.sites?.length || 1) === 1 ? "The data center's" : "The data centers'"} own ${fmt(cascade.site_dark_mw)} MW lost power too.`}
        </p>
      )}
      {/* under the toll: how long the lights are out (and, before a run, what it would cost), then one click to present it */}
      {result && <OutageCost />}
      {result && <PresentDamage />}
      <details className="more" onToggle={(e) => setMore(e.currentTarget.open)}>
        <summary>More: incident briefing, cost breakdown, towns, map key</summary>
        {more && (
          <div className="stack more__body">
            <Bulletin />
            {result && <CostCard bare />}
            <TownsFeed />
            <MapLegend />
          </div>
        )}
      </details>
    </div>
  )
}

// ------------------------------------------------------------------ the toll
const LABEL = 'People hit (estimate)'
const MONEY_LABEL = 'Cost of the outage (estimate)'
const WHY = 'Everyone whose power ran through a failed line or went out, each person counted once.'

// nobody hit yet (the first line is still failing, or the replay is scrubbed back to its start): the outage
// is not priced yet, so a dash holds the figure's place, never "$0" during a real outage
const PENDING = { figure: '—', unit: '', range: 'estimate', pending: true }

// the money for `hit` people at `rate` ($ per person, high and low): figure, unit word and its range
function moneyAt(hit, rate) {
  if (!(hit > 0)) return PENDING
  const hi = hit * rate.high
  const lo = hit * rate.low
  return { ...moneyParts(hi), range: `${moneyRange(lo, hi)} · estimate`, pending: false }
}

// a person's share, in whole dollars ("$849", "$42"; under a dollar in cents)
const perPerson = (v) => (v >= 1 ? `$${Math.round(v).toLocaleString('en-US')}` : money(v))

// One line on how the money is found (the rule of thumb and its source live in the cost panel's "How we got this").
function HowMoney({ rate }) {
  return (
    <p className="toll__how">
      How this is estimated: the blackout&apos;s cost (lost power × hours without it × the value of lost load, high end)
      {rate.basis === 'hit' ? ' shared out by the people hit' : ' per person who loses power'}, about {perPerson(rate.high)} a person.
    </p>
  )
}

// The toll's figures: the people-hit number and, once the case is priced, the cost beside it. Rendered the
// same way live and paused so the end of a replay changes nothing but the numbers (the live loop finds the
// figures by these class names and writes them directly).
function Toll({ hit, rate, calm, children }) {
  const m = rate ? moneyAt(hit, rate) : null
  return (
    <div className={`toll${calm ? ' toll--calm' : ''}${hit > 0 ? '' : ' toll--zero'}`}>
      <div className="toll__figs">
        <div className="toll__fig">
          <span className="toll__k">{LABEL}</span>
          <span className="toll__n toll__n--people">{fmt(hit)}</span>
        </div>
        <div className={`toll__fig toll__fig--money${m?.pending ? ' toll__fig--pending' : ''}`} hidden={!m}>
          <span className="toll__k">{MONEY_LABEL}</span>
          <span className="toll__n toll__n--money">
            <span className="toll__fig-n">{m?.figure ?? ''}</span> <span className="toll__unit">{m?.unit ?? ''}</span>
          </span>
          <span className="toll__range">{m?.range ?? ''}</span>
        </div>
      </div>
      <p className="toll__why">{WHY}</p>
      {children}
    </div>
  )
}

// Paused, scrubbed, or done: the exact value at the step on screen, no animation.
function Counter({ view, done, ran, calm, rate }) {
  const { step, subById } = useOverload()
  const events = useHitEvents()
  const hit = Math.max(0, view?.peopleHit || 0)
  const zone = view?.peopleZone || 0
  // once the replay is under way: the town hit hardest so far, with its share of the cost
  const worst = useMemo(() => (ran && step > 0 ? hitTowns(events.slice(0, step).flat(), subById)[0] : null), [ran, step, events, subById])
  return (
    <section className="toll-wrap" aria-label="People hit and the cost of the outage" aria-live="polite">
      <Toll hit={hit} rate={rate} calm={calm}>
        {worst && (
          <p className="toll__line">
            {/* rounded up (the higher end), never past the toll above it: 11,045 hit, not "~12,000" in one town */}
            Hardest hit: <b>{worst.name}</b>, ~{fmt(Math.max(worst.people, Math.min(roundPeople(worst.people), hit)))} people
            {rate && <>, {money(worst.people * rate.high)}</>}
          </p>
        )}
        {done && calm && hit > 0 && <p className="toll__line">No one lost power: the grid rerouted around every failure.</p>}
        {zone > 0 && (
          <p className="toll__line">
            <b>{fmt(zone)}</b> in the areas that lost power
            {done && view.homesZone > 0 && (
              <>
                {' '}
                · <b>{fmt(view.homesZone)}</b> homes <span className="muted">(2.5 people per home)</span>
              </>
            )}
          </p>
        )}
        {rate && <HowMoney rate={rate} />}
      </Toll>
    </section>
  )
}

// While the replay plays: a rAF loop reads the schedule's clock and writes both figures through refs (no
// per-frame React render). Each leap snaps the numbers to their new values with one short hard jolt (a
// translate, sized by the leap's weight in its incident); big leaps jolt the map too (App listens for
// overload:leap). Memoized: React re-renders it only when the cost estimate lands mid-replay, and then
// writes the same props, so nothing overwrites what the loop wrote.
// the money for `hit` people, written straight into a toll's DOM (the live loop's no-render path)
function paintMoney(root, hit, rate) {
  const box = root?.querySelector('.toll__fig--money')
  if (!box) return
  box.hidden = !rate
  if (!rate) return
  const m = moneyAt(hit, rate)
  box.classList.toggle('toll__fig--pending', m.pending)
  box.querySelector('.toll__fig-n').textContent = m.figure
  box.querySelector('.toll__unit').textContent = m.unit
  box.querySelector('.toll__range').textContent = m.range
}

const LiveCounter = memo(function LiveCounter({ fx, calm, rate }) {
  const reduced = useReducedMotion()
  const rootRef = useRef(null)
  const rateRef = useRef(rate)
  const hitRef = useRef(fx.schedule.start.hit)
  const start = fx.schedule.start
  const final = fx.schedule.incident?.hit ?? 0 // where this incident ends: each jolt is sized against it

  // a new estimate is written at once (it may land mid-replay); the loop reads the ref from then on
  useLayoutEffect(() => {
    rateRef.current = rate
    paintMoney(rootRef.current, hitRef.current, rate)
  }, [rate])

  useLayoutEffect(() => {
    const root = rootRef.current
    const wrap = root.querySelector('.toll')
    const figs = root.querySelector('.toll__figs')
    const num = root.querySelector('.toll__n--people')
    const zoneLine = root.querySelector('.toll__zone')
    const zoneNum = zoneLine.querySelector('b')
    const leaps = fx.schedule.leaps
    const paint = (hit, zone) => {
      hitRef.current = hit
      num.textContent = fmt(hit)
      wrap.classList.toggle('toll--zero', !(hit > 0))
      paintMoney(root, hit, rateRef.current)
      zoneNum.textContent = fmt(zone)
      zoneLine.hidden = !(zone > 0)
    }
    const land = (l, animate) => {
      paint(l.hit, l.zone)
      if (!animate || reduced || calm || !(l.delta > 0)) return
      // how hard this leap hits against its own incident (0.3..1; the biggest leap of a big incident is 1)
      const I = l.intensity ?? leapIntensity(l.delta, final)
      const a = byIntensity(I, 2, 8) // px: one hard jolt, down and a little left, then settle — no scale
      figs.animate(
        [
          { transform: `translate3d(${(-a * 0.3).toFixed(2)}px, ${a.toFixed(2)}px, 0)` },
          { transform: `translate3d(${(a * 0.12).toFixed(2)}px, ${(-a * 0.22).toFixed(2)}px, 0)`, offset: 0.45 },
          { transform: 'translate3d(0, 0, 0)' },
        ],
        { duration: 110 + Math.round(30 * I), easing: 'ease-out' },
      )
      // the map jolts for a leap of 100,000+ people, or one that is a fifth or more of its incident
      if (l.big || (l.share ?? 0) >= 0.2) window.dispatchEvent(new CustomEvent('overload:leap', { detail: { delta: l.delta, total: l.hit, intensity: I, big: !!l.big } }))
    }

    paint(start.hit, start.zone)
    let i = leapIndexAt(fx.schedule, performance.now() - fx.startedAt)
    if (i >= 0) land(leaps[i], false)
    let raf = 0
    const tick = () => {
      const j = leapIndexAt(fx.schedule, performance.now() - fx.startedAt)
      if (j > i) {
        for (let q = i + 1; q <= j; q++) land(leaps[q], q === j) // a frame late (a hidden tab): only the last one jolts
        i = j
      }
      if (i < leaps.length - 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [fx, start, final, reduced, calm])

  return (
    <section className="toll-wrap" aria-label="People hit and the cost of the outage" aria-live="off" ref={rootRef}>
      <Toll hit={start.hit} rate={rate} calm={calm}>
        <p className="toll__line toll__zone" hidden={!(start.zone > 0)}>
          <b>{fmt(start.zone)}</b> in the areas that lost power
        </p>
        {rate && <HowMoney rate={rate} />}
      </Toll>
    </section>
  )
})

// ------------------------------------------------------------------ before the run
const approx = (n) => {
  if (n < 1000) return fmt(n)
  const p = Math.pow(10, Math.floor(Math.log10(n)) - 2) // three significant digits, rounded up (the higher end)
  return fmt(Math.ceil(n / p) * p)
}

// the most loaded line of the what-if, in % of its rating
function busiest(result) {
  let max = 0
  for (const o of result?.overloaded || []) if (o.pct > max) max = o.pct
  const all = result?.loading_pct || []
  for (let i = 0; i < all.length; i++) if (all[i] > max) max = all[i]
  return max
}

// How hard the grid is strained, then the overloaded lines and transformers of the what-if, each with the
// people its power flows on to (the engine's at_risk: the counter's own rule, so the first line to fail hits
// exactly its number) and, where the case is priced, the cost at stake; everyone in the path, each counted
// once. Click one to fly the map to it; pointing at one lights it on the map (CascadeFX listens for
// overload:aim-line).
function WhereThePeopleAre({ rate }) {
  const { result, subName, subPos, focus, branchById, region } = useOverload()
  const rows = useMemo(() => {
    const pct = new Map((result?.overloaded || []).map((o) => [o.id, o.pct]))
    return (result?.at_risk || [])
      .map((r) => {
        const b = branchById.get(r.id)
        const name = !b
          ? `Line ${r.id}`
          : b.from_sub === b.to_sub
            ? `${titleCase(subName(b.from_sub))} transformer`
            : `${titleCase(subName(b.from_sub))} → ${titleCase(subName(b.to_sub))}`
        return { id: r.id, name, pct: pct.get(r.id) ?? null, mw: r.mw, people: r.people }
      })
      .sort((a, b) => b.people - a.people)
      .slice(0, 6)
  }, [result, branchById, subName])
  const peak = useMemo(() => busiest(result), [result])
  const aim = (id) => window.dispatchEvent(new CustomEvent('overload:aim-line', { detail: { id } }))
  // the highlight must not outlive the list
  useLayoutEffect(() => () => aim(null), [])

  const inPath = result.at_risk_people ?? 0
  const over = peak > 100
  return (
    <section className="risk" aria-label="Where the people are">
      <h2 className="panel-h">Where the people are</h2>
      {peak > 0 && (
        <p className="risk__strain">
          The busiest line runs at <span className={over ? 'risk__pct risk__pct--over' : 'risk__pct'}>{Math.round(peak)} %</span> of its rating
          {over ? ': past its limit, it can trip.' : '.'}
        </p>
      )}
      {result.people > 0 && (
        <p className="risk__already">
          <strong>{approx(result.people)}</strong> people already without power <span className="muted">(estimate)</span>
          {rate && <>, {money(Math.min(result.people * rate.high, rate.total.high))}</>}
        </p>
      )}
      {rows.length ? (
        <>
          <div className="risk__lead">
            <span className="risk__big">{fmt(inPath)}</span>
            <span className="risk__k">people in the path (estimate)</span>
            <p className="risk__text">
              The power on these overloaded lines flows on to them, each counted once. If one trips, everyone its power reaches is hit, and
              the next line takes the strain.
            </p>
          </div>
          <ol className="risk__list">
            {rows.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  className="risk__row"
                  onMouseEnter={() => aim(r.id)}
                  onMouseLeave={() => aim(null)}
                  onFocus={() => aim(r.id)}
                  onBlur={() => aim(null)}
                  onClick={() => {
                    const b = branchById.get(r.id)
                    if (b) focus([subPos(b.from_sub), subPos(b.to_sub)])
                  }}
                  aria-label={`${r.name}: ${r.pct != null ? `${Math.round(r.pct)} % of its rating, ` : ''}${fmt(r.mw)} MW, carries power for about ${approx(r.people)} people. Show it on the map.`}
                >
                  <span className="risk__name">{r.name}</span>
                  <span className="risk__meta">
                    {r.pct != null && (
                      <>
                        <span className={r.pct > 100 ? 'risk__pct risk__pct--over' : 'risk__pct'}>{Math.round(r.pct)} %</span> of its rating ·{' '}
                      </>
                    )}
                    {fmt(r.mw)} MW
                  </span>
                  <span className="risk__people">~{approx(r.people)} people</span>
                </button>
              </li>
            ))}
          </ol>
          <p className="risk__note">
            People: everyone served where each line&apos;s power flows on to, from the state&apos;s population over its model load. The
            lines share people and a cascade rarely reaches all of them, so the cost is priced for the whole case
            {region !== 'FL' ? ' once the cascade runs' : ', below'}. Estimates on a synthetic grid model.
          </p>
        </>
      ) : (
        <p className="risk__calm">No line is over its limit, so nothing trips: nobody is hit by this load.</p>
      )}
    </section>
  )
}
