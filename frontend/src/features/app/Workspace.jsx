import { useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { STATES, fmt } from '../../geo'
import { HeadroomToggle, overLimitText } from '../../shell/CampusPanel'
import useCountUp from '../../shell/useCountUp'
import { stepMsFor, townOf, useOverload } from '../../store'
import { Badge, Button, ErrorBanner, Field } from '../../ui'
import BoomPanel from '../boom/BoomPanel'
import MapLegend from '../flow/MapLegend'
import HeatClock from '../heat/HeatClock'
import { presetFor } from '../heat/presets'
import HurricanePanel from '../hurricane/HurricanePanel'
import TownsFeed from '../impact/TownsFeed'
import AreaFinder from './AreaFinder'
import { MODEL_MW_MAX, placeText, statusText, testQuery, useCatalog } from './catalogData'
import { afterRun, beforeRun, whyNumber } from './explain'
import { Optional, hasModule } from './optional'
import { go, href } from './router'
import { areaName, scenarioSentence } from './sentence'

// The workspace for one state: the scenario as a recipe on the left, the map in the middle, the
// result on the right, the replay along the bottom, and the scenario written as a question on top.

export const moodOf = (lf) => (lf < 0.7 ? 'night' : lf < 0.9 ? 'morning' : lf > 1.001 ? 'heat' : 'peak')
const FL_ONLY = new Set(['hurricane', 'boom']) // their presets and storm tracks are Florida's (see open issues)
const near = (a, b) => a && b && Math.abs(a.lat - b.lat) < 0.002 && Math.abs(a.lon - b.lon) < 0.002

// ------------------------------------------------------------------ the inspector's tab (shared with the URL)
let panelState = 'result'
const panelSubs = new Set()
const setPanel = (p) => {
  panelState = p
  panelSubs.forEach((fn) => fn())
}
const usePanel = () =>
  useSyncExternalStore(
    (fn) => {
      panelSubs.add(fn)
      return () => panelSubs.delete(fn)
    },
    () => panelState,
  )

// ------------------------------------------------------------------ the review stage (briefing track)
let stageState = false
const stageSubs = new Set()
const setStage = (on) => {
  stageState = !!on
  stageSubs.forEach((fn) => fn())
}
const useStage = () =>
  useSyncExternalStore(
    (fn) => {
      stageSubs.add(fn)
      return () => stageSubs.delete(fn)
    },
    () => stageState,
  )

// The incident briefing over the live map, for the case on screen (features/briefing ReviewStage).
export function ReviewHost() {
  const open = useStage()
  const { caseBody, resetCount } = useOverload()
  useEffect(() => () => setStage(false), []) // leaving the workspace closes it
  useEffect(() => setStage(false), [resetCount]) // so does Start over or another state
  if (!open) return null
  return <Optional from="briefing/ReviewStage" body={caseBody} onClose={() => setStage(false)} autoPlay loading={null} fallback={null} />
}

// ------------------------------------------------------------------ entering a workspace
// #/next/state/XX?lat&lon&mw&t&firm&dc&panel: set the store's region, and place the campus the
// query describes (once per navigation). Then the address mirrors the case, so a reload or a copied
// link opens the same scenario.
export function useWorkspaceEntry(route) {
  const o = useOverload()
  const live = useRef(o)
  useEffect(() => {
    live.current = o // runs before the entry effect below, so it reads this render's store
  })
  useEffect(() => {
    if (route.page !== 'state') return
    const s = live.current
    const code = route.code
    if (!isKnownState(code, s.regions)) return
    const q = route.query
    const lat = Number(q.lat)
    const lon = Number(q.lon)
    // a point outside the state's outline (a hand-edited link) is ignored rather than sent to a 422
    const place = q.lat && q.lon && Number.isFinite(lat) && Number.isFinite(lon) && inState(code, lat, lon) ? [lat, lon] : null
    const mw = q.mw && Number.isFinite(Number(q.mw)) ? Math.min(MODEL_MW_MAX, Math.max(1, Math.round(Number(q.mw)))) : undefined
    if (s.region !== code) {
      s.setRegion(code, place ? { place, mw } : {})
    } else if (place) {
      if (mw && mw !== s.mw) s.setMw(mw)
      if (!near(s.site, { lat, lon })) {
        s.setMode('campus')
        s.place(lat, lon)
      }
    }
    // A link that places a campus describes the whole case: no `t` means 4 PM, no `firm` means
    // flexible (the address only writes them when they differ). Without a campus, keep what's on screen.
    const t = Number(q.t)
    const tOk = q.t && Number.isFinite(t) && t >= 0.4 && t <= 1.4
    const lf = tOk ? t : place ? 1 : null
    if (lf !== null && lf !== s.loadFactor) s.setLoadFactor(lf)
    const firm = q.firm !== undefined ? q.firm === '1' : place ? false : null
    if (firm !== null && firm !== s.firm) s.setFirm(firm)
    setPanel(q.panel === 'area' ? 'area' : 'result')
  }, [route])

  // the address follows the case (a replace: no new history entry, no re-entry)
  const panel = usePanel()
  const { region, grid, site, mw, loadFactor, firm } = o
  useEffect(() => {
    if (route.page !== 'state' || region !== route.code || grid?.meta?.region !== route.code) return
    const q = {}
    if (site) Object.assign(q, { lat: site.lat.toFixed(4), lon: site.lon.toFixed(4), mw: Math.round(mw) })
    if (loadFactor !== 1) q.t = loadFactor
    if (firm) q.firm = 1
    if (site && route.query.dc && near(site, { lat: Number(route.query.lat), lon: Number(route.query.lon) })) q.dc = route.query.dc
    if (panel === 'area') q.panel = 'area'
    go(`/state/${route.code}`, q, { replace: true })
  }, [route, region, grid, site, mw, loadFactor, firm, panel])
}

// inside the state's outline box, with a little slack for a coastal or border site
function inState(code, lat, lon) {
  const b = STATES[code]?.bbox
  if (!b) return true
  const m = 0.3
  return lon >= b[0] - m && lon <= b[2] + m && lat >= b[1] - m && lat <= b[3] + m
}

export function isKnownState(code, regions) {
  if (!code || code === 'DC' || !STATES[code]) return false
  return !regions || regions.some((r) => r.code === code)
}

// ------------------------------------------------------------------ toolbar
export function Toolbar({ route }) {
  const o = useOverload()
  const { site, mw, result, extraSites, loadFactor, trip, upgrades, firm, resetAll, cascade, mapTool, grid } = o
  const regionName = STATES[route.code]?.name || grid?.meta?.region_name || route.code
  const area = site && result?.sub_name ? result.sub_area || areaName(result.sub_name) : null
  const sentence = scenarioSentence({ regionName, site, mw, area, extraSites, loadFactor, trip, upgrades, firm })
  const busy = !!(site || extraSites.length || trip.length || loadFactor !== 1 || Object.keys(upgrades).length || cascade || mapTool || firm)
  return (
    <div className="nx-tool" role="region" aria-label="Scenario">
      <div className="nx-tool__title">
        <h1 className={sentence ? 'nx-q' : 'nx-q nx-q--empty'}>{sentence || `What if a data center plugs in somewhere in ${regionName}?`}</h1>
        <Status />
      </div>
      <div className="nx-tool__actions">
        {hasModule('briefing/ReviewStage') && cascade?.steps.length > 0 && (
          <Button variant="secondary" onClick={() => setStage(true)}>
            Review what happened
          </Button>
        )}
        <Optional from="library/SaveButton" loading={null} />
        <Optional from="library/ShareButton" loading={null} />
        <Button variant="secondary" onClick={resetAll} disabled={!busy}>
          Start over
        </Button>
      </div>
    </div>
  )
}

function Status() {
  const { result, solving, cascade, cascading, step, playing, whatifError, site, extraSites, trip, loadFactor } = useOverload()
  const hasCase = !!(site || extraSites.length || trip.length || loadFactor !== 1)
  let tone = 'idle'
  let text = 'Nothing added yet'
  if (cascading) [tone, text] = ['busy', 'Running the cascade…']
  else if (cascade) {
    const n = cascade.steps.length
    if (playing || (step > 0 && step < n)) [tone, text] = ['bad', `Step ${step} of ${n}`]
    else if (step >= n) {
      const v = afterRun(cascade)
      ;[tone, text] = [v.tone, v.label]
    } else [tone, text] = ['warn', `Ready to replay: ${n} ${n === 1 ? 'step' : 'steps'}`]
  } else if (hasCase) {
    const v = beforeRun({ result, solving: solving && !result ? 'first' : false, hasCase, whatifError })
    ;[tone, text] = [v.tone, v.label]
  }
  return (
    <span className={`nx-status nx-status--${tone}`} role="status" aria-live="polite">
      <span className="nx-status__dot" aria-hidden="true" />
      {text}
    </span>
  )
}

// ------------------------------------------------------------------ recipe sidebar
const SECTIONS = [
  { id: 'campus', title: 'Data center' },
  { id: 'hurricane', title: 'Storm' },
  { id: 'boom', title: 'More campuses' },
  { id: 'planner', title: 'Plan campuses', module: 'planner/PlannerPanel' },
]

export function RecipeSidebar({ route }) {
  const o = useOverload()
  const { mode, setMode, regions } = o
  const code = route.code
  const known = isKnownState(code, regions)
  if (!known) return <NoModel code={code} />
  const floridaOnly = code !== 'FL'
  const sections = SECTIONS.filter((s) => !s.module || hasModule(s.module))
  return (
    <div className="nx-recipe">
      <div className="nx-pad nx-recipe__head">
        <StatePicker code={code} />
        <RealCampusNote route={route} />
      </div>
      <Section id="campus" title="Data center" summary={<CampusSummary />} open={mode === 'campus' || !sections.some((s) => s.id === mode)} onOpen={() => setMode('campus')}>
        <CampusEditor code={code} />
      </Section>
      <div className="nx-sec nx-sec--fixed">
        <div className="nx-sec__head nx-sec__head--static">
          <span className="nx-sec__title">Time of day</span>
          <span className="nx-sec__sum">{timeSummary(o.loadFactor)}</span>
        </div>
        <div className="nx-sec__body nx-heat">
          <HeatClock />
        </div>
      </div>
      {sections
        .filter((s) => s.id !== 'campus')
        .map((s) => {
          const off = floridaOnly && FL_ONLY.has(s.id)
          return (
            <Section
              key={s.id}
              id={s.id}
              title={s.title}
              summary={off ? 'Florida only for now' : <Summary id={s.id} />}
              open={mode === s.id && !off}
              disabled={off}
              onOpen={() => setMode(s.id)}
            >
              {s.id === 'hurricane' && <HurricanePanel />}
              {s.id === 'boom' && <BoomPanel />}
              {s.id === 'planner' && <Optional from="planner/PlannerPanel" fallback={<p className="nx-pad muted">The planner is coming online.</p>} />}
            </Section>
          )
        })}
      {floridaOnly && (
        <p className="nx-pad nx-note">Storm tracks and the AI-boom preset are drawn for Florida&apos;s model for now; every other part of the scenario works in {STATES[code]?.name}.</p>
      )}
    </div>
  )
}

// #/next/state/<code> for a place with no grid model (Alaska, D.C., a typo): say so, offer the rest.
const OUTSIDE_48 = { AK: 'Alaska', HI: 'Hawaii', PR: 'Puerto Rico' }
export function NoModel({ code }) {
  const name = STATES[code]?.name || OUTSIDE_48[code]
  return (
    <div className="nx-pad stack nx-nomodel">
      <h1 className="nx-h2">{name ? `No grid model for ${name}` : `No state called “${code}”`}</h1>
      <p className="muted">The synthetic grid models cover the lower 48 states. Pick one to open its model.</p>
      <StatePicker code={null} />
      <a className="nx-link" href={href()}>
        Back to the map of America
      </a>
    </div>
  )
}

function timeSummary(lf) {
  const p = presetFor(lf)
  if (!p) return `${Math.round(lf * 100)} % of peak`
  return p.id === 'afternoon' ? '4 PM · summer peak' : p.id === 'wave' ? 'Heat wave' : p.label
}

function Section({ id, title, summary, open, disabled, onOpen, children }) {
  const bodyId = useId()
  return (
    <section className={`nx-sec${open ? ' nx-sec--open' : ''}${disabled ? ' nx-sec--off' : ''}`} data-sec={id}>
      <button type="button" className="nx-sec__head" aria-expanded={open} aria-controls={bodyId} onClick={onOpen} disabled={disabled}>
        <span className="nx-sec__title">{title}</span>
        <span className="nx-sec__sum">{summary}</span>
        <span className="nx-sec__chev" aria-hidden="true" />
      </button>
      {open && (
        <div className="nx-sec__body" id={bodyId}>
          {children}
        </div>
      )}
    </section>
  )
}

function Summary({ id }) {
  const { trip, extraSites } = useOverload()
  if (id === 'planner') return 'Fit MW without a blackout'
  if (id === 'hurricane') return trip.length ? `${fmt(trip.length)} lines knocked out` : 'None'
  if (id === 'boom') {
    if (!extraSites.length) return 'None'
    const total = extraSites.reduce((a, s) => a + (Number(s.mw) || 0), 0)
    return `${extraSites.length} · ${fmt(total)} MW`
  }
  return ''
}

function CampusSummary() {
  const { site, mw, result, firm } = useOverload()
  if (!site) return 'None yet'
  const area = result?.sub_name ? result.sub_area || areaName(result.sub_name) : null
  return `${fmt(mw)} MW${area ? ` · ${area}` : ''} · ${firm ? 'firm' : 'flexible'}`
}

function StatePicker({ code }) {
  const { regions } = useOverload()
  const list = useMemo(() => {
    const src = regions?.length ? regions.map((r) => ({ code: r.code, name: r.name })) : Object.entries(STATES).filter(([c]) => c !== 'DC').map(([c, s]) => ({ code: c, name: s.name }))
    return src.sort((a, b) => a.name.localeCompare(b.name))
  }, [regions])
  return (
    <Field
      as="select"
      label="State"
      value={code || ''}
      onChange={(e) => e.target.value && go(`/state/${e.target.value}`)}
      hint={code ? 'Each state runs on its own synthetic grid model.' : undefined}
    >
      {!code && <option value="">Choose a state</option>}
      {list.map((r) => (
        <option key={r.code} value={r.code}>
          {r.name}
        </option>
      ))}
    </Field>
  )
}

// When the campus on the map is a real, catalogued proposal: its sourced facts and the framing.
function RealCampusNote({ route }) {
  const { site } = useOverload()
  const { entries } = useCatalog()
  const id = route.query.dc
  const e = id ? entries?.find((x) => x.id === id) : null
  if (!e || !near(site, e)) return null
  const capped = e.mw > MODEL_MW_MAX
  return (
    <div className="nx-real">
      <p className="nx-real__k">Testing a reported proposal</p>
      <a className="nx-real__name" href={href(`/dc/${encodeURIComponent(e.id)}`)}>
        {e.name}
      </a>
      <p className="nx-real__meta">
        {placeText(e)} · {e.mw ? `${fmt(e.mw)} MW reported` : 'size not reported'} · {statusText(e.status)}
      </p>
      {capped && <p className="nx-real__meta">Tested at the model&apos;s limit of {fmt(MODEL_MW_MAX)} MW.</p>}
      <p className="nx-real__fine">
        A campus of this reported size at this location, tested on a synthetic grid model: not a prediction about the real project or the real utility.
        {e.sources[0] && (
          <>
            {' '}
            <a href={e.sources[0].url} target="_blank" rel="noreferrer">
              Source
            </a>
          </>
        )}
      </p>
    </div>
  )
}

function CampusEditor({ code }) {
  const o = useOverload()
  const { site, mw, setMw, result, solving, whatifError, subName, firm, setFirm, clearSite, peoplePerMw } = o
  const max = mw > 2000 ? Math.min(MODEL_MW_MAX, Math.ceil(mw / 1000) * 1000) : 2000 // the slider stretches to a bigger campus
  const main = result?.sites?.[0]
  return (
    <div className="stack nx-pad nx-campus">
      {!site ? (
        <div className="dc-chip" draggable onDragStart={(e) => e.dataTransfer.setData('text/plain', 'data-center')}>
          <span className="dc-chip__mw">{fmt(mw)} MW</span>
          <span className="dc-chip__hint">Click anywhere on the map to plug in an AI campus of this size, or drag this card there.</span>
        </div>
      ) : null}
      <Field
        label={`Size: ${fmt(mw)} MW`}
        type="range"
        min={100}
        max={max}
        step={50}
        value={mw}
        onChange={(e) => setMw(Number(e.target.value))}
        hint={peoplePerMw ? `About as much power as ${fmt(mw * peoplePerMw)} people use (estimate)` : undefined}
      />
      <ExactSize mw={mw} setMw={setMw} />
      <ServiceSwitch firm={firm} setFirm={setFirm} />
      <ErrorBanner error={whatifError} />
      {site && result && main && (
        <div className="nx-conn">
          <p>
            Connected at <strong>{result.sub_area || areaName(result.sub_name)}</strong>{' '}
            <span className="muted">
              ({result.sub_name}, {fmt(result.kv)} kV)
            </span>
            {solving && <span className="muted"> · updating…</span>}
          </p>
          <p>
            Room here: <strong>{fmt(main.headroom_mw)} MW</strong> before the first line overloads.
          </p>
          {result.overloaded.length > 0 ? (
            <>
              <p className="verdict verdict--bad">{overLimitText(result.overloaded)}</p>
              <ul className="over-list">
                {result.overloaded.slice(0, 3).map((b) => (
                  <li key={b.id}>
                    <span>{b.from === b.to ? `${subName(b.from)} transformer` : `${subName(b.from)} to ${subName(b.to)}`}</span>
                    <Badge tone="warn">{b.pct.toFixed(0)} %</Badge>
                  </li>
                ))}
              </ul>
              {result.overloaded.length > 3 && <p className="muted">and {result.overloaded.length - 3} more</p>}
            </>
          ) : (
            <p className="verdict verdict--ok">No line over its limit.</p>
          )}
        </div>
      )}
      {site && !result && !whatifError && <p className="muted">Solving the grid…</p>}
      <details className="nx-why">
        <summary>Why doesn&apos;t a bigger campus always black out more people?</summary>
        <p>
          Up to a site&apos;s room, nothing overloads. Past it, the lines that feed the campus are usually the first to trip, and that cuts the campus off.
          So on flexible service a bigger campus often blacks out the same people: at Fort Myers, 800 MW and 2,000 MW both end with about 780,000
          people without power (estimate). On firm service the operator holds those lines and cuts other customers instead, so size matters: 800 MW there
          leaves about 135,000 people dark, 1,500 MW about 675,000.
        </p>
      </details>
      <HeadroomToggle />
      {site && (
        <div className="row">
          <Button variant="danger" onClick={clearSite}>
            Remove this data center
          </Button>
        </div>
      )}
      <StateCampuses code={code} />
    </div>
  )
}

// Past the slider: any size from 1 MW to the model's 50 GW, typed or picked.
const QUICK = [5000, 20000, 50000]
function ExactSize({ mw, setMw }) {
  const id = useId()
  const [draft, setDraft] = useState('')
  const [error, setError] = useState(null)
  const apply = (v) => {
    setError(null)
    setDraft(String(v))
    setMw(v)
  }
  return (
    <details className="nx-exact">
      <summary>Exact size, up to {fmt(MODEL_MW_MAX / 1000)} GW</summary>
      <form
        className="nx-exact__form"
        noValidate
        onSubmit={(e) => {
          e.preventDefault()
          const v = Number(draft)
          if (!draft.trim() || !Number.isFinite(v) || v < 1 || v > MODEL_MW_MAX) return setError(`Enter a size from 1 to ${fmt(MODEL_MW_MAX)} MW.`)
          apply(Math.round(v))
        }}
      >
        <div className="nx-exact__row">
          <Field
            id={id}
            label="Size in MW"
            type="number"
            inputMode="numeric"
            min={1}
            max={MODEL_MW_MAX}
            value={draft}
            placeholder={fmt(mw)}
            onChange={(e) => {
              setDraft(e.target.value)
              setError(null)
            }}
            aria-invalid={error ? true : undefined}
          />
          <Button type="submit" variant="secondary">
            Set
          </Button>
        </div>
        {error && (
          <p className="nx-exact__err" role="alert">
            {error}
          </p>
        )}
        <div className="nx-exact__quick" role="group" aria-label="Quick sizes">
          {QUICK.map((v) => (
            <Button key={v} variant="secondary" aria-pressed={mw === v} onClick={() => apply(v)}>
              {fmt(v / 1000)} GW
            </Button>
          ))}
        </div>
      </form>
    </details>
  )
}

function ServiceSwitch({ firm, setFirm }) {
  const id = useId()
  return (
    <fieldset className="nx-seg" aria-describedby={`${id}-hint`}>
      <legend>Service</legend>
      <div className="nx-seg__opts">
        <label className={!firm ? 'nx-seg__on' : undefined}>
          <input type="radio" name={`${id}-svc`} checked={!firm} onChange={() => setFirm(false)} />
          <span>Flexible</span>
        </label>
        <label className={firm ? 'nx-seg__on' : undefined}>
          <input type="radio" name={`${id}-svc`} checked={firm} onChange={() => setFirm(true)} />
          <span>Firm</span>
        </label>
      </div>
      <p className="hint nx-seg__hint" id={`${id}-hint`}>
        {firm
          ? 'Firm: the campus is kept on. The lines that would cut it off are held, and other customers lose power instead.'
          : "Flexible: the campus can be cut off when its lines overload (Texas's SB 6, 2025, lets the grid operator curtail large loads in emergencies)."}
      </p>
    </fieldset>
  )
}

// Real, announced campuses in this state (from the catalog): one click tests one at its reported size.
function StateCampuses({ code }) {
  const { entries, status } = useCatalog()
  const { site, loadFactor, firm } = useOverload()
  const list = (entries || []).filter((e) => e.state === code && e.mw).sort((a, b) => b.mw - a.mw)
  const keep = { t: loadFactor !== 1 ? loadFactor : undefined, firm: firm ? 1 : undefined } // the time and service on screen stay
  if (!list.length) return status === 'loading' ? <p className="muted nx-small">Loading real data centers…</p> : null
  // open until a campus is on the map, so the recipe's other ingredients stay in view
  return (
    <details className="stack nx-state-dcs" key={site ? 'placed' : 'empty'} open={!site}>
      <summary className="panel-h">
        Reported data centers in {STATES[code]?.name || code} ({fmt(list.length)})
      </summary>
      <ul className="real__list">
        {list.slice(0, 8).map((e) => (
          <li key={e.id}>
            <a className="real__pick" href={href(`/state/${code}`, { ...testQuery(e), ...keep })}>
              <span className="real__name">{e.name}</span>
              <span className="real__meta">
                {e.city || placeText(e)} · {fmt(e.mw)} MW reported · {statusText(e.status)}
              </span>
            </a>
            <a className="real__src" href={href(`/dc/${encodeURIComponent(e.id)}`)} aria-label={`Facts and sources for ${e.name}`}>
              Facts
            </a>
          </li>
        ))}
      </ul>
      {list.length > 8 && <p className="muted nx-small">and {list.length - 8} more on the map of America</p>}
      {status === 'fallback' && <p className="muted nx-small">The national catalog is still loading; these are Florida&apos;s sourced proposals.</p>}
    </details>
  )
}

// ------------------------------------------------------------------ inspector
export function Inspector({ route }) {
  const panel = usePanel()
  const { regions } = useOverload()
  if (!isKnownState(route.code, regions)) return null
  return (
    <div className="nx-insp">
      <div className="nx-tabs" role="tablist" aria-label="Inspector">
        <button type="button" role="tab" aria-selected={panel === 'result'} className="nx-tab" onClick={() => setPanel('result')}>
          Result
        </button>
        <button type="button" role="tab" aria-selected={panel === 'area'} className="nx-tab" onClick={() => setPanel('area')}>
          Is my area at risk?
        </button>
      </div>
      <div role="tabpanel" className="nx-insp__body">
        {panel === 'result' ? <ResultPanel /> : <AreaPanel code={route.code} />}
      </div>
    </div>
  )
}

// the areas track's finder (search, card, stress tests); ours (lights, room, the case on screen) until it lands
function AreaPanel({ code }) {
  return (
    <div className="nx-areapanel">
      <Optional from="town/AreaFinder" title="Is my area at risk?" fallback={<AreaFinder code={code} />} loading={<AreaFinder code={code} />} />
    </div>
  )
}

function ResultPanel() {
  const o = useOverload()
  const { result, solving, whatifError, cascade, step, site, extraSites, trip, loadFactor, setFirm, firm, cascading, startCascade } = o
  const hasCase = !!(site || extraSites.length || trip.length || loadFactor !== 1)
  const n = cascade?.steps.length || 0
  const done = !!cascade && step >= n
  const v = done ? afterRun(cascade) : beforeRun({ result, solving: solving && !result ? 'first' : false, hasCase, whatifError })
  const room = result?.sites?.[0]?.headroom_mw
  const why = done ? whyNumber(cascade, Number(room)) : null
  // switch the service and replay the same case at once (the switch alone clears the cascade)
  const rerun = (on) => {
    setFirm(on)
    startCascade({ firm: on })
  }
  return (
    <div className="stack nx-pad nx-result">
      <section className={`nx-verdict nx-verdict--${v.tone}`} aria-live="polite">
        <p className="nx-verdict__k">{done ? 'What happened' : cascade ? 'Replaying' : 'Before you run'}</p>
        <p className="nx-verdict__label">{cascade && !done ? `Step ${step} of ${n}` : v.label}</p>
        <p className="nx-verdict__s">{cascade && !done ? 'Lines trip one by one as the flow reroutes.' : v.sentence}</p>
      </section>
      <PeopleCounter />
      {why && (
        <section className={`nx-why-card nx-why-card--${why.kind}`}>
          <h3 className="nx-h3">{why.title}</h3>
          <p>{why.sentence}</p>
          {why.action === 'firm' && !firm && (
            <Button variant="secondary" onClick={() => rerun(true)} disabled={cascading}>
              Run it on firm service
            </Button>
          )}
          {why.action === 'flexible' && firm && (
            <Button variant="secondary" onClick={() => rerun(false)} disabled={cascading}>
              Run it on flexible service
            </Button>
          )}
        </section>
      )}
      {!cascade && hasCase && result && (
        <p className="muted nx-small">Run the cascade (below the map) to see which lines trip and who loses power.</p>
      )}
      {/* before a run the forecast leads ("know before you run"); after it, who was hit */}
      {!done && <Optional key="forecast" from="forecast/ForecastCard" loading={null} />}
      {/* while it plays: who the blast reaches; once it ends: where the lights are out, adding up to the counter */}
      {done ? <EndTowns /> : <TownsFeed />}
      <Optional from="hospitals/HospitalsList" loading={null} />
      {done && <Optional key="forecast-after" from="forecast/ForecastCard" title="Size and service" loading={null} />}
      <Optional from="cost/CostCard" loading={null} />
      {!hasCase && <MapLegend />}
    </div>
  )
}

// After the cascade: the areas still without power, from the engine's final lost load per substation
// (cascade.affected) × the state's people per MW — the same estimate as the counter's final number,
// so the rows add up to it. (During the replay the towns feed tells who the blast reached.)
const END_ROWS = 8
function EndTowns() {
  const { view, cascade, subById, peoplePerMw, focus, subPos } = useOverload()
  const headId = useId()
  const areas = useMemo(() => {
    const by = new Map()
    view?.affected?.forEach((mw, id) => {
      const s = subById.get(id)
      if (!s || !(mw > 0)) return
      const name = s.area || townOf(s.name)
      const a = by.get(name) || { name, mw: 0, subs: [] }
      a.mw += mw
      a.subs.push(id)
      by.set(name, a)
    })
    return [...by.values()].map((a) => ({ ...a, people: a.mw * (peoplePerMw || 0) })).sort((a, b) => b.people - a.people)
  }, [view, subById, peoplePerMw])
  if (!areas.length || !peoplePerMw) return null
  const top = areas[0].people || 1
  const rest = areas.slice(END_ROWS)
  const restPeople = rest.reduce((t, a) => t + a.people, 0)
  return (
    <section className="towns nx-endtowns" aria-labelledby={headId}>
      <div className="towns__head">
        <h2 id={headId} className="panel-h">
          Without power at the end (estimates)
        </h2>
        <span className="towns__count">{`${fmt(areas.length)} ${areas.length === 1 ? 'area' : 'areas'}`}</span>
      </div>
      <ol className="towns__list">
        {areas.slice(0, END_ROWS).map((a) => (
          <li key={a.name} className="towns__item">
            <button
              type="button"
              className="towns__row"
              onClick={() => focus(a.subs.map(subPos))}
              aria-label={`${a.name}: about ${fmt(about(a.people))} people without power (estimate). Show it on the map.`}
            >
              <span className="towns__name">{a.name}</span>
              <span className="towns__homes">
                {fmt(about(a.people))} <span className="towns__unit">people</span>
              </span>
              <span className="towns__bar" style={{ transform: `scaleX(${Math.max(a.people / top, 0.02)})` }} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ol>
      {rest.length > 0 && (
        <p className="towns__more">
          and {fmt(rest.length)} more {rest.length === 1 ? 'area' : 'areas'} (about {fmt(about(restPeople))} people)
        </p>
      )}
      <p className="muted nx-small">
        Together about {fmt(cascade?.people ?? 0)} people: each area&apos;s lost load × {fmt(Math.round(peoplePerMw))} people per MW.
      </p>
    </section>
  )
}
// three significant figures: an estimate shouldn't read more precise than it is
const about = (n) => {
  const v = Number(n) || 0
  if (v < 1000) return Math.round(v)
  const p = 10 ** (Math.floor(Math.log10(v)) - 2)
  return Math.round(v / p) * p
}

// The loud number: people without power (an estimate). While the cascade plays it climbs steadily to
// the worst moment and never counts down; paused or scrubbed it shows the exact step.
function PeopleCounter() {
  const { view, cascade, step, playing, population } = useOverload()
  const n = cascade?.steps.length || 0
  const peak = view?.peopleMax || 0
  const worst = n ? Math.max(...cascade.steps.map((s) => s.people ?? 0)) : 0
  const target = playing && n ? Math.max(peak, Math.round((worst * Math.min(step + 1, n)) / n)) : peak
  const shown = Math.max(0, useCountUp(Math.max(0, target), playing ? stepMsFor(n) : 500, { linear: playing }))
  const done = cascade && n > 0 && step >= n
  const final = done ? cascade.people ?? cascade.steps[n - 1].people ?? 0 : null
  return (
    <section className="counter nx-counter">
      <span className={`counter__n${shown > 0 ? ' counter__n--dark' : ''}`}>{fmt(shown)}</span>
      <span className="counter__label">
        People without power (estimate){final !== null && final < peak ? ` · at its worst; ${fmt(final)} at the end` : ''}
      </span>
      {population ? (
        <span className="nx-counter__basis">Lost load&apos;s share of the state model&apos;s load × {fmt(population)} residents (Census 2024).</span>
      ) : null}
    </section>
  )
}

// ------------------------------------------------------------------ replay bar
// The one place the cascade is run, played and scrubbed. Each step is described in words: a storm
// knocking lines out, a line tripping, or (firm service) the operator cutting customers.
export function ReplayBar() {
  const o = useOverload()
  const { cascade, cascading, cascadeError, step, setStep, playing, setPlaying, startCascade, result, subName, branchById, grid, site } = o
  const ready = !!result
  const n = cascade?.steps.length || 0
  const cur = cascade && step > 0 ? cascade.steps[step - 1] : null
  const where = grid?.meta?.region_name || 'the map'

  const lineName = (id) => {
    const b = branchById.get(id)
    if (!b) return `line ${id}`
    return b.from_sub === b.to_sub ? `${subName(b.from_sub)} transformer` : `${subName(b.from_sub)} to ${subName(b.to_sub)}`
  }
  function describe(st) {
    if (!st) return 'Before anything trips.'
    if (st.action === 'shed') {
      return `The operator cut ${fmt(st.shed_mw || 0)} MW of customers${st.held_line != null ? ` to hold ${lineName(st.held_line)}` : ''}.`
    }
    const names = st.tripped.slice(0, 2).map(lineName)
    const more = st.tripped.length > 2 ? ` and ${st.tripped.length - 2} more` : ''
    if (!names.length) return 'The flow reroutes.'
    return `${st.action === 'storm' || st.n === 0 ? 'Knocked out' : 'Tripped'}: ${names.join(', ')}${more}.`
  }

  return (
    <footer className="nx-replay" aria-label="Cascade replay">
      <div className="timeline">
        <div className="timeline__cta">
          <Button onClick={() => startCascade()} busy={cascading} disabled={!ready}>
            {cascading ? 'Running…' : cascade ? 'Run it again' : 'Run the cascade'}
          </Button>
          {cascade && n > 0 && (
            <Button
              variant="secondary"
              onClick={() => {
                if (step >= n) setStep(0)
                setPlaying(!playing || step >= n)
              }}
              aria-label={playing ? 'Pause the cascade' : 'Play the cascade'}
            >
              {playing ? 'Pause' : step >= n ? 'Replay' : 'Play'}
            </Button>
          )}
        </div>
        <div className="timeline__track">
          {!cascade ? (
            <p className="timeline__hint">
              {ready
                ? 'The cascade trips the most overloaded line, re-solves the flow, and repeats until the grid settles or splits.'
                : site
                  ? 'Solving the grid…'
                  : `Click ${where} to plug in a data center, then run the cascade.`}
            </p>
          ) : n === 0 ? (
            <p className="timeline__hint">Nothing to cascade: no line is over its limit.</p>
          ) : (
            <>
              <div className="flowbar">
                <div className="flowbar__fill" style={{ width: `${(step / n) * 100}%`, transitionDuration: playing ? `${stepMsFor(n)}ms` : '0ms' }} />
                <ol className="flowbar__marks" aria-label="Cascade steps">
                  {Array.from({ length: n + 1 }, (_, i) => (
                    <li key={i} style={{ left: `${(i / n) * 100}%` }}>
                      <button
                        type="button"
                        className={`mark${i === step ? ' mark--now' : ''}${i < step ? ' mark--past' : ''}`}
                        onClick={() => setStep(i)}
                        aria-label={i === 0 ? 'Before the cascade' : `Step ${i}`}
                        aria-current={i === step ? 'step' : undefined}
                      />
                    </li>
                  ))}
                </ol>
              </div>
              <input className="timeline__range" type="range" min={0} max={n} step={1} value={step} onChange={(e) => setStep(Number(e.target.value))} aria-label="Cascade step" />
              <p className="timeline__now" aria-live="polite">
                <strong>{step === 0 ? 'Step 0' : `Step ${step} of ${n}`}</strong> {describe(cur)}
                {cur && cur.people > 0 && <span className="muted"> · {fmt(cur.people)} people without power (estimate)</span>}
              </p>
            </>
          )}
          <ErrorBanner error={cascadeError} />
        </div>
      </div>
    </footer>
  )
}
