// FEATURE: Strengthen the grid, the full page (owned by the unlock track). App renders it while mode === 'unlock'
// around the shared map (GridMap + UnlockLayer) in place of the demo's panels; the top bar stays.
//
// The story in one glance: WHERE the grid is weak (amber halos on the map: the lines and transformers that block
// the most sites and start the biggest blackouts, learned by simulating a campus at every town), WHAT the cheapest
// fixes are (the ranked table: the engine's plan, cheapest per site first, priced with published figures; Gemini's
// bundles beside it, only when the engine re-ran and confirmed them) and WHAT THEY UNLOCK (the headline: more sites
// that can host a campus of this size, GW of site options, each site tested alone, and the biggest blackouts gone).
// A budget buys the plan up to its amount; "Play the build-up" lays the upgrades on the map one step at a time with
// the headline counting and the table following. Everything is an estimate on a SYNTHETIC grid model.
//
// Florida's 1,000 MW study is warmed at startup, so the page opens with answers; other states run on the button
// (LAZY), with the time it takes and a real progress view.
import { useEffect, useMemo } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Button, ErrorBanner, Loading } from '../../ui'
import { money, moneyRange } from '../cost/money'
import BeforeAfter from './BeforeAfter'
import { at, budgetStops, goneText, shortMoney, siteOptions, stepsWithin, stopIndex } from './budget'
import StudyProgress from './StudyProgress'
import UnlockChart from './UnlockChart'
import UpgradeCard from './UpgradeCard'
import UpgradeTable from './UpgradeTable'
import { SIZES } from './unlockApi'
import { budgetOf, keyOf, openStudy, runStudy, scrub, select, setBudget, setSize, showBundle, startPlay, stopPlay, useUnlock } from './unlockStore'
import useTween from './useTween'
import './unlock.css'
import './strengthen.css'

const BUSY = new Set(['starting', 'queued', 'running'])

