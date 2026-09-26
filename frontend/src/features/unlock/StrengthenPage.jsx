// FEATURE: Strengthen the grid, the full page (owned by the unlock track). App renders it while mode === 'unlock'
// around the shared map (GridMap + UnlockLayer) in place of the demo's panels; the top bar stays.
//
// The page answers one question: how many more AI data centers of this size can the state's grid carry AT ONCE,
// and what would it take to carry more? (backend/capacity.py: campuses connected together, each where it fits
// with every one before it; the cheapest upgrades for each next one.)
//   THE ANSWER .... one sentence in display type that follows the controls, and a quieter line under it
//   THE METER ..... one cell per campus: today's in light ink, the budget's in green, the rest outlined, the power
//                   plants' reserve line, and why the search ends
//   THE CONTROLS .. campus size, always on or flexible, the budget (every stop adds a campus), Watch it get built
//   THE MAP ....... the campuses numbered in order, the upgrades in green, what stops the next one in amber
//   THE PLAN ...... campus by campus; a row opens its card, which hands the case to Watch it fail with and
//                   without the upgrades
// Secondary, behind a tab and folds: the site-by-site study (one more campus, each site tested alone, with
// Gemini's verified bundles), the power plants, how it works. Everything is an estimate on a SYNTHETIC grid model.
//
// Florida's 1,000 MW study is warmed at startup, so the page opens with answers and the build-up plays once by
// itself; other states run on the button (LAZY), with the time it takes and a real progress view.
import { useEffect, useMemo, useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Button, ErrorBanner, Loading } from '../../ui'
import AiBadge from '../ai/AiBadge'
import { money, moneyRange } from '../cost/money'
import { budgetOf, targetOf } from './unlockStore'
import { capStopIndex, capStops, capWithin, costAt, count, levelPhrase, ordinal, sizeLabel, stopText, stopsClause } from './capacity'
import { flyTo, pickCampus } from './capacityPick'
import CapacityCard from './CapacityCard'
import CapacityMeter from './CapacityMeter'
import CapacityPlan from './CapacityPlan'
import StudyProgress from './StudyProgress'
import UpgradeCard from './UpgradeCard'
import UpgradeTable from './UpgradeTable'
import { SIZES } from './unlockApi'
import {
  capBudgetOf,
  capNow,
  capTargetOf,
  keyOf,
  openStudy,
  playCap,
  reducedMotion,
  runStudy,
  select,
  setBudget,
  setCapBudget,
  setFlex,
  setSize,
  setView,
  settleCap,
  showBundle,
  stopCap,
  stopPlay,
  useUnlock,
} from './unlockStore'
import './unlock.css'
import './strengthen.css'

