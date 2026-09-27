// THE INCIDENT SOLUTION STAGE (CLAUDE.md -> Decisions -> SOLUTIONS, SIMPLE -> IN DEPTH): Strengthen opened on one
// incident, the in-depth answer to "if you want to build this here, what does it take?". StrengthenPage renders it in
// place of its statewide view while an incident is open (incident.js); UnlockLayer draws its map (IncidentLayer.jsx).
//   THE HEAD ..... Back to the incident, the incident's name and what it does without a fix (people hit, time without
//                  power, cost of the outage: the results panel's own figures and words)
//   THE WEAK POINT the line or transformer that fails first: its loading today and with the campus, why it is the
//                  bottleneck, and how often the full campus overloads the grid (the engine's check at each hour)
//   THE OPTIONS .. every verified option, distinct kinds first; one opens at a time and draws on the map: what gets
//                  built or changed element by element (from -> to MVA, the kind of work, length, its own price), the
//                  cost range with its source, the typical time to build (leadtimes.py), what it prevents, how the
//                  engine checked it, and who proposed it (the engine, or Gemini and then the engine)
//   THE STATE .... one link down to the statewide answer, the page's normal view
// Everything is the report the briefing already computed for this exact case: nothing is re-run to open it.
// Estimates on a synthetic grid model, never a real utility's network.
import { useEffect, useMemo, useRef } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Button, ErrorBanner, Loading } from '../../ui'
import AiBadge from '../ai/AiBadge'
import { cleanBody } from '../briefing/briefingApi'
import { applyBase, bodyFor } from '../briefing/stage'
import { LABEL } from '../cost/figures'
import { money, moneyParts, moneyRange } from '../cost/money'
import { applyCase, describeFix, fixWords, runWithFix, useBestFix } from '../fix/flipCase'
import { closeIncident, incidentHash, markOnScreen, markShown, retryIncident, selectOption, setAim, takeReport, useLeadItems } from './incident'
import {
  WORK,
  areasWords,
  campusOf,
  checkedWords,
  elementsOf,
  firstOf,
  leadOf,
  mainOf,
  mwText,
  noThe,
  oftenOf,
  optionsOf,
  pct,
  people,
  pickSel,
  preventsOf,
  resultWords,
  scopeOf,
  scopeText,
  stageTitle,
  stepsWords,
  titleName,
  yearsText,
} from './incidentModel'
import { reducedMotion, settleCap } from './unlockStore'
import './incident.css'
import HowWeKnow from '../evidence/HowWeKnow'

const STAGE_HASH = /^#\/strengthen\?incident/
const dropHash = () => STAGE_HASH.test(window.location.hash) && window.history.replaceState(null, '', '#/')