// ------------------------------------------------------------------ the page
export default function StrengthenPage() {
  const o = useOverload()
  const u = useUnlock()
  const { region, loadFactor, grid, regions, mapRef } = o
  const national = region === 'US'
  const where = regions?.find((x) => x.code === region)?.name || grid?.meta?.region_name || region
  const florida = region === 'FL'
  const mine = u.key === keyOf(region, u.size, loadFactor)
  const r = mine && u.status === 'done' ? u.result : null
  const busy = mine && BUSY.has(u.status)
  const steps = r?.steps?.length || 0
  const budget = r ? budgetOf(u) : 0
  const target = r ? stepsWithin(r, budget) : 0
  const n = r ? Math.min(u.shown, steps) : 0
  const now = r ? at(r, n) : null
  const bought = r ? at(r, target) : null

  // the study for this state, size and load level: shown at once when the backend has it; Florida starts it
  useEffect(() => {
    if (!national) openStudy({ region, loadFactor, auto: florida })
  }, [region, loadFactor, u.size, national, florida])
  // opening the page: the whole state in view
  useEffect(() => {
    mapRef.current?.reset()
  }, [mapRef])
  useEffect(() => () => stopPlay(), [])

  // Fly to what was picked. On a wide screen the card opens over the map's right side, so the camera frames the
  // upgrade left of center: an empty point east of it widens the frame (the map centers on the frame's middle).
  const flyTo = (projects) => {
    const pts = projects.flatMap((p) => [
      [p.from.lon, p.from.lat],
      [p.to.lon, p.to.lat],
    ])
    if (!pts.length) return
    let wide = false
    try {
      wide = window.matchMedia('(min-width: 861px)').matches
    } catch {
      wide = false
    }
    if (wide) {
      const lons = pts.map((q) => q[0])
      const lats = pts.map((q) => q[1])
      const span = Math.max(Math.max(...lons) - Math.min(...lons), 0.9)
      pts.push([Math.max(...lons) + span * 0.85, (Math.min(...lats) + Math.max(...lats)) / 2])
    }
    o.focus(pts)
  }
  const pickStep = (st) => {
    if (u.bundle != null) showBundle(u.bundle)
    select({ type: 'step', id: st.n })
    flyTo(st.projects)
  }
  const pickBundle = (i) => {
    const b = r.ai.bundles[i]
    select(null)
    showBundle(i)
    if (u.bundle !== i) flyTo(b.projects)
  }
  const run = () => runStudy({ region, loadFactor })
  const retry = () => openStudy({ region, loadFactor, auto: florida, force: true })

  return (
    <>
      <header className="st-head">
        <div className="st-head__row">
          <div className="st-title">
            <h1 className="st-title__h">Strengthen the grid</h1>
            <span className="st-title__where">{national ? 'Pick a state' : where}</span>
          </div>
          <div className="st-ctl">
            <div className="st-size" role="radiogroup" aria-labelledby="st-size-k">
              <span className="st-ctl__k" id="st-size-k">
                Campus size
              </span>
              <div className="st-size__opts">
                {SIZES.map((s) => (
                  <button
                    key={s}
                    type="button"
                    role="radio"
                    aria-checked={u.size === s}
                    className={`st-size__b${u.size === s ? ' is-on' : ''}`}
                    onClick={() => setSize(s)}
                    disabled={busy && u.size !== s}
                  >
                    {fmt(s)} MW
                  </button>
                ))}
              </div>
            </div>
            {r && steps > 0 && <BudgetControl r={r} budget={budget} target={target} />}
          </div>
        </div>
        <Headline o={o} u={u} r={r} n={n} now={now} target={target} budget={budget} where={where} national={national} busy={busy} mine={mine} />
      </header>

      {r && steps > 0 && (
        <div className="st-play" role="group" aria-label="Build-up">
          <Button variant={u.playing ? 'secondary' : 'primary'} onClick={u.playing ? stopPlay : () => startPlay(n >= target)} disabled={!target}>
            <span aria-hidden="true" className={`st-play__icon${u.playing ? ' is-pause' : ''}`} />
            {u.playing ? 'Pause' : n >= target ? 'Play the build-up' : n ? 'Keep building' : 'Play the build-up'}
          </Button>
          <div className="st-scrub">
            <input
              type="range"
              aria-label="Step of the build-up"
              min={0}
              max={Math.max(target, 1)}
              step={1}
              value={n}
              disabled={!target}
              onChange={(e) => scrub(Number(e.target.value))}
            />
            <span className="st-play__at">
              Step {fmt(n)} of {fmt(target)}
              {n > 0 && ` · ${shortMoney(now.cost)}`}
            </span>
          </div>
        </div>
      )}

      {!national && (r || busy) && <Legend />}

      {r && <UpgradeCard r={r} selected={u.selected} bundle={u.bundle} target={target} budget={budget} onBudget={setBudget} onPickStep={pickStep} />}

      <section className="st-rail" aria-label="The plan">
        {national ? (
          <div className="st-note">
            <h2 className="st-h">Pick a state</h2>
            <p>The weak points are found on one state&apos;s grid model. Choose a state in the menu at the top, or click one on the map.</p>
            <p>
              For a {fmt(u.size)} MW campus the engine simulates one at every town of that state, runs the full cascade wherever a line goes over its rating, tests the cheapest
              upgrades that fix it and re-runs every site they unlock. Florida&apos;s 1,000 MW study is ready now; another state takes from a few seconds to about two minutes.
            </p>
            <Button onClick={() => o.setRegion('FL')}>Open Florida</Button>
          </div>
        ) : mine && u.status === 'error' ? (
          <ErrorBanner error={u.error} onRetry={retry} />
        ) : busy ? (
          <StudyProgress u={u} where={where} />
        ) : mine && u.status === 'cta' ? (
          <RunCard where={where} size={u.size} estimate={u.estimate} loadFactor={loadFactor} onRun={run} />
        ) : r ? (
          <Result r={r} u={u} o={o} n={n} target={target} budget={budget} bought={bought} pickStep={pickStep} pickBundle={pickBundle} />
        ) : (
          <Loading label="Loading the study…" />
        )}
      </section>
    </>
  )
}

// ------------------------------------------------------------------ header pieces
function BudgetControl({ r, budget, target }) {
  const stops = useMemo(() => budgetStops(r), [r])
  const i = stopIndex(stops, budget)
  const whole = budget >= stops.at(-1) - 0.5
  return (
    <label className="st-budget">
      <span className="st-ctl__k">Budget</span>
      <span className="st-budget__v">{whole ? `${shortMoney(stops.at(-1))}, the whole plan` : budget ? shortMoney(budget) : 'none'}</span>
      <input
        type="range"
        min={0}
        max={stops.length - 1}
        step={1}
        value={i}
        onChange={(e) => setBudget(stops[Number(e.target.value)])}
        aria-valuetext={`${money(budget)}: ${target} of ${r.steps.length} upgrade packages`}
      />
    </label>
  )
}