const BUSY = new Set(['starting', 'queued', 'running'])
const cap1 = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s)

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
  const m = r ? capNow(u) : null
  const view = m ? u.view : 'sites'
  const [sitesStart, setSitesStart] = useState({ view: 'plan', key: 0 })
  const [still] = useState(reducedMotion)

  // the capacity view's numbers
  const target = m ? capTargetOf(u) : 0
  const budget = m ? capBudgetOf(u) : 0
  const shown = m ? Math.min(u.capShown, m.steps.length) : 0
  const plants = r?.capacity?.plants || null
  const plantsN = plants ? (u.flex ? plants.campuses_flexible : plants.campuses_firm) : null
  const selN = u.selected?.type === 'cap' ? u.selected.id : null

  // the study for this state, size and load level: shown at once when the backend has it; Florida starts it
  useEffect(() => {
    if (!national) openStudy({ region, loadFactor, auto: florida })
  }, [region, loadFactor, u.size, national, florida])
  // opening the page, and a new answer (another size or campus type): the whole state in view
  useEffect(() => {
    mapRef.current?.reset()
  }, [mapRef, u.key, u.flex])
  useEffect(
    () => () => {
      stopPlay()
      settleCap()
    },
    [],
  )

  const pick = (n) => pickCampus(o, m, n)
  const watch = () => {
    select(null)
    mapRef.current?.reset()
    playCap(!(shown > m.today && shown < target))
  }
  const openSites = (start = 'plan') => {
    setSitesStart((s) => ({ view: start, key: s.key + 1 }))
    setView('sites')
  }
  // the site-by-site view's own picks (its table and Gemini bundles)
  const pickStep = (st) => {
    if (u.bundle != null) showBundle(u.bundle)
    select({ type: 'step', id: st.n })
    flyTo(
      o,
      st.projects.flatMap((p) => [
        [p.from.lon, p.from.lat],
        [p.to.lon, p.to.lat],
      ]),
    )
  }
  const pickBundle = (i) => {
    const b = r.ai.bundles[i]
    select(null)
    showBundle(i)
    if (u.bundle !== i)
      flyTo(
        o,
        b.projects.flatMap((p) => [
          [p.from.lon, p.from.lat],
          [p.to.lon, p.to.lat],
        ]),
      )
  }
  const run = () => runStudy({ region, loadFactor })
  const retry = () => openStudy({ region, loadFactor, auto: florida, force: true })

  const hasPlan = !!(m && m.steps.length)
  const duke = r?.capacity?.sources?.find((x) => /Duke/.test(x.name))
  const summary = hasPlan ? meterSummary(m, r.mw, target, plantsN, plants?.reserve_pct) : ''

  return (
    <>
      <header className="st-head">
        <Answer o={o} u={u} r={r} m={m} where={where} national={national} busy={busy} mine={mine} target={target} loadFactor={loadFactor} plantsN={plantsN} />
        {hasPlan && (
          <CapacityMeter
            m={m}
            plantsN={plantsN}
            reservePct={plants?.reserve_pct ?? 15}
            target={target}
            shown={shown}
            playing={u.capPlaying}
            selectedN={selN}
            onPick={pick}
            summary={summary}
          />
        )}
        {!national && (
          <div className="st-controls">
            <Segmented
              label="Campus size"
              value={u.size}
              options={SIZES.map((s) => [s, sizeLabel(s)])}
              onChange={setSize}
              disabledOther={busy}
            />
            {m && (
              <Segmented
                label="Campus type"
                value={u.flex}
                options={[
                  [false, 'Always on'],
                  [true, 'Flexible'],
                ]}
                onChange={setFlex}
              />
            )}
            {hasPlan && <CapBudget m={m} budget={budget} onChange={setCapBudget} />}
            {/* with reduced motion the plan stands complete at once: there is no build-up to watch */}
            {hasPlan && !still && (
              <Button
                variant={u.capPlaying ? 'secondary' : 'primary'}
                onClick={u.capPlaying ? stopCap : watch}
                disabled={target <= m.today}
                title={target <= m.today ? 'Raise the budget: there is nothing to build yet' : undefined}
              >
                <span aria-hidden="true" className={`st-watch__icon${u.capPlaying ? ' is-pause' : ''}`} />
                {u.capPlaying ? 'Pause' : shown > m.today && shown < target ? 'Keep building' : 'Watch it get built'}
              </Button>
            )}
          </div>
        )}
        {m && (
          <p className="st-foot">
            <span>
              Flexible: full power except on peak afternoons, half power then
              {duke && (
                <>
                  {' '}
                  (
                  <a href={duke.url} target="_blank" rel="noreferrer">
                    Duke University, 2025
                  </a>
                  )
                </>
              )}
              .
            </span>
            <span className="st-foot__syn">Estimates on a synthetic grid model, not any utility’s network.</span>
          </p>
        )}
      </header>

      {m && view === 'capacity' && <MapKey />}

      {r && view === 'capacity' && selN && <CapacityCard r={r} m={m} n={selN} flex={u.flex} target={target} onBudget={setCapBudget} />}
      {r && view === 'sites' && (
        <UpgradeCard r={r} selected={u.selected} bundle={u.bundle} target={targetOf(u)} budget={budgetOf(u)} onBudget={setBudget} onPickStep={pickStep} />
      )}

      <section className="st-rail" aria-label="The plan">
        {national ? (
          <div className="st-note">
            <h2 className="st-h">Pick a state</h2>
            <p>The engine works on one state’s grid model at a time. Choose a state in the menu at the top, or click one on the map.</p>
            <p>
              For a {sizeLabel(u.size)} data center it connects one campus after another where each fits with all the others, finds the cheapest upgrades that make room for
              the next one, and checks the finished set through the full cascade. Florida’s 1 GW study is ready now; another state takes from a few seconds to about two
              minutes.
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
          r.already_failing ? (
            <div className="st-note">
              <h2 className="st-h">Already over its limits</h2>
              <p>At this load level the model’s grid fails with no data center at all, so there is no room to measure. Set the demand back to normal.</p>
              <Button onClick={() => o.setLoadFactor(1)}>Study normal demand</Button>
            </div>
          ) : (
            <>
              <div className="st-tabs" role="tablist" aria-label="Which plan">
                <button type="button" role="tab" aria-selected={view === 'capacity'} className={view === 'capacity' ? 'is-on' : ''} onClick={() => setView('capacity')} disabled={!m}>
                  Campuses at once
                </button>
                <button type="button" role="tab" aria-selected={view === 'sites'} className={view === 'sites' ? 'is-on' : ''} onClick={() => openSites('plan')}>
                  Site by site
                </button>
              </div>
              {view === 'capacity' ? (
                <div className="st-pane" role="tabpanel" aria-label="Campuses at once">
                  <div className="cp-head">
                    <h2 className="st-h">The plan, campus by campus</h2>
                    <GeminiLine r={r} onOpen={() => openSites('ai')} />
                  </div>
                  {hasPlan ? (
                    <CapacityPlan m={m} target={target} shown={shown} playing={u.capPlaying} budget={budget} selectedN={selN} onPick={pick} plantsN={plantsN} />
                  ) : (
                    <p className="st-empty">No campus of this size fits, and no upgrade makes room for one: {stopText(m.stop)}.</p>
                  )}
                  <Folds r={r} flex={u.flex} />
                </div>
              ) : (
                <div className="st-pane st-pane--sites" role="tabpanel" aria-label="Site by site">
                  {!m && <p className="st-quiet">This study has no campuses-at-once search, so it shows one more campus, site by site.</p>}
                  <p className="st-sbs__lead">
                    <strong>Where can one more campus go?</strong> Each site is tested alone, not together with the others. {r.headline?.sentence}
                  </p>
                  {r.steps.length ? (
                    <UpgradeTable
                      key={sitesStart.key}
                      startView={sitesStart.view}
                      r={r}
                      shown={targetOf(u)}
                      target={targetOf(u)}
                      playing={false}
                      budget={budgetOf(u)}
                      selected={u.selected}
                      bundle={u.bundle}
                      onPickStep={pickStep}
                      onPickBundle={pickBundle}
                    />
                  ) : (
                    <p className="st-empty">No upgrade opens another site for a campus of this size.</p>
                  )}
                  <SiteHow r={r} />
                </div>
              )}
            </>
          )
        ) : (
          <Loading label="Loading the study…" />
        )}
      </section>
    </>
  )
}