export default function IncidentSolution({ inc }) {
  const o = useOverload()
  const lead = useLeadItems()
  const { region, regions, grid, mapRef } = o
  const r = inc.status === 'done' ? inc.report : null
  const gridRegion = grid ? grid.meta?.region || 'FL' : null
  const here = region === inc.region && gridRegion === inc.region
  const stateName = regions?.find((x) => x.code === inc.region)?.name || r?.region_name || inc.region

  // a deep link to another state: open that state first (Strengthen stays on: useStayOnStrengthen)
  const asked = useRef(null)
  useEffect(() => {
    if (region !== inc.region && asked.current !== inc.key) {
      asked.current = inc.key
      o.setRegion(inc.region)
    }
  }, [region, inc.region, inc.key, o])
  // on screen (this renders only on Strengthen): leaving Strengthen closes it from now on, loaded or not
  useEffect(() => {
    markOnScreen()
  }, [inc.want, inc.entered])
  // on its own state with its report: from now on, picking another state closes it too
  useEffect(() => {
    if (here && r) markShown()
  }, [here, r])
  useEffect(() => {
    if (inc.shown && region !== inc.region) {
      closeIncident()
      dropHash()
    }
  }, [inc.shown, region, inc.region])
  // the address says what is on screen, so a reload reopens it; it goes back to #/ when the stage closes
  useEffect(() => {
    if (!here) return undefined
    window.history.replaceState(null, '', incidentHash(inc.body))
    return dropHash
  }, [here, inc.key, inc.body])
  // the statewide build-up never plays behind the stage
  useEffect(() => settleCap(), [])
  // Gemini's plans still coming: features/fix's shared poller (one per case, every 5 s, shared with Watch it fail's
  // flip button: the venue's one IP has 30 briefing requests a minute) brings the newer report in
  const running = inc.status === 'done' && inc.report?.agentic?.status === 'running'
  const live = useBestFix(inc.body, running)
  useEffect(() => {
    if (live.report) takeReport(inc.key, live.report)
  }, [live.report, inc.key])
  // the top bar's "Strengthen the grid" tab, clicked while the stage is open: the page's own view, the statewide answer
  useEffect(() => {
    const onTab = (e) => {
      if (!e.target?.closest?.('.appbar a[href="#/strengthen"]')) return
      closeIncident()
      dropHash()
      mapRef.current?.reset()
    }
    document.addEventListener('click', onTab, true)
    return () => document.removeEventListener('click', onTab, true)
  }, [mapRef])

  const options = useMemo(() => optionsOf(r), [r])
  const sel = pickSel(options, inc.sel)
  const { total } = campusOf(r)

  // the camera: the campus, the weak point and the option on screen
  const optKey = sel ? `${inc.key}|${sel.i}` : inc.key
  useEffect(() => {
    if (!here || !r) return
    const pts = framePoints(r, sel, o)
    if (pts.length) mapRef.current?.focus(pts)
    // (only when the option or the incident changes, not on every store update)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [here, optKey, !!r])

  // Back: the incident on Watch it fail, as it was (its cascade stays when it is the map's case; a deep-linked one is
  // put on the map, one click from running)
  const back = () => {
    const body = inc.body
    const onMap = o.cascade ? cleanBody(bodyFor(o.cascade, o.caseBody, o.cascadeBody)) : cleanBody(o.caseBody)
    const same = JSON.stringify(onMap) === inc.key || JSON.stringify(cleanBody(o.caseBody)) === inc.key
    closeIncident()
    dropHash()
    if (!same) {
      const base = applyBase(body, r)
      if (base) applyCase(o, base)
    }
    o.setMode('campus')
  }
  // the statewide answer: the page's normal view (the camera goes back to the whole state)
  const toState = () => {
    closeIncident()
    dropHash()
    mapRef.current?.reset()
  }
  // run the same incident again with this option, on Watch it fail (the results panel shows it with the fix)
  const tryIt = (op) => {
    const fx = describeFix(op.fix, total)
    const base = applyBase(inc.body, r)
    if (!fx || !base) return
    closeIncident()
    dropHash()
    o.setMode('campus')
    runWithFix(o, fx, { base, report: r })
  }

  if (!here || inc.status === 'loading') {
    return (
      <>
        <header className="st-head inc-head">
          <StageBar onBack={back} where={stateName} />
          <p className="inc-title inc-title--wait">{region !== inc.region ? `Opening ${stateName}…` : 'Opening the incident’s solutions…'}</p>
        </header>
        <section className="st-rail inc-rail" aria-label="The solution" aria-busy="true">
          <Loading label="Reading the incident’s report…" />
        </section>
      </>
    )
  }
  if (inc.status === 'error' || !r) {
    return (
      <>
        <header className="st-head inc-head">
          <StageBar onBack={back} where={stateName} />
          <p className="inc-title inc-title--wait">The incident’s report didn’t load.</p>
        </header>
        <section className="st-rail inc-rail" aria-label="The solution">
          <ErrorBanner error={inc.error || new Error('The report did not load')} onRetry={retryIncident} />
          <div className="inc-sec">
            <button type="button" className="st-link" onClick={toState}>
              How many more campuses {stateName}’s grid can take
            </button>
          </div>
        </section>
      </>
    )
  }

  const t = stageTitle(r)
  const ag = r.agentic || {}
  return (
    <>
      <header className="st-head inc-head">
        <StageBar onBack={back} where={r.region_name || stateName} />
        <div className="inc-top">
          <div className="inc-words">
            <h1 className="inc-title">
              <span className="st-sr">Strengthen the grid, incident solution. </span>
              {t.name}: <span className="inc-title__tail">{t.tail}</span>
            </h1>
            <p className="inc-sub">{summaryOf(r, options)}</p>
          </div>
          <Toll r={r} />
        </div>
      </header>

      <MapKey r={r} sel={sel} />

      <section className="st-rail inc-rail" aria-label="The solution">
        <div className="inc-scroll">
          <WeakPoint r={r} o={o} sel={sel} />
          {r.no_fix && (
            <section className="inc-sec inc-nofix" aria-label="What no fix prevents">
              <h2 className="inc-h">What no fix prevents</h2>
              <p>{r.no_fix.sentence}</p>
            </section>
          )}
          <Recovery rec={r.recovery} />
          <section className="inc-sec" aria-labelledby="inc-opts-h">
            <div className="inc-sec__head">
              <h2 className="inc-h" id="inc-opts-h">
                {options.length ? `Every verified way to ${campusOf(r).n ? `build ${campusOf(r).n > 1 ? 'them' : 'it'}` : 'prevent it'}` : 'No verified option'}
              </h2>
              {options.length > 0 && <p className="inc-quiet">{optionsLine(options)}</p>}
            </div>
            {options.length ? (
              <ol className="inc-opts">
                {options.map((op) => (
                  <Option key={op.i} op={op} cur={sel?.group === op ? sel : null} r={r} o={o} lead={lead} total={total} onTry={tryIt} />
                ))}
              </ol>
            ) : (
              <p className="inc-quiet inc-pad">
                {r.verdict === 'nothing_happened' ? 'Nothing trips in this case, so nothing needs fixing.' : 'None of the fixes the engine tried holds for this case.'}
              </p>
            )}
            <AgentLine ag={ag} />
          </section>
          <section className="inc-sec" aria-label="How we know">
            <HowWeKnow body={inc.body} />
          </section>
          <section className="inc-sec inc-next" aria-label="The statewide answer">
            <button type="button" className="inc-next__btn" onClick={toState}>
              <span className="inc-next__q">This was one site.</span>
              <span className="inc-next__a">How many more campuses {r.region_name || stateName}’s grid can take</span>
              <span aria-hidden="true" className="inc-next__arrow">
                →
              </span>
            </button>
          </section>
          <HowFold r={r} lead={lead} />
        </div>
      </section>
    </>
  )
}

function StageBar({ onBack, where }) {
  return (
    <div className="inc-bar">
      {onBack && (
        <button type="button" className="inc-back" onClick={onBack}>
          <span aria-hidden="true">←</span> Back to the incident
        </button>
      )}
      <span className="inc-kicker">Incident solution · synthetic grid model of {where}, not any utility’s network · estimates</span>
    </div>
  )
}

// "Without a fix, a 1,500 MW campus at Fort Myers sets off a 9-step cascade at the 4 PM summer peak, reaching Naples,
// Cape Coral, Fort Myers and 3 more areas. The engine verified 7 distinct ways to prevent it."
function summaryOf(r, options) {
  const ev = r.event || {}
  const c = r.case || {}
  const { total, where, n } = campusOf(r)
  const what = n ? (n > 1 ? `${n} campuses, ${mwText(total)} in all${where ? ` (the first at ${where})` : ''}` : `a ${mwText(total)} campus${where ? ` at ${where}` : ''}`) : c.preset?.name || 'the storm'
  const level = c.load_word || ''
  const areas = areasWords(r)
  const held = options.filter((x) => !x.partly).length
  if (r.verdict === 'nothing_happened') return `${what[0].toUpperCase()}${what.slice(1)} ${level}: nothing goes over its limit, so nothing trips.`
  const reach = areas ? `, reaching ${areas}` : ''
  const steps = stepsWords(ev.steps || 0)
  const head = c.trip_count
    ? `Without a fix, the storm takes down ${fmt(c.trip_count)} ${c.trip_count === 1 ? 'line' : 'lines'}${n ? ` while ${what.replace(/^a /, 'the ')} ${n > 1 ? 'run' : 'runs'}` : ''}, and ${steps} cascade follows ${level}${reach}.`
    : `Without a fix, ${what} ${n > 1 ? 'set' : 'sets'} off ${steps} cascade ${level}${reach}.`
  if (r.verdict === 'no_fix') return `${head} Part of it no fix prevents; what still helps is below.`
  return `${head} The engine verified ${held === 1 ? 'one way' : `${fmt(held)} distinct ways`} to prevent it${held > 1 && n ? ', the ones that keep the full size first' : ''}.`
}

// "6 distinct options, each re-run by the physics engine on this exact case (3 more plans are variants of one of them:
// the same lines and transformers at other ratings). Pick one to see it on the map."
function optionsLine(options) {
  const held = options.filter((x) => !x.partly)
  const more = held.reduce((a, x) => a + x.variants.length - 1, 0)
  const vars = more > 0 ? ` (${fmt(more)} more ${more === 1 ? 'plan is a variant' : 'plans are variants'} of one of them: the same main lines and transformers, other ratings)` : ''
  return `${fmt(held.length)} distinct ${held.length === 1 ? 'option' : 'options'}, each re-run by the physics engine on this exact case${vars}. Pick one to see it on the map.`
}

// the results panel's figures, same words: people hit, time without power, cost of the outage (high end, its range)
function Toll({ r }) {
  const ev = r.event || {}
  const hit = Math.max(Number(ev.people) || 0, Number(ev.people_hit ?? r.replay?.people_hit) || 0)
  const cost = r.cost || {}
  const range = cost.ranges?.blackout_usd || []
  const hi = cost.blackout_high_usd ?? range[1]
  const m = hi ? moneyParts(hi) : null
  if (!(hit > 0)) return null
  return (
    <dl className="inc-toll" aria-label="Without a fix (estimates)">
      <p className="inc-toll__k">Without a fix</p>
      <div>
        <dt>{LABEL.en.hit}</dt>
        <dd>{fmt(hit)}</dd>
      </div>
      {cost.outage_label?.en && (
        <div>
          <dt>{LABEL.en.time}</dt>
          <dd>{cost.outage_label.en}</dd>
        </div>
      )}
      {m && (
        <div>
          <dt>{LABEL.en.cost}</dt>
          <dd>
            {m.figure} <span className="inc-toll__u">{m.unit}</span>
            {range.length === 2 && <span className="inc-toll__r">{moneyRange(range[0], range[1])}</span>}
          </dd>
        </div>
      )}
    </dl>
  )
}

// ------------------------------------------------------------------ the weak point
function WeakPoint({ r, o, sel }) {
  const rc = r.root_cause || {}
  const line = rc.line
  const often = oftenOf(r)
  const { total, where, n } = campusOf(r)
  const many = n > 1
  // the share is of all the case's new load (backend/briefing.py path_share: MW on it / every campus's MW)
  const at = many ? `at the ${fmt(n)} sites` : where ? `at ${where}` : 'there'
  const kindWord = line?.transformer ? 'transformer' : 'line'
  const first = (r.timeline || [])[0]
  const failsFirst = line && first?.lines?.some((x) => Number(x.id) === Number(line.id))
  const b = line ? o.branchById?.get(Number(line.id)) : null
  const name = line
    ? line.transformer
      ? `${titleName(o.subName?.(b?.from_sub) || line.from_area)} transformer`
      : b
        ? `${titleName(o.subName(b.from_sub))} to ${titleName(o.subName(b.to_sub))} line`
        : noThe(line.label)
    : null
  // the campus set it off (or was the last straw on a line already near its limit): today -> with the campus
  const campusView = total > 0 && (rc.cause === 'campus' || rc.cause === 'last_straw')
  const fixedBySel = !!(line && sel && Object.keys(sel.fix?.apply?.upgrades || {}).map(Number).includes(Number(line.id)))
  const up = fixedBySel ? (sel.fix.detail?.list || []).find((x) => Number(x.id) === Number(line.id)) : null
  return (
    <section className="inc-sec inc-wp" aria-labelledby="inc-wp-h">
      <h2 className="inc-h" id="inc-wp-h">
        The weak point
      </h2>
      {line && !campusView ? (
        <>
          <p className="inc-wp__name">
            {name}
            {line.kv ? <span className="inc-wp__kv"> · {fmt(line.kv)} kV</span> : null}
          </p>
          {/* a storm or the heat set it off, not the campus: the engine's own sentence says so */}
          <p className="inc-wp__sentence">{rc.sentence}</p>
          {fixedBySel && up && (
            <p className="inc-wp__fixed">
              The option on screen raises it from {fmt(up.old_mva)} to {fmt(up.new_mva)} MVA.
            </p>
          )}
        </>
      ) : line ? (
        <>
          <p className="inc-wp__name">
            {name}
            {line.kv ? <span className="inc-wp__kv"> · {fmt(line.kv)} kV</span> : null}
          </p>
          {rc.pct_without != null && rc.pct_with != null && <Gauge before={rc.pct_without} after={rc.pct_with} withWord={many ? 'with the campuses' : 'with the campus'} />}
          <ul className="inc-wp__why">
            {rc.path_share_pct != null &&
              (rc.path_share_pct >= 30 ? (
                <li>
                  <b>{Math.round(rc.path_share_pct)} %</b> of any new load {at} flows through it, so {many ? 'the campuses lean' : 'a campus there leans'} on it first.
                </li>
              ) : rc.cause === 'last_straw' ? (
                // the engine's "last straw": the element was already near its limit (90 % or more) before any new load
                <li>
                  Only <b>{Math.round(rc.path_share_pct)} %</b> of any new load {at} flows through it, but it was already at{' '}
                  {rc.pct_without != null ? pct(rc.pct_without) : 'the edge'} of its rating{r.case?.load_word ? ` ${r.case.load_word}` : ''}: a small share was enough.
                </li>
              ) : (
                <li>
                  Only <b>{Math.round(rc.path_share_pct)} %</b> of any new load {at} flows through it, but that share is more than this {kindWord} has room for
                  {rc.pct_without != null && rc.pct_with != null ? `: it goes from ${pct(rc.pct_without)} to ${pct(rc.pct_with)} of its rating` : ''}.
                </li>
              ))}
            {rc.campus_mw_on_line != null && total > 0 && (
              <li>
                <b>{mwText(rc.campus_mw_on_line)}</b> of the {many ? `${fmt(n)} campuses’` : 'campus’s'} {mwText(total)} {many ? 'end' : 'ends'} up on it
                {rc.campus_share_pct != null ? `: ${Math.round(rc.campus_share_pct)} % of what it carries then` : ''}.
              </li>
            )}
            {failsFirst && (
              <li>
                It fails first: step 1 of the {fmt(r.event?.steps || 0)}-step cascade, and its load moves onto the lines around it.
              </li>
            )}
            {fixedBySel && up && (
              <li className="inc-wp__fixed">
                The option on screen raises it from {fmt(up.old_mva)} to {fmt(up.new_mva)} MVA.
              </li>
            )}
          </ul>
        </>
      ) : (
        <p className="inc-quiet">{rc.sentence || 'No single line or transformer sets this incident off.'}</p>
      )}
      {often && total > 0 && (
        <div className="inc-often">
          <p className="inc-often__h">
            How often: the full {mwText(total)} overloads the grid <b>{often.words}</b>.
          </p>
          <ol className="inc-often__cells" aria-label="The engine’s check at each hour">
            {often.levels.map((x) => (
              <li key={x.level} className={x.over ? 'is-over' : 'is-ok'}>
                <span className="inc-often__n">{hourName(x.name)}</span>
                <span className="inc-often__v">{x.over ? (x.overLines != null ? `${fmt(x.overLines)} over` : 'over') : 'holds'}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  )
}

// "in a heat wave" -> "Heat wave" (a cell's label)
const hourName = (name) => {
  const s = String(name || '').replace(/^in an? /, '').replace(/^at the /, '')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// today's loading -> with the campus, against the 100 % rating
function Gauge({ before, after, withWord = 'with the campus' }) {
  const top = Math.max(150, Math.ceil(after / 25) * 25)
  const w = (v) => `${(Math.min(v, top) / top) * 100}%`
  const over = after > 100
  return (
    <div className="inc-gauge" role="img" aria-label={`${pct(before)} of its rating today, ${pct(after)} ${withWord}`}>
      <div className="inc-gauge__bar">
        <span className="inc-gauge__today" style={{ width: w(before) }} />
        <span className={`inc-gauge__add${over ? ' is-over' : ''}`} style={{ left: w(before), width: `calc(${w(after)} - ${w(before)})` }} />
        <span className="inc-gauge__limit" style={{ left: w(100) }} />
      </div>
      <div className="inc-gauge__labels">
        <span>
          <b>{pct(before)}</b> of its rating today
        </span>
        <span className={over ? 'is-over' : ''}>
          <b>{pct(after)}</b> {withWord}
          {over ? ': past its limit, it trips' : ''}
        </span>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ one option
function keepsText(op, total) {
  const f = op.fix
  const d = f.detail || {}
  if (op.kind === 'rule') {
    const lv = (d.levels || []).slice().sort((a, b) => a.level - b.level)
    const low = lv.filter((x) => !x.full)
    if (!low.length) return `All ${mwText(total)}`
    const peak = lv.find((x) => Math.abs(x.level - 1) < 0.005)
    return peak && !peak.full ? `${mwText(peak.runs_mw)} at the 4 PM peak` : `${mwText(low[0].runs_mw)} ${low[0].name}`
  }
  if (op.kind === 'onsite') return `All ${mwText(total)}, ${mwText(d.net_mw ?? f.kept_mw ?? 0)} from the grid`
  if (op.kind === 'move') return `All ${mwText(total)} at ${d.sites?.[0]?.town || 'another site'}`
  const kept = f.kept_mw ?? d.mw
  if (kept != null && total && kept < total - 0.5) return `${mwText(kept)} of ${mwText(total)}`
  return total ? `All ${mwText(total)}` : 'The grid as is'
}

function costText(op) {
  const c = op.fix.cost
  if (c?.high > 0) return moneyRange(c.low, c.high)
  if (op.kind === 'onsite') return 'Plant not priced'
  return 'No grid upgrades'
}

// one option's row (its own plan: the cheapest of its variants) and, open, the plan picked among its variants in depth
function Option({ op, cur, r, o, lead, total, onTry }) {
  const open = !!cur
  // the row speaks for the plan on screen: the option's own, or the variant picked below it
  const shown = cur || op
  const f = shown.fix
  const els = useMemo(() => elementsOf(f, o.branchById, o.subName), [f, o.branchById, o.subName])
  const time = leadOf(shown, els, lead)
  const gem = f.by === 'gemini' || f.family === 'agentic'
  const sc = scopeOf(f)
  const nv = op.variants.length
  const gemVars = op.variants.slice(1).filter((v) => v.fix.by === 'gemini' || v.fix.family === 'agentic').length
  const topCost = Math.max(...op.variants.map((v) => Number(v.fix.cost?.high) || 0))
  const id = `inc-opt-${op.i}`
  const ref = useRef(null)
  // opened from the list: keep it in view in the rail (a phone scrolls the page instead)
  useEffect(() => {
    if (open && ref.current?.dataset.picked === '1') {
      ref.current.dataset.picked = ''
      ref.current.scrollIntoView({ block: 'start', behavior: reducedMotion() ? 'auto' : 'smooth' })
    }
  }, [open])
  return (
    <li className={`inc-opt${open ? ' is-open' : ''}${op.partly ? ' is-partly' : ''}`} ref={ref}>
      <button
        type="button"
        className="inc-opt__row"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          if (ref.current) ref.current.dataset.picked = '1'
          selectOption(op.i)
        }}
      >
        <span className="inc-opt__k">
          {op.label}
          {op.partly ? ' · helps, doesn’t hold' : op.note ? ` · ${op.note.toLowerCase()}` : ''}
        </span>
        <span className="inc-opt__cost">{costText(shown)}</span>
        <span className="inc-opt__a">{fixWords(f, total)}</span>
        <span className="inc-opt__meta">
          <span>{keepsText(shown, total)}</span>
          {sc && <span>{scopeText(sc)}</span>}
          {time && <span>{yearsText(time)}</span>}
          <AiBadge by={gem ? 'gemini' : 'engine'} verified={gem} />
          {nv > 1 &&
            (open ? (
              <span className="inc-opt__vars">
                Plan {fmt(op.variants.findIndex((v) => v.i === cur.i) + 1)} of {fmt(nv)}
              </span>
            ) : (
              <span className="inc-opt__vars">
                +{fmt(nv - 1)} {nv - 1 === 1 ? 'variant' : 'variants'}
                {gemVars > 0 && gemVars === nv - 1 && !gem ? ' by Gemini' : ''}
                {topCost > 0 ? `, up to ${money(topCost)}` : ''}
              </span>
            ))}
        </span>
      </button>
      {open && <OptionDetail id={id} op={cur} r={r} o={o} lead={lead} total={total} onTry={() => onTry(cur)} />}
    </li>
  )
}

// The variants of one option: the same main lines and transformers, other ratings, some with a smaller line beside
// them. Each is a plan the engine re-ran and found holding; picking one shows it below and on the map.
function Variants({ op, r, o }) {
  const group = op.group
  if (!group || group.variants.length < 2) return null
  const main = mainOf(group.fix, firstOf(r))
  return (
    <div className="inc-blk inc-vars">
      <p className="inc-blk__k">
        {fmt(group.variants.length)} verified plans for this {group.kind === 'combo' || group.kind === 'combo_deep' ? 'option' : 'upgrade'}
      </p>
      <p className="inc-note">
        They raise the same main lines and transformers to other ratings, some with a smaller line beside them. Every one holds on this case; the cheapest comes
        first.
      </p>
      <ul className="inc-vars__list">
        {group.variants.map((v) => {
          const gem = v.fix.by === 'gemini' || v.fix.family === 'agentic'
          const on = v.i === op.i
          const els = elementsOf(v.fix, o.branchById, o.subName)
          const mains = els.filter((e) => main.has(e.id))
          const extra = els.filter((e) => !main.has(e.id))
          return (
            <li key={v.i}>
              <button type="button" className={`inc-var${on ? ' is-on' : ''}`} aria-pressed={on} onClick={() => selectOption(v.i)}>
                <span className="inc-var__cost">{v.fix.cost?.high > 0 ? money(v.fix.cost.high) : costText({ ...group, fix: v.fix })}</span>
                <span className="inc-var__who">
                  <AiBadge by={gem ? 'gemini' : 'engine'} verified={gem} />
                </span>
                <span className="inc-var__what">
                  {mains.map((e) => `${shortName(e)} to ${fmt(e.newMva)}`).join(' · ')}
                  {mains.length ? ' MVA' : ''}
                  {extra.length > 0 && (
                    <span className="inc-var__extra">
                      {' '}
                      + {extra.map((e) => `${shortName(e)} to ${fmt(e.newMva)} MVA`).join(', ')}
                    </span>
                  )}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

// "North Fort Myers 6 transformer", "North Fort Myers 6–Fort Myers 3 line"
const shortName = (e) => (e.ends?.length === 2 ? `${e.ends[0]}–${e.ends[1]} line` : e.name)

function OptionDetail({ id, op, r, o, lead, total, onTry }) {
  const f = op.fix
  const els = useMemo(() => elementsOf(f, o.branchById, o.subName), [f, o.branchById, o.subName])
  const time = leadOf(op, els, lead)
  const p = preventsOf(f, r)
  const gem = f.by === 'gemini' || f.family === 'agentic'
  const s0 = r.strain?.with_campus?.peak_pct
  const s1 = f.strain?.peak_pct
  const alone = r.strain?.grid_alone?.peak_pct
  const level = r.case?.load_word || 'at the case’s hour'
  const costNote = (r.cost?.assumptions || []).find((a) => a.key === 'upgrades')?.note
  const costSources = (r.cost?.sources || []).filter((s) => /Black & Veatch|2035 Report|CPI/.test(s.name))
  return (
    <div className="inc-det" id={id}>
      <Variants op={op} r={r} o={o} />
      {/* what gets built or changed */}
      <div className="inc-blk">
        <p className="inc-blk__k">{els.length ? 'What gets built' : 'What changes'}</p>
        <Changes op={op} els={els} r={r} lead={lead} total={total} />
      </div>

      {/* what it prevents, and the strain */}
      <div className="inc-blk">
        <p className="inc-blk__k">What it prevents</p>
        {p.all ? (
          <p className="inc-blk__v">
            The whole incident: <b>{people(p.hit)}</b> hit{p.hours ? `, ${p.hours} without power` : ''}
            {p.high ? (
              <>
                , <b>{p.low != null ? moneyRange(p.low, p.high) : money(p.high)}</b> of outage cost
              </>
            ) : null}{' '}
            <span className="inc-est">(estimates)</span>. With it, nothing trips and nobody loses power.
          </p>
        ) : (
          <p className="inc-blk__v">
            Part of it: {p.leftSteps ? `the engine still finds ${stepsWords(p.leftSteps)} cascade, with ` : 'it stops the cascade, but '}
            <b>{people(p.left)}</b> {p.leftSteps ? '' : 'are '}still without power <span className="inc-est">(estimate)</span>, against {people(p.stillOut)} without it
            {!p.leftSteps && p.cutOff ? ': the storm cut them off, and only rebuilding the downed lines brings them back' : ''}.
          </p>
        )}
        {s0 != null && s1 != null && <StrainBars before={s0} after={s1} alone={alone} />}
      </div>

      {/* cost */}
      <div className="inc-blk">
        <p className="inc-blk__k">
          Cost <span className="inc-blk__kv">{costText(op)}</span>
        </p>
        {f.cost?.high > 0 ? (
          <p className="inc-note">
            Low end to high end, priced element by element above. {costNote || ''}{' '}
            {costSources.map((s, k) => (
              <span key={s.url}>
                {k > 0 ? ' · ' : ''}
                <a href={s.url} target="_blank" rel="noreferrer">
                  {sourceShort(s.name)}
                </a>
              </span>
            ))}
          </p>
        ) : (
          <p className="inc-note">{noCostWords(op, total)}</p>
        )}
      </div>

      {/* typical time to build */}
      <div className="inc-blk">
        <p className="inc-blk__k">
          Typical time to build <span className="inc-blk__kv">{time ? yearsText(time) : '—'}</span>
        </p>
        {time ? (
          <p className="inc-note">
            Set by {time.label}. {time.basis}{' '}
            {time.sources.map((s, k) => (
              <span key={s.url}>
                {k > 0 ? ' · ' : ''}
                <a href={s.url} target="_blank" rel="noreferrer">
                  {s.short || s.name}
                </a>
              </span>
            ))}
            {op.kind === 'onsite' && ' Those ranges are for grid-connected plants; a plant on the campus’s own site can differ.'}
            {op.kind === 'rule' && lead?.flexNote ? ` ${lead.flexNote}` : ''}
          </p>
        ) : (
          <p className="inc-note">The typical build-time ranges didn’t load: no time is given rather than a guess.</p>
        )}
      </div>

      {/* how it was checked, who proposed it */}
      <div className="inc-blk">
        <p className="inc-blk__k">Checked</p>
        <p className="inc-note inc-note--ink">
          Re-run {level} by {checkedWords(f)}.{' '}
          {resultWords(p)}
          {op.kind !== 'rule' ? ` Checked at this hour; the other hours are the “how often” line above.` : ''}
        </p>
      </div>
      <div className="inc-blk">
        <p className="inc-blk__k">
          Proposed by <AiBadge by={gem ? 'gemini' : 'engine'} verified={gem} />
        </p>
        <p className="inc-note">{foundWords(op, r)}</p>
        {gem && f.detail?.why && (
          <blockquote className="inc-why">
            “{f.detail.why}” <span className="inc-why__by">Gemini’s reasoning, in its words: not a checked fact</span>
          </blockquote>
        )}
        {(op.kind === 'combo' || op.kind === 'combo_deep' || op.partly) && f.tradeoff && <p className="inc-note">{cleanTradeoff(f.tradeoff)}</p>}
      </div>

      {op.fix.apply && (
        <div className="inc-try">
          <Button variant="secondary" onClick={onTry}>
            Run the incident again with this
          </Button>
          <span className="inc-note">On Watch it fail: the same case with this change; nothing should trip.</span>
        </div>
      )}
    </div>
  )
}

// How the option was found, in plain words (what each engine family does: backend/briefing.py _fixes, fixit.py;
// Gemini's: backend/solutions.py propose, re-run by the engine)
function foundWords(op, r) {
  const f = op.fix
  const d = f.detail || {}
  const level = r.case?.load_word || 'at this hour'
  if (f.by === 'gemini' || f.family === 'agentic')
    return 'Gemini proposed the lines and ratings from the case’s facts and the lines it may raise; the engine then priced the plan itself (Gemini’s own numbers are never used) and re-ran the case with it. It counts only because it holds.'
  switch (f.family) {
    case 'upgrade':
      return 'The engine’s upgrade search: take the most overloaded line or transformer, raise its rating (in standard 50 MVA steps) until it carries its flow at 80 % or less, and repeat until none is over its rating.'
    case 'combo':
      return `A small lowering first (people want the power), then the same upgrade search for what is left: ${mwText(d.mw ?? f.kept_mw ?? 0)}.`
    case 'shrink':
      return 'The engine’s largest size that fits at this site with no line or transformer over its rating (a search over sizes, one power-flow solve each).'
    case 'flexible':
      return 'The engine’s largest size that fits at each hour it checks, one power-flow solve per hour.'
    case 'onsite':
      return `The grid carries what the smaller campus draws (${mwText(d.net_mw ?? 0)}, the same verified run); the rest comes from a plant on the campus’s own site.`
    case 'move':
      return `The engine tried the roomiest towns first (${fmt(d.tried || 0)} tried) and kept those where all ${mwText(campusOf(r).total)} fits ${level} with nothing over its rating.`
    default:
      return 'Found and checked by the engine.'
  }
}

// "Engine check: …" and "Proposed by AI, then re-run by the engine: it holds." say what the blocks above already say
const cleanTradeoff = (s) =>
  String(s)
    .replace(/^Proposed by AI, then re-run by the engine: it holds\.\s*/, '')
    .trim()

const sourceShort = (name) =>
  /Black & Veatch/.test(name) ? 'Black & Veatch, 2014' : /2035 Report/.test(name) ? 'GridLab and UC Berkeley, 2024' : /CPI/.test(name) ? 'BLS CPI-U' : name

function noCostWords(op, total) {
  const d = op.fix.detail || {}
  if (op.kind === 'onsite')
    return `No grid upgrades. The campus builds and runs its own ${mwText(d.onsite_mw || 0)} plant; that plant isn’t priced here (it depends on the kind of plant and fuel).`
  if (op.kind === 'rule') return 'No grid upgrades. The cost is the computing the campus gives up at those hours (not priced here).'
  if (op.kind === 'move') return 'No grid upgrades at the new site: the campus’s own connection is part of its build, as it would be here.'
  if (op.kind === 'shrink') return `No grid upgrades. The cost is the ${mwText(Math.max(total - (op.fix.kept_mw || 0), 0))} not built.`
  return 'No grid upgrades.'
}

// the busiest line of the grid: with the campus, and with the campus AND this option
function StrainBars({ before, after, alone }) {
  const top = Math.max(150, Math.ceil(Math.max(before, after) / 25) * 25)
  const w = (v) => `${(Math.min(v, top) / top) * 100}%`
  return (
    <div className="inc-strain" role="img" aria-label={`The busiest line: ${pct(before)} of its rating with the campus, ${pct(after)} with this option`}>
      <div className="inc-strain__row">
        <span className="inc-strain__k">Busiest line, no fix</span>
        <span className="inc-strain__bar">
          <span className={`inc-strain__fill${before > 100 ? ' is-over' : ' is-warm'}`} style={{ width: w(before) }} />
          <span className="inc-strain__limit" style={{ left: w(100) }} />
        </span>
        <span className="inc-strain__v">{pct(before)}</span>
      </div>
      <div className="inc-strain__row">
        <span className="inc-strain__k">With this</span>
        <span className="inc-strain__bar">
          <span className={`inc-strain__fill${after > 100 ? ' is-over' : ' is-ok'}`} style={{ width: w(after) }} />
          <span className="inc-strain__limit" style={{ left: w(100) }} />
        </span>
        <span className="inc-strain__v">{pct(after)}</span>
      </div>
      {alone != null && <p className="inc-strain__note">Of its rating, in one power-flow solve before anything trips; the grid alone runs its busiest line at {pct(alone)}.</p>}
    </div>
  )
}

// ------------------------------------------------------------------ what gets built or changed
function Changes({ op, els, r, lead, total }) {
  const f = op.fix
  const d = f.detail || {}
  const kept = f.kept_mw ?? d.mw
  const head =
    op.kind === 'combo' || op.kind === 'combo_deep' ? (
      <p className="inc-blk__v">
        Build <b>{mwText(kept)}</b> instead of {mwText(total)} ({Math.round(f.kept_pct ?? d.kept_pct ?? 0)} %), and raise:
      </p>
    ) : null
  if (els.length) {
    const top = Math.max(...els.map((e) => e.newMva), 1)
    // the engine's own totals (its element list is cut at 20 for a big plan)
    const sc = scopeOf(f)
    return (
      <>
        {head}
        <ol className="inc-els">
          {els.map((e) => (
            <Element key={e.id} e={e} top={top} lead={lead} />
          ))}
        </ol>
        {sc && sc.n > 1 && (
          <p className="inc-note">
            {scopeText(sc)} in all
            {sc.km ? `, ${sc.km.toLocaleString('en-US', { maximumFractionDigits: 1 })} km of line` : ''}
            {sc.listed < sc.n ? `; the first ${fmt(sc.listed)} are listed` : ''}. The numbers match the pins on the map.
          </p>
        )}
      </>
    )
  }
  if (op.kind === 'rule') {
    const lv = (d.levels || []).slice().sort((a, b) => a.level - b.level)
    return (
      <>
        <p className="inc-blk__v">An operating rule in the connection agreement: the campus steps down to what the grid can carry at each hour, and runs in full when it can.</p>
        <table className="inc-rule">
          <caption className="st-sr">What the campus runs at each hour the engine checked</caption>
          <thead>
            <tr>
              <th scope="col">Hour</th>
              <th scope="col">Runs</th>
              <th scope="col">Steps down</th>
            </tr>
          </thead>
          <tbody>
            {lv.map((x) => (
              <tr key={x.level} className={x.full ? 'is-full' : ''}>
                <th scope="row">{x.name}</th>
                <td>
                  <span className="inc-rule__bar" aria-hidden="true">
                    <span style={{ width: `${Math.min(100, (x.runs_mw / (total || 1)) * 100)}%` }} />
                  </span>
                  {x.full ? `All ${mwText(total)}` : mwText(x.runs_mw)}
                </td>
                <td>{x.full ? '—' : mwText(Math.max(total - x.runs_mw, 0))}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="inc-note">Each hour’s size is the engine’s own fit at that hour’s demand (a power-flow solve per hour).</p>
      </>
    )
  }
  if (op.kind === 'move') {
    const s = d.sites?.[0]
    const also = (d.sites || []).slice(1).map((x) => x.town)
    return (
      <>
        <p className="inc-blk__v">
          Connect the whole {mwText(total)} at <b>{s ? titleName(s.name) : 'another substation'}</b> instead of {titleName(r.case?.sub_name || r.case?.sub_area || 'this site')}
          {s?.headroom_mw != null ? (
            <>
              : room there for <b>{mwText(Math.min(s.headroom_mw, 1e6))}</b> before the first overload
            </>
          ) : null}
          {s?.max_pct != null ? `; with the campus its busiest line runs at ${pct(s.max_pct)} of its rating` : ''}.
        </p>
        {also.length > 0 && <p className="inc-note">Also fits: {also.join(', ')}.</p>}
        <p className="inc-note">Sites are substations named after towns in a synthetic model, not real addresses.</p>
      </>
    )
  }
  if (op.kind === 'onsite')
    return (
      <p className="inc-blk__v">
        The campus runs all <b>{mwText(total)}</b>: <b>{mwText(d.onsite_mw || 0)}</b> from its own plant on site, <b>{mwText(d.net_mw || 0)}</b> from the grid, what the grid
        carries here without an overload.
      </p>
    )
  if (op.kind === 'shrink')
    return (
      <p className="inc-blk__v">
        Build <b>{mwText(kept)}</b> instead of {mwText(total)} ({Math.round(f.kept_pct ?? d.kept_pct ?? 0)} %)
        {d.room_mw != null ? `: the site’s room before the first overload is ${mwText(d.room_mw)}` : ''}.
      </p>
    )
  return <p className="inc-blk__v">{f.action}</p>
}

function Element({ e, top, lead }) {
  const w = (v) => `${(v / top) * 100}%`
  const kind = lead?.items?.[e.lead]
  const one = (v) => v.toLocaleString('en-US', { maximumFractionDigits: 1 })
  const len = e.transformer ? null : e.miles != null ? `${e.km ? `${one(e.km)} km, ` : ''}${one(e.miles)} mi` : null
  return (
    <li className="inc-el" onMouseEnter={() => setAim(e.id)} onMouseLeave={() => setAim(null)}>
      <span className="inc-el__n" aria-hidden="true">
        {e.n}
      </span>
      <div className="inc-el__body">
        <p className="inc-el__name">
          {e.name}
          <span className="inc-el__tag">
            {e.transformer ? 'transformer' : 'line'}
            {e.kv ? ` · ${fmt(e.kv)} kV` : ''}
            {len ? ` · ${len}` : ''}
          </span>
        </p>
        <p className="inc-el__mva">
          <b>
            {fmt(e.oldMva)} → {fmt(e.newMva)} MVA
          </b>{' '}
          <span className="inc-el__add">+{fmt(e.added)}</span>
        </p>
        <span className="inc-el__bar" role="img" aria-label={`Rating from ${fmt(e.oldMva)} to ${fmt(e.newMva)} MVA`}>
          <span className="inc-el__old" style={{ width: w(e.oldMva) }} />
          <span className="inc-el__new" style={{ left: w(e.oldMva), width: `calc(${w(e.newMva)} - ${w(e.oldMva)})` }} />
        </span>
        <p className="inc-note">
          <b className="inc-el__work">{WORK[e.work]?.short || 'Upgrade'}.</b> {WORK[e.work]?.text(e)}
        </p>
        <p className="inc-el__meta">
          {e.high != null ? (
            <span>
              {moneyRange(e.low, e.high)}
              {e.pricedMiles ? ` (priced as ${one(e.pricedMiles)} ${e.pricedMiles === 1 ? 'mile' : 'miles'}, the shortest line priced)` : ''}
            </span>
          ) : null}
          {kind ? (
            <span>
              {yearsText(kind)} ({kind.short})
            </span>
          ) : null}
        </p>
      </div>
    </li>
  )
}

// ------------------------------------------------------------------ a storm's downed lines: how the lights come back
// (backend/briefing.py _recovery: the engine's repair order, wave by wave, each wave's people back counted by a
// controlled-pickup solve)
function Recovery({ rec }) {
  const waves = (rec?.waves || []).filter((w) => w && w.lines_total != null)
  if (!rec || !waves.length) return null
  const b = rec.baseline
  return (
    <section className="inc-sec inc-rec" aria-labelledby="inc-rec-h">
      <h2 className="inc-h" id="inc-rec-h">
        How the lights come back
      </h2>
      <p className="inc-quiet">
        {fmt(rec.damaged_lines)} downed {rec.damaged_lines === 1 ? 'line' : 'lines'} to rebuild. The engine’s repair order, wave by wave ({rec.method_note}):
      </p>
      <ol className="inc-rec__waves">
        {waves.slice(0, 6).map((w, i) => (
          <li key={i}>
            <span className="inc-rec__n">{fmt(w.lines_total)} rebuilt</span>
            <span>
              {fmt(w.people_back)} people back, {fmt(w.people_out)} still out <span className="inc-est">(estimates)</span>
            </span>
          </li>
        ))}
      </ol>
      {b?.plan_better_by > 0 && (
        <p className="inc-note">
          After the first {fmt(b.repairs)} repairs this order leaves {fmt(b.plan_better_by)} fewer people in the dark than rebuilding the biggest lines first.
        </p>
      )}
      {rec.still_out_after_all != null && <p className="inc-note">Even with every line rebuilt, about {fmt(rec.still_out_after_all)} people (estimate) stay dark at this load.</p>}
    </section>
  )
}

// ------------------------------------------------------------------ Gemini's proposer, while it runs / when done
function AgentLine({ ag }) {
  if (ag.status === 'running')
    return (
      <p className="inc-gem" aria-live="polite">
        <AiBadge by="gemini" /> <span>Gemini is proposing more plans for this case; the engine re-runs each one and the ones that hold join the list.</span>
      </p>
    )
  if (ag.status === 'done' && ag.asked)
    return (
      <p className="inc-gem">
        <AiBadge by="gemini" verified={ag.verified > 0} />{' '}
        <span>
          Gemini proposed {fmt(ag.asked)} {ag.asked === 1 ? 'plan' : 'plans'} in {fmt(ag.rounds || 1)} {ag.rounds === 1 ? 'round' : 'rounds'}; the engine re-ran each and
          kept {fmt(ag.verified || 0)} that hold{ag.added > 0 ? '' : ' (none new)'}.
        </span>
      </p>
    )
  return null
}

// ------------------------------------------------------------------ the key over the map
function MapKey({ r, sel }) {
  const dark = Object.keys(r.replay?.affected || {}).length > 0
  const move = sel?.kind === 'move'
  return (
    <ul className="st-key inc-key" aria-label="Map key">
      <li>
        <span className="inc-key__wp" aria-hidden="true" />
        Weak point
      </li>
      {sel && (sel.fix.detail?.list?.length ?? 0) > 0 && (
        <li>
          <span className="st-key__up" aria-hidden="true" />
          This option’s upgrades
        </li>
      )}
      <li>
        <span className="inc-key__camp" aria-hidden="true" />
        Campus
      </li>
      {move && (
        <li>
          <span className="inc-key__move" aria-hidden="true" />
          New site
        </li>
      )}
      {(r.replay?.trip || r.case?.trip || []).length > 0 && (
        <li>
          <span className="inc-key__downed" aria-hidden="true" />
          Downed by the storm
        </li>
      )}
      {dark && (
        <li>
          <span className="inc-key__dark" aria-hidden="true" />
          Loses power without a fix
        </li>
      )}
    </ul>
  )
}

// ------------------------------------------------------------------ how it was worked out
function HowFold({ r, lead }) {
  const sources = [...(r.cost?.sources || [])]
  for (const s of Object.values(lead?.sources || {})) if (!sources.some((x) => x.url === s.url)) sources.push(s)
  return (
    <details className="st-fold inc-how">
      <summary>How this was worked out, sources and limits</summary>
      <p>
        This is the briefing the engine already computed for this exact case: the same site, size, time of day{r.case?.trip_count ? ', storm' : ''} and campuses. Each option
        was re-run on the case by the DC power-flow engine; Gemini’s plans count only once the engine has re-run them and they hold.
      </p>
      <p>
        Costs use published per-mile and per-MVA figures (sources below), priced element by element (low end to high end); land, permits and substation work beyond
        transformers are not included. Times to build are typical ranges from published sources for that kind of work, not a schedule: every one varies by utility,
        region and project.
      </p>
      <p>
        Estimates on a synthetic grid model (Breakthrough Energy / Texas A&amp;M, CC-BY 4.0), not any utility’s network: not an interconnection study, and not a
        prediction about any real project.
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
  )
}

// ------------------------------------------------------------------ the camera
function framePoints(r, sel, o) {
  const pts = []
  const c = r.case || {}
  if (c.sub_lat != null) pts.push([c.sub_lon, c.sub_lat])
  for (const s of c.sites || []) if (s.sub_lat != null) pts.push([s.sub_lon, s.sub_lat])
  const line = r.root_cause?.line
  if (line) {
    const a = o.subPos(line.from_sub)
    const b = o.subPos(line.to_sub)
    if (a) pts.push(a)
    if (b) pts.push(b)
  }
  for (const x of sel?.fix?.detail?.list || []) {
    const b = o.branchById.get(Number(x.id))
    if (b) pts.push(o.subPos(b.from_sub), o.subPos(b.to_sub))
  }
  if (sel?.kind === 'move') {
    const s = sel.fix.detail?.sites?.[0]
    if (s) pts.push([s.lon, s.lat])
  }
  return pts.filter(Boolean)
}
