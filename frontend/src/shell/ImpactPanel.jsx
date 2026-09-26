import { memo, useLayoutEffect, useMemo, useRef, useState } from 'react'
import Bulletin from '../features/bulletin/Bulletin'
import MapLegend from '../features/flow/MapLegend'
import PresentDamage from '../features/briefing/PresentDamage'
import CostCard from '../features/cost/CostCard'
import OutageCost from '../features/cost/OutageCost'
import TownsFeed from '../features/impact/TownsFeed'
import { hitTowns, roundPeople, useHitEvents, useReducedMotion } from '../features/impact/towns'
import { fmt } from '../geo'
import { useOverload } from '../store'
import './bomb.css'
import { leapIndexAt, titleCase } from './cascadeSchedule'

// The right-hand column: who is affected. Before the cascade runs, "Where the people are": the
// overloaded lines and how many people their power reaches. Once it runs, the counter — the one
// loud thing on the screen: it only LEAPS, each time the blast front reaches a town, bigger and
// redder the more people it has hit (estimates, from the engine: backend powerflow.hits).
export default function ImpactPanel() {
  const { view, cascade, step, result, fx, playing } = useOverload()
  const n = cascade?.steps.length || 0
  const live = !!(fx && playing)
  const done = !!cascade && n > 0 && step >= n && !live
  // nobody lost power in the end (the grid rerouted around every failure): the counter stays pale
  const calm = !!cascade && (cascade.people_zone ?? cascade.people ?? 0) === 0
  // the rest (full cost breakdown, towns going dark, the map key) sits behind one closed fold, mounted only once opened
  const [more, setMore] = useState(false)
  return (
    <div className="stack panel-body impact">
      {result && !cascade ? (
        <WhereThePeopleAre />
      ) : live ? (
        <LiveCounter key={fx.startedAt} fx={fx} calm={calm} />
      ) : (
        <Counter view={view} done={done} ran={!!cascade} calm={calm} />
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
      {/* under the counter: how long the lights are out and what it costs (the high end), then one click to present it */}
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

// ------------------------------------------------------------------ how a number looks
// Color by the running total: pale -> amber -> red -> hot red (with a glow from a million up).
// Stops mirror the tokens in index.css (--lit, --strain, --overload) and bomb.css (--hot).
const STOPS = [
  [3, [214, 226, 241]], // under ~1,000: pale (--lit)
  [4.3, [255, 176, 58]], // ~20,000: amber (--strain)
  [5.3, [255, 61, 94]], // ~200,000: red (--overload)
  [6, [255, 26, 60]], // a million: hot red (--hot)
]
function heat(total) {
  const x = Math.log10(Math.max(total, 1))
  if (x <= STOPS[0][0]) return STOPS[0][1]
  for (let i = 1; i < STOPS.length; i++) {
    const [x1, c1] = STOPS[i]
    if (x <= x1) {
      const [x0, c0] = STOPS[i - 1]
      const f = (x - x0) / (x1 - x0)
      return c0.map((v, j) => Math.round(v + (c1[j] - v) * f))
    }
  }
  return STOPS.at(-1)[1]
}
// {--c, --mag (font size 0..1, by log10), --glow, --fit (so the widest number still fits)}
function look(total, calm = false) {
  const x = Math.log10(Math.max(total, 1))
  const [r, g, b] = calm ? STOPS[0][1] : heat(total)
  const chars = fmt(total).length
  return {
    '--c': total > 0 ? `rgb(${r}, ${g}, ${b})` : 'var(--ink)',
    '--mag': Math.min(1, Math.max(0, (x - 3) / 3.2)).toFixed(3),
    '--glow': calm ? '0' : Math.min(1, Math.max(0, (x - 5.5) / 0.5)).toFixed(3),
    '--red': calm ? '0' : '1',
    '--fit': (1 / (chars * 0.44)).toFixed(4),
  }
}

const LABEL = 'People hit (estimate)'
const WHY = 'Everyone whose power ran through a failed line or went out, each person counted once.'

// Paused, scrubbed, or done: the exact value at the step on screen, no animation.
function Counter({ view, done, ran, calm }) {
  const { step, subById } = useOverload()
  const events = useHitEvents()
  const hit = Math.max(0, view?.peopleHit || 0)
  const zone = view?.peopleZone || 0
  // the chips' row, once the replay is over: the town hit hardest so far
  const worst = useMemo(() => (ran && step > 0 ? hitTowns(events.slice(0, step).flat(), subById)[0] : null), [ran, step, events, subById])
  return (
    <div className="bomb" style={look(hit, calm)}>
      <span className="bomb__n" aria-live="polite">
        {fmt(hit)}
      </span>
      {ran && (
        <span className="bomb__chips">
          {worst && (
            <span className="bomb__worst">
              Hardest hit: <b>{worst.name}</b> · ~{fmt(roundPeople(worst.people))} people
            </span>
          )}
        </span>
      )}
      <span className="bomb__label">{LABEL}</span>
      <span className="bomb__why">{WHY}</span>
      {done && calm && hit > 0 && <p className="bomb__zone">No one lost power: the grid rerouted around every failure.</p>}
      {zone > 0 && (
        <p className="bomb__zone">
          <strong>{fmt(zone)}</strong> in the areas that lost power
          {done && view.homesZone > 0 && (
            <>
              {' '}
              · <strong>{fmt(view.homesZone)}</strong> homes <span className="muted">(2.5 people per home)</span>
            </>
          )}
        </p>
      )}
    </div>
  )
}

// While the replay plays: a rAF loop reads the schedule's clock and writes the number through refs
// (no per-frame React render). Each leap snaps to its new value with a pop, a shake, a heat-colored
// glow and a "+people · town" chip; big leaps shake the map too (App listens for overload:leap).
// Memoized on fx: React never re-renders it mid-replay, so nothing overwrites what the loop wrote.
const LiveCounter = memo(function LiveCounter({ fx, calm }) {
  const reduced = useReducedMotion()
  const boxRef = useRef(null)
  const numRef = useRef(null)
  const zoneRef = useRef(null)
  const zoneNumRef = useRef(null)
  const chipsRef = useRef(null)
  const flashRef = useRef(null)
  const start = fx.schedule.start

  useLayoutEffect(() => {
    const box = boxRef.current
    const num = numRef.current
    const leaps = fx.schedule.leaps
    const paint = (hit, zone) => {
      Object.entries(look(hit, calm)).forEach(([k, v]) => box.style.setProperty(k, v))
      num.textContent = fmt(hit)
      zoneNumRef.current.textContent = fmt(zone)
      zoneRef.current.hidden = !(zone > 0)
    }
    const land = (l, animate) => {
      paint(l.hit, l.zone)
      if (!animate || reduced) return
      if (l.delta > 0) {
        const lg = Math.log10(l.delta)
        // the pop: bigger for bigger leaps, capped so the number stays inside the panel
        const room = (box.clientWidth + 14) / Math.max(num.offsetWidth, 1)
        const s = Math.max(1.04, Math.min(1 + Math.max(0.06, (lg - 3) * 0.13), room))
        // transform only: the compositor runs it without repainting the glowing number
        num.animate(
          [
            { transform: `scale(${s})` },
            { transform: 'scale(0.97)', offset: 0.45 },
            { transform: 'scale(1.015)', offset: 0.75 },
            { transform: 'scale(1)' },
          ],
          { duration: 480 + lg * 30, easing: 'cubic-bezier(0.2, 0.9, 0.3, 1)' },
        )
        const a = calm ? 0 : Math.min(14, Math.max(1.5, (lg - 2.6) * 3.2)) // shake amplitude (px), by log10(delta); none when nobody lost power
        if (a) box.animate(
          [0, 1, -0.85, 0.65, -0.45, 0.25, -0.1, 0].map((f, i) => ({
            transform: `translate(${(f * a).toFixed(2)}px, ${((i % 2 ? -0.35 : 0.3) * f * a).toFixed(2)}px) rotate(${(f * a * 0.12).toFixed(2)}deg)`,
          })),
          { duration: 420 + lg * 20, easing: 'ease-out' },
        )
        if (!calm && l.delta >= 20000) flashRef.current.animate([{ opacity: Math.min(0.9, (lg - 3.8) * 0.4) }, { opacity: 0 }], { duration: 650, easing: 'ease-out' })
        chip(l, lg)
        if (l.big && !calm) window.dispatchEvent(new CustomEvent('overload:leap', { detail: { delta: l.delta, total: l.hit } }))
      } else if (l.zoneDelta > 0) {
        zoneNumRef.current.animate([{ transform: 'scale(1.35)' }, { transform: 'scale(1)' }], { duration: 380, easing: 'ease-out' })
      }
    }
    const chip = (l, lg) => {
      const el = document.createElement('span')
      el.className = 'bomb__chip'
      const b = document.createElement('b')
      b.textContent = `+${fmt(l.delta)}`
      el.append(b)
      if (l.label) el.append(` · ${l.label}`)
      el.style.fontSize = `${Math.min(1.2, 0.8 + Math.max(0, lg - 3.5) * 0.18).toFixed(2)}rem`
      chipsRef.current.append(el)
      const x = (Math.random() * 18).toFixed(1)
      el.animate(
        [
          { transform: `translate(${x}px, 12px) scale(0.7)`, opacity: 0 },
          { transform: `translate(${x}px, 0) scale(1.1)`, opacity: 1, offset: 0.12 },
          { transform: `translate(${x}px, -2px) scale(1)`, opacity: 1, offset: 0.62 },
          { transform: `translate(${x}px, -16px) scale(0.97)`, opacity: 0 },
        ],
        { duration: 1600, easing: 'cubic-bezier(0.2, 0.7, 0.3, 1)' },
      ).onfinish = () => el.remove()
    }

    paint(start.hit, start.zone)
    let i = leapIndexAt(fx.schedule, performance.now() - fx.startedAt)
    if (i >= 0) land(leaps[i], false)
    let raf = 0
    const tick = () => {
      const j = leapIndexAt(fx.schedule, performance.now() - fx.startedAt)
      if (j > i) {
        for (let q = i + 1; q <= j; q++) land(leaps[q], q === j) // a frame late (a hidden tab): only the last one animates
        i = j
      }
      if (i < leaps.length - 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [fx, start, reduced, calm])

  return (
    <div className="bomb bomb--live" ref={boxRef} style={look(start.hit, calm)} aria-live="off">
      <span className="bomb__flash" ref={flashRef} aria-hidden="true" />
      <span className="bomb__n" ref={numRef}>
        {fmt(start.hit)}
      </span>
      <span className="bomb__chips" ref={chipsRef} aria-hidden="true" />
      <span className="bomb__label">{LABEL}</span>
      <span className="bomb__why">{WHY}</span>
      <p className="bomb__zone" ref={zoneRef} hidden={!(start.zone > 0)}>
        <strong ref={zoneNumRef}>{fmt(start.zone)}</strong> in the areas that lost power
      </p>
    </div>
  )
})

// ------------------------------------------------------------------ before the run
const approx = (n) => {
  if (n < 1000) return fmt(n)
  const p = Math.pow(10, Math.floor(Math.log10(n)) - 2) // three significant digits, rounded up (the higher end)
  return fmt(Math.ceil(n / p) * p)
}

// The overloaded lines and transformers of the what-if, each with the people its power flows on to
// (the engine's at_risk: the counter's own rule, so the first line to fail hits exactly its number),
// and everyone in the path, each counted once. Click one to fly the map to it; pointing at one
// lights it on the map (CascadeFX listens for overload:aim-line).
function WhereThePeopleAre() {
  const { result, subName, subPos, focus, branchById } = useOverload()
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
  const aim = (id) => window.dispatchEvent(new CustomEvent('overload:aim-line', { detail: { id } }))
  // the highlight must not outlive the list
  useLayoutEffect(() => () => aim(null), [])

  const inPath = result.at_risk_people ?? 0
  return (
    <section className="risk" aria-label="Where the people are">
      <h2 className="panel-h">Where the people are</h2>
      {result.people > 0 && (
        <p className="risk__already">
          <strong>{approx(result.people)}</strong> people already without power <span className="muted">(estimate)</span>
        </p>
      )}
      {rows.length ? (
        <>
          <p className="risk__lead">
            <span className="risk__big">{fmt(inPath)}</span>
            <span>
              Up to {fmt(inPath)} people are in the path (each counted once): the power on these overloaded lines flows on to
              them. If one trips, everyone its power reaches is hit — and the next line takes the strain.
            </span>
          </p>
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
                  aria-label={`${r.name}: ${r.pct != null ? `${Math.round(r.pct)} % of its limit, ` : ''}${fmt(r.mw)} MW, carries power for about ${approx(r.people)} people. Show it on the map.`}
                >
                  <span className="risk__name">{r.name}</span>
                  <span className="risk__people">
                    <small>carries power for</small>~{approx(r.people)}
                    <small>people</small>
                  </span>
                  <span className="risk__meta">
                    {r.pct != null && (
                      <>
                        <span className="risk__pct">{Math.round(r.pct)} %</span> of its limit ·{' '}
                      </>
                    )}
                    {fmt(r.mw)} MW
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <p className="risk__note">People: everyone served where each line&apos;s power flows on to, from the state&apos;s population over its model load (estimates).</p>
        </>
      ) : (
        <p className="risk__calm">No line is over its limit, so nothing trips: nobody is hit by this load.</p>
      )}
    </section>
  )
}
