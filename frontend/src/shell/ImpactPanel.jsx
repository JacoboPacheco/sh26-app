import { memo, useLayoutEffect, useMemo, useRef, useState } from 'react'
import Bulletin from '../features/bulletin/Bulletin'
import MapLegend from '../features/flow/MapLegend'
import PresentDamage from '../features/briefing/PresentDamage'
import CostCard from '../features/cost/CostCard'
import OutageCost from '../features/cost/OutageCost'
import { LABEL, cascadePeople, homesOf } from '../features/cost/figures'
import { money, moneyParts, moneyRange } from '../features/cost/money'
import DarkFirst from '../features/darkfirst/DarkFirst'
import HowWeKnow from '../features/evidence/HowWeKnow'
import ActiveFix from '../features/fix/ActiveFix'
import { FlipOffer, FlipResult } from '../features/fix/Flip'
import { flipSide, plantsOut, useFixFollowsCase, useFlip } from '../features/fix/flipCase'
import TownsFeed from '../features/impact/TownsFeed'
import FullSolutionButton from '../features/unlock/FullSolutionButton'
import { useSteadyLossRate } from '../features/cost/steady'
import { byIntensity, leapIntensity } from '../features/impact/intensity'
import { hitTowns, roundPeople, useHitEvents, useReducedMotion } from '../features/impact/towns'
import { fmt } from '../geo'
import { townOf, useOverload } from '../store'
import './bomb.css'
import { leapIndexAt, titleCase } from './cascadeSchedule'
import SiteVerdict from './SiteVerdict'
import StepFeed from './StepFeed'