// ------------------------------------------------------------------ the answer
function Answer({ o, u, r, m, where, national, busy, mine, target, loadFactor, plantsN }) {
  const size = sizeLabel(r?.mw || u.size)
  let main
  let sub = null
  if (national) {
    main = 'How many more AI data centers can a state’s grid carry at once?'
    sub = 'Pick a state: the engine connects one campus after another and finds the cheapest upgrades for the next one.'
  } else if (busy) {
    main = `Studying how many ${size} AI data centers ${where}’s grid can carry at once…`
    sub = 'The map fills in as the engine tests each site.'
  } else if (mine && u.status === 'cta') {
    main = `${where} hasn’t been studied for ${size} AI data centers yet.`
    sub = 'Run the study to see how many its grid can carry at once, and what it would take to carry more.'
  } else if (mine && u.status === 'error') {
    main = `The study for ${where} didn’t finish.`
  } else if (!r) {
    main = 'Loading the study…'
  } else if (r.already_failing) {
    main = `At ${Math.round(r.load_factor * 100)} % of normal demand, ${where}’s grid model fails with no data center at all.`
    sub = (
      <button type="button" className="st-link" onClick={() => o.setLoadFactor(1)}>
        Study normal demand
      </button>
    )
  } else if (!m) {
    main = r.headline?.sentence || `${where}: one more campus, site by site.`
    sub = 'Each site tested alone. Estimates on a synthetic grid model, not any utility’s network.'
  } else {
    ;({ main, sub } = sentence(m, where, size, u.flex, target, loadFactor, plantsN, r.capacity.plants?.reserve_pct ?? 15))
  }
  return (
    <div className={`st-answer${!r || !m ? ' st-answer--plain' : ''}`} aria-live="polite">
      <h1 className="st-answer__main">
        <span className="st-sr">Strengthen the grid. </span>
        {main}
      </h1>
      {sub && <p className="st-answer__sub">{sub}</p>}
    </div>
  )
}