function Headline({ o, u, r, n, now, target, budget, where, national, busy, mine }) {
  const cost = useTween(now?.cost || 0)
  const ups = useTween(now?.upgrades || 0)
  const more = useTween(now?.more || 0)
  const mw = Math.round(more) * (r?.mw || 0) // site options follow the sites as they count
  if (national)
    return (
      <p className="st-hl st-hl--plain">
        Where data centers strain a state&apos;s grid, the cheapest upgrades that take the strain away, and how many more sites could then host a campus.
      </p>
    )
  if (busy)
    return (
      <p className="st-hl st-hl--plain">
        Learning where {where}&apos;s grid is weakest for a {fmt(u.size)} MW campus…
      </p>
    )
  if (mine && u.status === 'cta')
    return (
      <p className="st-hl st-hl--plain">
        {where} hasn&apos;t been studied for a {fmt(u.size)} MW campus yet.
      </p>
    )
  if (!r) return <p className="st-hl st-hl--plain st-hl--wait">Loading the study…</p>
  const level = Math.round(r.load_factor * 100)
  if (r.already_failing)
    return (
      <p className="st-hl st-hl--plain">
        At {level} % of normal demand the model fails with no campus at all.{' '}
        <button type="button" className="st-link" onClick={() => o.setLoadFactor(1)}>
          Study normal demand
        </button>
      </p>
    )
  if (!r.steps.length) return <p className="st-hl st-hl--plain">{r.headline.sentence}</p>
  const g = goneText(now)
  const levelNote = level !== 100 ? ` At ${level} % of normal demand.` : ''
  return (
    <div className="st-hl" aria-live={u.playing ? 'off' : 'polite'}>
      {n > 0 ? (
        <p className="st-hl__line">
          <span className="st-hl__fig">{shortMoney(cost)}</span>
          <span className="st-hl__sep">·</span>
          <span className="st-hl__fig">{fmt(ups)}</span> {Math.round(ups) === 1 ? 'upgrade' : 'upgrades'}
          <span className="st-hl__arrow" aria-hidden="true">
            →
          </span>
          <span className="st-sr">then</span>
          <span className="st-hl__fig st-good">{fmt(more)}</span> more {Math.round(more) === 1 ? 'site' : 'sites'} can host {fmt(r.mw)} MW{' '}
          <span className="st-hl__muted">({siteOptions(mw)} of site options, each site tested alone)</span>
          {g && (
            <>
              <span className="st-hl__sep">·</span>
              <span className="st-hl__gone">{g}</span>
            </>
          )}
        </p>
      ) : (
        <p className="st-hl__line">
          Today <span className="st-hl__fig">{fmt(r.before.sites_ok)}</span> of {fmt(r.sites_total)} sites can host a {fmt(r.mw)} MW campus;{' '}
          <span className="st-hl__fig st-warn">{fmt(r.before.blackout_sites)}</span> would set off a blackout.
        </p>
      )}
      <p className="st-hl__sub">
        {u.playing || n < target
          ? `Building up: step ${fmt(n)} of ${fmt(target)} within the ${shortMoney(budget)} budget.`
          : target
            ? `Within a ${shortMoney(budget)} budget (high end of each estimate): ${fmt(target)} of ${fmt(r.steps.length)} upgrade packages. Cost range ${moneyRange(now.costLow, now.cost)}.`
            : `The first upgrade costs ${money(r.steps[0].cum_cost.high)}: raise the budget.`}{' '}
        Estimates on a synthetic grid model, not any utility&apos;s network.{levelNote}
      </p>
    </div>
  )
}