// The right-hand column: who is affected and what it costs. Before the cascade runs, "Where the people
// are": how hard the grid is strained, the overloaded lines and the people their power reaches. Once it
// runs, the toll: people hit and the cost of the outage, two plain figures that only LEAP, each time the
// blast front reaches a town (estimates, from the engine: backend powerflow.hits, and the case's cost:
// backend/costs.py). Incident-room look: no pops, no glow, one short hard jolt per leap.
export default function ImpactPanel() {
  const O = useOverload()
  const { view, cascade, cascading, cascadeError, step, site, result, fx, playing, mode, caseBody } = O
  const rate = useSteadyLossRate() // (after a flip, switching back keeps this case's own money on the toll)
  const n = cascade?.steps.length || 0
  const live = !!(fx && playing)
  const done = !!cascade && n > 0 && step >= n && !live
  const settled = !!cascade && step >= n && !live
  // nobody lost power in the end (the grid rerouted around every failure): the toll stays neutral
  const calm = !!cascade && (cascade.people_zone ?? cascade.people ?? 0) === 0
  // the flip: the map's case is the one run again with the fix (features/fix/flip.js)
  // (while one computes, the flip's view stays only if the flip itself started the run)
  const flip = useFlip()
  const fixed = flipSide(flip, caseBody) === 'fixed' && !plantsOut(cascade) && (!cascading || flip.status === 'running' || !cascade)
  useFixFollowsCase(O) // a fix belongs to its case: moving the campus takes the flip's upgrades off
  // the steps, live under the toll while the replay plays (or sits paused part-way): each trip as the replay
  // reaches it (shell/StepFeed); once it has played, the whole list folds into "More" with the rest
  const feed = n > 0 && (live || (step > 0 && step < n))
  const every = n > 0 && settled
  // Fix it lives on Strengthen (user, Sat 22:30): "Fix it in Strengthen" opens its incident stage for this case, right
  // after a drop that overloads (the stage computes the options on that click) and again once the cascade has played
  const over = !cascade && !cascading && !fixed && !!result && (result.overloaded?.length || 0) > 0
  // the rest (full cost breakdown, towns going dark, the map key) sits behind one closed fold, mounted only once opened
  const [more, setMore] = useState(false)
  return (
    <div className="stack panel-body impact">
      {!site && !cascade && mode === 'campus' && !fixed ? (
        <StartHere />
      ) : fixed && !live && (settled || cascading || (!cascade && !!cascadeError)) ? (
        <FlipResult />
      ) : site && !cascade ? (
        <>
          <SiteVerdict />
          {result && <WhereThePeopleAre rate={rate} />}
        </>
      ) : live ? (
        <LiveCounter key={fx.startedAt} fx={fx} calm={calm} rate={calm ? null : rate} />
      ) : (
        <Counter view={view} done={done} ran={!!cascade} calm={calm} rate={calm ? null : rate} />
      )}
      {done && !fixed && (
        <p className={cascade.outcome === 'islanded' ? 'verdict verdict--bad' : 'verdict'}>
          {cascade.outcome === 'islanded'
            ? `The grid split after ${n} ${n === 1 ? 'step' : 'steps'}: ${fmt(cascade.lost_mw)} MW of homes and businesses lost.`
            : `Settled after ${n} ${n === 1 ? 'step' : 'steps'}.`}
          {cascade.capped && ' It was still spreading when the model stopped at 30 steps.'}
          {cascade.site_dark_mw > 0.5 &&
            ` ${(cascade.sites?.length || 1) === 1 ? "The data center's" : "The data centers'"} own ${fmt(cascade.site_dark_mw)} MW lost power too (not counted as people).`}
        </p>
      )}
      {/* a fix on the case outside the flip (a flipped fix kept after the hour or the size changed, a Strengthen option
          tried on the map): what it changes, as the flip's result says it (features/fix) */}
      {!fixed && !live && <ActiveFix rate={rate} settled={settled} />}
      {/* under the toll: how long the lights are out (and, before a run, what it would cost), then the flip (the
          same case again with the best verified fix), then one click to present it */}
      {result && !fixed && <OutageCost />}
      {feed && <StepFeed />}
      {done && !calm && !fixed && <FlipOffer rate={rate} />}
      {over && <FullSolutionButton label="Fix it in Strengthen" hint={FIX_HINT} />}
      {result && <PresentDamage />}
      {done && !calm && !fixed && <FullSolutionButton label="Fix it in Strengthen" hint={FIX_HINT} />}
      {(done || (fixed && settled)) && <ToStrengthen />}
      {/* the same campus under three service rules: who is cut first (features/darkfirst); below the flip, the
          presentation and the hand-off, so the fix stays in view when the cascade ends */}
      {done && !calm && !fixed && <DarkFirst />}
      {result && !live && <HowWeKnow body={caseBody} applied={fixed} figures={done && !fixed ? ['people_hit', 'cost', 'outage_hours'] : []} cascade={cascade} />}
      <details className="more" onToggle={(e) => setMore(e.currentTarget.open)}>
        <summary>More: {every ? 'every step, ' : ''}incident briefing, cost breakdown, towns, map key</summary>
        {more && (
          <div className="stack more__body">
            {every && <StepFeed all />}
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

const FIX_HINT = 'The smallest upgrades, every verified option and other sites for this size, on Strengthen'

// Before anything is on the map: what this column will show, instead of a toll of 0.
function StartHere() {
  const { grid, region } = useOverload()
  const state = region === 'US' ? 'a state' : grid?.meta?.region_name || 'the state'
  return (
    <section className="start-here" aria-label="What happens">
      <h2 className="panel-h">What happens</h2>
      <p className="start-here__lead">Drop a data center anywhere in {state} and this column shows what it does to the grid.</p>
      <ol className="start-here__steps">
        <li>Where it connects, which lines it pushes past their limit, and the people in their path.</li>
        <li>Run the cascade: the lines trip one by one and the people hit and the cost of the outage add up.</li>
        <li>Then how to fix it, and how many data centers the grid can safely take.</li>
      </ol>
    </section>
  )
}

// After the cascade: the other half of the story, one click away — how many campuses the state's grid carries at
// once, and the cheapest upgrades for more (Strengthen the grid; CLAUDE.md -> Decisions -> PICKED BEFORE SLEEP).
function ToStrengthen() {
  const { grid, region, setMode } = useOverload()
  if (region === 'US') return null
  const state = grid?.meta?.region_name || 'this state'
  return (
    <button type="button" className="to-strengthen" onClick={() => setMode('unlock')}>
      <span className="to-strengthen__q">How many can {state} take?</span>
      <span className="to-strengthen__a">Strengthen the grid</span>
    </button>
  )
}

// ------------------------------------------------------------------ the toll
const HIT_LABEL = LABEL.en.hit
const MONEY_LABEL = LABEL.en.cost
const WHY = LABEL.en.hitWhy

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

// The toll's figures: the people-hit number and, once the case is priced, the cost beside it. Rendered the
// same way live and paused so the end of a replay changes nothing but the numbers (the live loop finds the
// figures by these class names and writes them directly).
function Toll({ hit, rate, calm, children }) {
  const m = rate ? moneyAt(hit, rate) : null
  return (
    <div className={`toll${calm ? ' toll--calm' : ''}${hit > 0 ? '' : ' toll--zero'}`}>
      <div className="toll__figs">
        <div className="toll__fig">
          <span className="toll__k">{HIT_LABEL}</span>
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

// A storm's step lands its far tail as one group ("127 more towns", backend powerflow.hits): split it back into
// its towns, the group's people shared by each town's load (how the engine counts people), so "Hardest hit"
// names a town and a town inside the tail can still be the hardest hit.
const TAIL = /^\d[\d,]* more towns?$/
function splitTail(list, subById) {
  const out = []
  for (const e of list) {
    if (!e || !TAIL.test(e.area || '')) {
      out.push(e)
      continue
    }
    const by = new Map()
    let total = 0
    for (const id of e.subs || []) {
      const s = subById.get(id)
      if (!s) continue
      const w = Math.max(Number(s.load_mw) || 0, 0)
      const name = townOf(s.name)
      const t = by.get(name) || { w: 0, subs: [] }
      t.w += w
      t.subs.push(id)
      by.set(name, t)
      total += w
    }
    if (total > 0) by.forEach((t, name) => out.push({ ...e, area: name, subs: t.subs, people: Math.round((e.people * t.w) / total) }))
  }
  return out
}

// Paused, scrubbed, or done: the exact value at the step on screen, no animation.
function Counter({ view, done, ran, calm, rate }) {
  const { step, subById, cascade } = useOverload()
  const events = useHitEvents()
  const hit = Math.max(0, view?.peopleHit || 0)
  // once it settles: the second figure, a part of the people hit (never read as a rival count)
  const stillOut = done ? cascadePeople(cascade).stillOut : 0
  // once the replay is under way: the town hit hardest so far, with its share of the cost
  const worst = useMemo(() => (ran && step > 0 ? hitTowns(splitTail(events.slice(0, step).flat(), subById), subById)[0] || null : null), [ran, step, events, subById])
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
        {stillOut > 0 && (
          <p className="toll__line">
            Of them, <b>{fmt(stillOut)}</b> {LABEL.en.stillOut} · <b>{fmt(homesOf(stillOut))}</b> homes{' '}
            <span className="muted">(estimates, 2.5 people per home)</span>
          </p>
        )}
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
    const leaps = fx.schedule.leaps
    const paint = (hit) => {
      hitRef.current = hit
      num.textContent = fmt(hit)
      wrap.classList.toggle('toll--zero', !(hit > 0))
      paintMoney(root, hit, rateRef.current)
    }
    const land = (l, animate) => {
      paint(l.hit)
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

    paint(start.hit)
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
      <Toll hit={start.hit} rate={rate} calm={calm} />
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
      .slice(0, 4)
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
            <p className="risk__text">If one of these lines trips, everyone its power reaches is hit, each counted once.</p>
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
            Lines share people, so the case is priced once{region !== 'FL' ? ', after the cascade runs' : ', below'}. Estimates on a synthetic
            grid model.
          </p>
        </>
      ) : (
        <p className="risk__calm">No line is over its limit, so nothing trips: nobody is hit by this load.</p>
      )}
    </section>
  )
}