// The answer in words. The money is transmission only (lines and transformers); when the power plants (with their
// planning reserve kept) supply fewer campuses than the wires carry, the headline says so itself, so the two
// numbers never read as rival answers.
function sentence(m, where, size, flex, target, lf, plantsN, reservePct) {
  const kind = flex ? 'flexible ' : ''
  const dc = (n) => `${kind}AI data center${n === 1 ? '' : 's'}`
  const N = m.steps.length
  const today = m.today
  const level = levelPhrase(lf)
  const peak = Math.abs(lf - 1) < 0.005
  if (!N) {
    const main = `${where}’s grid can’t carry one more ${size} ${dc(1)} ${level}.`
    const sub =
      m.stop === 'plants'
        ? 'The model’s power plants can’t supply one more at this load: that needs new generation, not wires. Try a smaller size.'
        : m.stop === 'no_fix'
          ? 'No line upgrade within five times its rating makes room for one: that needs new lines or generation. Try a smaller size.'
          : `The search ended early (${stopText(m.stop)}). Try a smaller size.`
    return { main, sub }
  }
  // how many campuses the headline names, and what the power plants say about them
  let n
  let v = 0
  if (today === 0) {
    const stops = capStops(m)
    v = target > 0 ? costAt(m, target).high : stops[1]
    n = target > 0 ? target : capWithin(m, stops[1])
  } else if (target <= today) {
    n = today
  } else {
    n = target
    v = costAt(m, target).high
  }
  const short = plantsN != null && plantsN < n
  const but = !short
    ? ''
    : plantsN === 0
      ? `, but today’s power plants can’t supply ${n === 1 ? 'it' : 'any of them'}`
      : `, but today’s power plants can supply only ${count(plantsN)}`
  const ups = `${money(v)} of transmission upgrades`
  let main
  let sub
  if (today === 0) {
    main = `${where}’s grid can’t carry even one more ${size} ${dc(1)} ${level} without upgrades; with ${ups} it carries ${count(n)}${but}.`
    sub = `${cap1(stopsClause(m, 1))}.`
  } else if (target <= today) {
    main = `${where}’s grid can carry ${count(today)} more ${size} ${dc(today)} at once today, with no upgrades${but}.`
    const next = m.steps[today]
    sub = next ? `${cap1(stopsClause(m, today + 1))}; ${money(next.cum_cost.high)} of upgrades lets it in.` : `Then ${stopText(m.stop)}.`
    if (!peak) sub = `${cap1(level)}. ${sub}`
  } else {
    main = `${where}’s grid can carry ${count(n)} more ${size} ${dc(n)} at once with ${ups}${but}.`
    sub = `Today it has room for ${count(today)}; ${stopsClause(m, today + 1)}.`
    if (!peak) sub = `${cap1(level)}. ${sub}`
  }
  if (plantsN != null) {
    const reserve = `${fmt(reservePct)} % reserve`
    sub += !short
      ? ` The power plants cover ${n === 1 ? 'it' : n === 2 ? 'both' : `all ${count(n)}`} with a ${reserve} kept.`
      : plantsN === 0
        ? ` The model’s power plants keep a ${reserve}: every campus of this size also needs new generation.`
        : ` Past the ${ordinal(plantsN)}, campuses need new generation too (the power plants keep a ${reserve}).`
  }
  return { main, sub }
}

function meterSummary(m, mw, target, plantsN, reservePct) {
  const N = m.steps.length
  const today = m.today
  const parts = [`Capacity meter for ${sizeLabel(mw)} campuses connected at once: ${fmt(today)} fit today`]
  if (target > today) parts.push(`${fmt(target - today)} more with ${money(costAt(m, target).high)} of upgrades`)
  if (N > target) parts.push(`${fmt(N - target)} more possible with ${money(m.steps.at(-1).cum_cost.high)} in all`)
  if (plantsN != null) parts.push(`the power plants cover ${fmt(plantsN)} with a ${fmt(reservePct ?? 15)} % reserve kept`)
  parts.push(`then ${stopText(m.stop)}.`)
  return parts.join('; ')
}