// ------------------------------------------------------------------ the rail
function Result({ r, u, o, n, target, budget, bought, pickStep, pickBundle }) {
  if (r.already_failing)
    return (
      <div className="st-note">
        <h2 className="st-h">Already over its limits</h2>
        <p>At this load level the model&apos;s grid fails with no campus at all, so there is nothing a campus exposes. Set the demand back to normal to find the weak points.</p>
        <Button onClick={() => o.setLoadFactor(1)}>Study normal demand</Button>
      </div>
    )
  if (!r.steps.length)
    return (
      <div className="st-note">
        <h2 className="st-h">No upgrade opens another site</h2>
        <p>
          {r.before.short_sites >= r.sites_total
            ? `The model's generators can't supply a ${fmt(r.mw)} MW campus anywhere in ${r.region_name}: it needs new generation or imports, not line upgrades. Try a smaller size.`
            : `No re-rating within five times a line's rating lets another site hold ${fmt(r.mw)} MW: the blocked sites need new lines or generation. Try a smaller size.`}
        </p>
        <HowItWorks r={r} />
      </div>
    )
  return (
    <>
      <UpgradeTable r={r} shown={n} target={target} playing={u.playing} budget={budget} selected={u.selected} bundle={u.bundle} onPickStep={pickStep} onPickBundle={pickBundle} />
      <div className="st-bottom">
        <UnlockChart result={r} shown={n} target={target} budget={budget} bundle={u.bundle} onBudget={setBudget} onBundle={pickBundle} />
        <BeforeAfter r={r} a={bought} budget={budget} />
      </div>
      <HowItWorks r={r} />
    </>
  )
}

function RunCard({ where, size, estimate, loadFactor, onRun }) {
  const secs = estimate?.seconds
  const time = secs ? (secs >= 90 ? `about ${Math.round(secs / 60)} minutes` : `about ${fmt(Math.max(10, Math.round(secs / 5) * 5))} seconds`) : 'under a minute or two'
  const level = Math.round(loadFactor * 100)
  return (
    <div className="st-run">
      <h2 className="st-h">Find {where}&apos;s weak points</h2>
      <p>
        The engine drops a {fmt(size)} MW campus at {estimate?.sites ? `each of ${fmt(estimate.sites)} towns` : 'every town'} on {where}&apos;s synthetic grid model, runs the full
        cascade wherever a line goes over its rating, tests the cheapest upgrades that fix it, and re-runs every site they unlock. Gemini proposes other bundles; the engine keeps
        only the ones it confirms.
        {level !== 100 ? ` At ${level} % of normal demand.` : ''}
      </p>
      <Button onClick={onRun}>Run the study for {where}</Button>
      <p className="st-run__time">Takes {time}. Nothing is computed until you press it.</p>
    </div>
  )
}

function HowItWorks({ r }) {
  const l = r.learned
  return (
    <details className="st-how-it">
      <summary>How the study works, its sources and limits</summary>
      <p>
        <strong>How it learned:</strong> simulated a {fmt(r.mw)} MW campus at {fmt(l.sites)} sites (one per town), ran the full cascade at {fmt(l.cascades)} of them and{' '}
        {fmt(l.verify_cascades)} more to check the plan ({fmt(l.solves)} power-flow solves), and tested {fmt(l.moves_tested)} upgrade options in {fmt(l.rounds)} rounds, in{' '}
        {Math.max(1, Math.round(l.seconds))} s{l.partial ? '; it ran out of time before checking everything, so some sites are unchecked' : ''}.
      </p>
      <p>
        {r.note} The whole plan: {moneyRange(r.headline.cost_low, r.headline.cost_high)}. {r.cost_basis}
        {r.headline.mw_unlocked_note ? ` ${r.headline.mw_unlocked_note}` : ''}
      </p>
      <ul>
        {r.sources.map((s) => (
          <li key={s.url}>
            <a href={s.url} target="_blank" rel="noreferrer">
              {s.name}
            </a>
          </li>
        ))}
      </ul>
    </details>
  )
}

function Legend() {
  return (
    <details className="st-legend" open>
      <summary>Map key</summary>
      <ul>
        <li>
          <span className="st-key st-key--wp" aria-hidden="true" />
          Weak point: fails first or blocks sites
        </li>
        <li>
          <span className="st-key st-key--up" aria-hidden="true" />
          Upgrade built so far
        </li>
        <li>
          <span className="st-key st-key--ok" aria-hidden="true" />
          Site that takes the campus today
        </li>
        <li>
          <span className="st-key st-key--blocked" aria-hidden="true" />
          Overloads a line
        </li>
        <li>
          <span className="st-key st-key--blackout" aria-hidden="true" />
          Would set off a blackout
        </li>
        <li>
          <span className="st-key st-key--unlocked" aria-hidden="true" />
          The upgrades let it connect
        </li>
      </ul>
    </details>
  )
}