// ------------------------------------------------------------------ controls
function Segmented({ label, value, options, onChange, disabledOther = false }) {
  const id = `st-seg-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <div className="st-ctl" role="radiogroup" aria-labelledby={id}>
      <span className="st-ctl__k" id={id}>
        {label}
      </span>
      <div className="st-segs">
        {options.map(([v, text]) => (
          <button
            key={String(v)}
            type="button"
            role="radio"
            aria-checked={value === v}
            className={`st-segs__b${value === v ? ' is-on' : ''}`}
            onClick={() => onChange(v)}
            disabled={disabledOther && value !== v}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  )
}

function CapBudget({ m, budget, onChange }) {
  const stops = useMemo(() => capStops(m), [m])
  const i = capStopIndex(stops, budget)
  const v = stops[i]
  const n = capWithin(m, v)
  const c = costAt(m, n)
  if (stops.length < 2) return <p className="st-ctl st-ctl__k">No upgrade adds a campus</p>
  return (
    <label className="st-ctl st-bud" title={v ? `${moneyRange(c.low, c.high)}: the low to high end of the estimates` : 'No upgrades: today’s grid'}>
      <span className="st-ctl__k">Budget</span>
      <input
        type="range"
        min={0}
        max={stops.length - 1}
        step={1}
        value={i}
        onChange={(e) => onChange(stops[Number(e.target.value)])}
        aria-valuetext={`${v ? money(v) : 'No upgrades'}: ${n} ${n === 1 ? 'campus' : 'campuses'} at once`}
        style={{ '--st-bud-at': `${(i / Math.max(1, stops.length - 1)) * 100}%` }}
      />
      <span className="st-bud__v">{v ? money(v) : 'No upgrades'}</span>
    </label>
  )
}

function MapKey() {
  return (
    <ul className="st-key" aria-label="Map key">
      <li>
        <span className="st-key__m st-key__m--today" aria-hidden="true" />
        Fits today
      </li>
      <li>
        <span className="st-key__m st-key__m--bought" aria-hidden="true" />
        With the budget
      </li>
      <li>
        <span className="st-key__m st-key__m--more" aria-hidden="true" />
        Needs more money
      </li>
      <li>
        <span className="st-key__up" aria-hidden="true" />
        Upgrade
      </li>
      <li>
        <span className="st-key__block" aria-hidden="true" />
        Stops the next one
      </li>
    </ul>
  )
}

// ------------------------------------------------------------------ the rail's pieces
function GeminiLine({ r, onOpen }) {
  const ai = r.ai || {}
  const n = ai.bundles?.length || 0
  if ((ai.status === 'used' || ai.status === 'none_verified') && !ai.asked) {
    // Gemini answered, but with no bundle the engine could test (none named lines of this study)
    return (
      <p className="st-gem">
        <AiBadge by="gemini" />
        <span>Gemini was asked for other upgrade bundles for single sites; none it returned named upgrades the engine could test.</span>
      </p>
    )
  }
  if (ai.status === 'used' || ai.status === 'none_verified') {
    const verified = n === 0 ? 'none let another site connect' : n === ai.asked ? (n === 1 ? 'the engine verified it' : `the engine verified all ${count(n)}`) : `the engine verified ${count(n)}`
    return (
      <p className="st-gem">
        <AiBadge by="gemini" verified={n > 0} />
        <span>
          Gemini also proposed {count(ai.asked)} upgrade {ai.asked === 1 ? 'bundle' : 'bundles'} for single sites; {verified}.{' '}
          {n > 0 && (
            <button type="button" className="st-link" onClick={onOpen}>
              See {n === 1 ? 'it' : 'them'}
            </button>
          )}
        </span>
      </p>
    )
  }
  if (ai.status === 'skipped' || !ai.status) return null
  return (
    <p className="st-gem">
      <AiBadge by="fallback" why={ai.status === 'not_configured' ? 'Gemini not set up' : 'Gemini unavailable'} compact />
      <span>Gemini’s bundles for single sites were unavailable for this study; the engine’s plan stands alone.</span>
    </p>
  )
}

function Folds({ r, flex }) {
  const c = r.capacity
  const pl = c.plants
  const l = r.learned
  const nerc = c.sources?.find((s) => /NERC/.test(s.name))
  const plantsN = pl ? (flex ? pl.campuses_flexible : pl.campuses_firm) : 0
  const gw = (mw) => `${(mw / 1000).toLocaleString('en-US', { maximumFractionDigits: 1 })} GW`
  const sources = [...(c.sources || []), ...(r.sources || [])].filter((s, i, all) => all.findIndex((x) => x.url === s.url) === i)
  return (
    <div className="st-folds">
      {pl && (
        <details className="st-fold">
          <summary>
            Power plants: room for {count(pl.campuses_firm)} always-on or {count(pl.campuses_flexible)} flexible {sizeLabel(r.mw)}{' '}
            {pl.campuses_flexible === 1 ? 'campus' : 'campuses'}
          </summary>
          <p>{pl.sentence}</p>
          <p>
            At this load level the model’s plants can make {gw(pl.max_mw)} at most; the state uses {gw(pl.load_mw)} and exports {gw(pl.export_mw)}. A {fmt(pl.reserve_pct)} %
            planning reserve on the state’s own load is {gw(pl.reserve_mw)}.{' '}
            {plantsN > 0
              ? `Past the ${ordinal(plantsN)} ${flex ? 'flexible ' : ''}campus, more data centers need new generation (or less export), not only wires: the meter hatches them.`
              : 'Every campus of this size needs new generation (or less export) too, not only wires: the meter hatches them.'}
          </p>
          {nerc && (
            <p>
              <a href={nerc.url} target="_blank" rel="noreferrer">
                {nerc.name}
              </a>
            </p>
          )}
        </details>
      )}
      <details className="st-fold">
        <summary>How it works, sources and limits</summary>
        <p>{c.method}</p>
        <p>
          This search tried {fmt(c.try_sites)} of {fmt(c.sites)} candidate sites for each campus and took {Math.max(1, Math.round(c.seconds))} s; the whole study ran{' '}
          {fmt(l.solves)} power-flow solves and {fmt((l.cascades || 0) + (l.verify_cascades || 0))} full cascades.
        </p>
        <p>{r.cost_basis}</p>
        <p>
          Estimates on a synthetic grid model (Breakthrough Energy / Texas A&amp;M), not any utility’s network: the counts and costs are estimates, not an interconnection
          study.
        </p>
        <ul>
          {sources.map((s) => (
            <li key={s.url}>
              <a href={s.url} target="_blank" rel="noreferrer">
                {s.name}
              </a>
            </li>
          ))}
        </ul>
      </details>
    </div>
  )
}

function SiteHow({ r }) {
  const l = r.learned
  return (
    <details className="st-fold">
      <summary>How the site-by-site study works</summary>
      <p>
        Simulated a {fmt(r.mw)} MW campus at {fmt(l.sites)} sites (one per town), ran the full cascade at {fmt(l.cascades)} of them and {fmt(l.verify_cascades)} more to
        check the plan ({fmt(l.solves)} power-flow solves), and tested {fmt(l.moves_tested)} upgrade options in {fmt(l.rounds)} rounds, in {Math.max(1, Math.round(l.seconds))}{' '}
        s{l.partial ? '; it ran out of time before checking everything, so some sites are unchecked' : ''}.
      </p>
      <p>
        {r.note} The whole plan: {moneyRange(r.headline.cost_low, r.headline.cost_high)}. {r.headline.mw_unlocked_note || ''}
      </p>
    </details>
  )
}

function RunCard({ where, size, estimate, loadFactor, onRun }) {
  const secs = estimate?.seconds
  const time = secs ? (secs >= 90 ? `about ${Math.round(secs / 60)} minutes` : `about ${fmt(Math.max(10, Math.round(secs / 5) * 5))} seconds`) : 'under a minute or two'
  const level = Math.round(loadFactor * 100)
  return (
    <div className="st-run">
      <h2 className="st-h">How many can {where} carry?</h2>
      <p>
        The engine tests a {sizeLabel(size)} campus at {estimate?.sites ? `each of ${fmt(estimate.sites)} towns` : 'every town'} on {where}’s synthetic grid model, then
        connects campuses one after another where each fits with all the others, and finds the cheapest upgrades that make room for the next one. The finished set runs
        through the full cascade. Gemini proposes other upgrade bundles; the engine keeps only the ones it confirms.
        {level !== 100 ? ` At ${level} % of normal demand.` : ''}
      </p>
      <Button onClick={onRun}>Run the study for {where}</Button>
      <p className="st-run__time">Takes {time}. Nothing is computed until you press it.</p>
    </div>
  )
}
