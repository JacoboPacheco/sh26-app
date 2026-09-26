// FEATURE: "Who goes dark first?" (CLAUDE.md -> Decisions; notes BIG-IDEAS-SAT-1655 #1). Right after the cascade, in
// the results column (shell/ImpactPanel.jsx): the SAME campus under three service rules, each computed by the engine
// (backend/service_rules.py): nobody planned (the plain cascade), keep the campus on (firm: the operator cuts other
// customers instead), the campus steps down first (to the room this site has before the first overload, verified:
// nothing trips there). A three-way switch; the number for the rule on screen snaps with the counter's one short hard
// jolt; the map crossfades to that rule's end state (DarkFirstLayer.jsx). One sourced line above the switch, the
// sources in a fold, and the question to take to the meeting. Estimates on a synthetic grid model.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api'
import floridaFive from '../../data/florida_five.json'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { cascadePeople } from '../cost/figures'
import { money } from '../cost/money'
import { plantsOut } from '../fix/flipCase'
import { byIntensity, leapIntensity } from '../impact/intensity'
import { roundPeople, useReducedMotion } from '../impact/towns'
import { isDroppedAt, usePickedProposal } from '../proposals/proposalStore'
import { RULES, allCut, baseRule, openFirmQuestion, pickRule, ruleLabel, rulesBody, shownRule, useDarkFirst, useServiceRules, useShowing, whenText } from './rulesStore'
import './darkfirst.css'

const plural = (n, one, many) => `${fmt(n)} ${n === 1 ? one : many}`

export default function DarkFirst() {
  const O = useOverload()
  const { cascade, caseBody } = O
  const body = useMemo(() => (plantsOut(cascade) ? null : rulesBody(caseBody, cascade)), [caseBody, cascade])
  const { data, status, retry, key } = useServiceRules(body)
  const s = useDarkFirst()
  useShowing(!!data)
  if (!body) return null
  const base = baseRule(cascade)
  const shown = shownRule(s, key, base)
  return (
    <section className="df" aria-labelledby="df-h">
      <h2 className="panel-h" id="df-h">
        Who goes dark first?
      </h2>
      <Lead data={data} />
      {status === 'error' ? (
        <p className="df__wait" role="alert">
          The three rules could not be measured.{' '}
          <button type="button" className="df__again" onClick={retry}>
            Try again
          </button>
        </p>
      ) : !data ? (
        <p className="df__wait" role="status">
          Running the same campus under three rules…
        </p>
      ) : (
        <Rules data={data} shown={shown} ruleKey={key} />
      )}
    </section>
  )
}

// One sourced line: who is cut first is written in the contract; a rule like Texas's (never "Florida's rule").
function Lead({ data }) {
  const sb6 = data?.sources?.find((x) => x.key === 'texas_sb6')
  return (
    <p className="df__lead">
      When the grid runs short, one sentence in the service contract decides who is cut first. A rule like{' '}
      {sb6 ? (
        <a href={sb6.url} target="_blank" rel="noopener noreferrer" title={sb6.name}>
          Texas&apos;s SB 6
        </a>
      ) : (
        "Texas's SB 6"
      )}{' '}
      lets utilities disconnect large loads during emergency load shedding.
    </p>
  )
}

function Rules({ data, shown, ruleKey }) {
  const reduced = useReducedMotion()
  const byKey = useMemo(() => Object.fromEntries(data.rules.map((r) => [r.key, r])), [data])
  const r = byKey[shown] || data.rules[0]
  const btns = useRef([])
  const figRef = useRef(null)
  const prev = useRef(null)
  const most = Math.max(...data.rules.map((x) => x.people_dark), 1)

  // the number snaps to the rule picked, with the counter's one short hard jolt (sized by how far it moved)
  useLayoutEffect(() => {
    const was = prev.current
    prev.current = r
    const el = figRef.current
    if (!was || was.key === r.key || reduced || !el?.animate) return
    const delta = Math.abs(r.people_dark - was.people_dark)
    if (!(delta > 0)) return
    const a = byIntensity(leapIntensity(delta, most), 2, 8)
    el.animate(
      [
        { transform: `translate3d(${(-a * 0.3).toFixed(2)}px, ${a.toFixed(2)}px, 0)` },
        { transform: `translate3d(${(a * 0.12).toFixed(2)}px, ${(-a * 0.22).toFixed(2)}px, 0)`, offset: 0.45 },
        { transform: 'translate3d(0, 0, 0)' },
      ],
      { duration: 130, easing: 'ease-out' },
    )
  }, [r, reduced, most])

  const pick = (i) => {
    const k = RULES[(i + RULES.length) % RULES.length].key
    pickRule(ruleKey, k)
    btns.current[(i + RULES.length) % RULES.length]?.focus()
  }
  const onKey = (e, i) => {
    const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key]
    if (step) {
      e.preventDefault()
      pick(i + step)
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault()
      pick(e.key === 'Home' ? 0 : RULES.length - 1)
    }
  }

  const lens = data.lens?.people || 0
  const calm = r.people_dark === 0
  const named = !!useProposalHere()
  return (
    <>
      <div className="df__switch" role="radiogroup" aria-labelledby="df-h">
        {RULES.map((rule, i) => {
          const x = byKey[rule.key]
          const on = rule.key === r.key
          const zero = x.people_dark === 0
          const label = ruleLabel(x, rule.key)
          return (
            <button
              key={rule.key}
              ref={(el) => (btns.current[i] = el)}
              type="button"
              role="radio"
              aria-checked={on}
              tabIndex={on ? 0 : -1}
              className={`df__opt${on ? ' df__opt--on' : ''}${zero ? ' df__opt--zero' : ''}`}
              onClick={() => pick(i)}
              onKeyDown={(e) => onKey(e, i)}
              aria-label={`${label}: ${fmt(x.people_dark)} ${allCut(x) ? 'people cut' : 'people lose power'} (estimate)`}
            >
              <span className="df__opt-l">{label}</span>
              <span className="df__opt-n">{fmt(x.people_dark)}</span>
            </button>
          )
        })}
      </div>

      <div className="df__res" aria-live="polite">
        <div className="df__figwrap">
          <span ref={figRef} className={`df__fig${calm ? ' df__fig--ok' : ''}`}>
            {fmt(r.people_dark)}
          </span>
          <span className="df__k">
            {allCut(r) ? `people cut to keep ${(data.sites?.length || 1) > 1 ? 'them' : 'it'} on` : 'people lose power'}{' '}
            <span className="df__est">(estimate)</span>
          </span>
        </div>
        <Bridge data={data} byKey={byKey} />
        <p className="df__line">
          <Why r={r} data={data} flex={byKey.flexible} />
        </p>
        <p className="df__meta">
          {r.people_dark > 0 || r.lost_mw > 0 ? (
            <>
              Out for {r.outage_label} · <b>{money(r.cost_high)}</b> <span className="df__est">high end</span>
            </>
          ) : (
            <>No outage · no line trips</>
          )}
        </p>
        {r.key === 'step_down' && data.step_down.needed && !data.step_down.over_without && <Duke data={data} />}
      </div>

      {lens >= 100 && (
        <p className="df__lens">
          {/* a named proposal on the map: the comparison is the synthetic model's, never a claim about the real project */}
          {named ? 'On this synthetic model, one sentence' : 'One sentence'} in the contract was worth about <b>{fmt(roundPeople(lens))}</b> people.
        </p>
      )}
      <div className="df__foot">
        <Meeting />
        <Sources data={data} />
      </div>
    </>
  )
}

// What happens under the rule on screen, in plain words with the engine's numbers.
function Why({ r, data, flex }) {
  const full = data.mw
  const many = (data.sites?.length || 1) > 1
  const campus = many ? 'the campuses' : 'the campus'
  const Campus = many ? 'The campuses' : 'The campus'
  const own = many ? "the campuses' own" : "the campus's own"
  const when = whenText(data.load_factor, data.storm)
  const lostCampus = Math.max(0, full - r.campus_served_mw)
  if (r.key === 'flexible') {
    const trips = r.lines_tripped ? `${plural(r.lines_tripped, 'line trips', 'lines trip')} one after another` : 'The grid settles'
    if (r.campus_cut_off) return `${trips}; ${own} ${fmt(lostCampus)} MW ${many ? 'go' : 'goes'} dark too.`
    return `${trips}; ${campus} ${many ? 'stay' : 'stays'} on.`
  }
  if (r.key === 'firm') {
    if (r.firm_held === false && !(r.shed_mw > 0))
      return `The operator can't hold ${campus} on: ${own} connection trips and ${many ? 'they go' : 'it goes'} dark with the neighbors.`
    if (r.firm_held === false) return `The operator cuts ${fmt(r.shed_mw)} MW of other customers, and ${fmt(lostCampus)} MW of ${campus} still ${many ? 'go' : 'goes'} dark.`
    if (!(r.shed_mw > 0)) return `${Campus} ${many ? 'stay' : 'stays'} on; the operator never has to cut anyone for ${many ? 'them' : 'it'}.`
    if (!allCut(r)) {
      // some of the dark areas were cut for the campus, the rest went dark on their own (the storm, a line that tripped)
      const rest = data.storm ? 'lose power to the storm and the lines that trip' : 'lose power as lines trip'
      const part = r.people_cut > 0 ? ` (about ${fmt(roundPeople(r.people_cut))} people)` : ''
      return `${Campus} ${many ? 'keep' : 'keeps'} all ${fmt(full)} MW; the operator cuts ${fmt(r.shed_mw)} MW of other customers for ${many ? 'them' : 'it'}${part}, and the rest ${rest}.`
    }
    return `${Campus} ${many ? 'keep' : 'keeps'} all ${fmt(full)} MW; the operator cuts ${fmt(r.shed_mw)} MW of other customers instead.`
  }
  const sd = data.step_down
  if (sd.over_without) {
    // stepping down can't clear an outage the campus doesn't cause: it switches off, and the cascade runs without it
    // (the lines can then fail in another order, so this can even end worse than nobody planned: said so, never hidden)
    const worse = flex && r.people_dark > flex.people_dark ? ' With it off, the lines fail in a different order and more areas go dark.' : ''
    return `Even with ${campus} off, the grid is past its limits ${when}, so stepping down means switching off: it can't clear this outage.${worse}`
  }
  if (!sd.needed) return `${Campus} ${many ? 'fit' : 'fits'} at full size ${when}: nothing to step down.`
  const tail = r.people_dark > 0 ? `; the rest of the outage isn't ${campus}'s doing` : '; no line goes over its limit, nothing trips'
  return `${Campus} ${many ? 'keep' : 'keeps'} running at ${Math.round(sd.share_pct)} % of ${many ? 'their' : 'its'} size ${when}: ${fmt(sd.level_mw)} of ${fmt(full)} MW${tail}.`
}

// The counter above counts everyone hit along the way (people whose power ran through a failed line, each once); these
// three numbers count the areas that lose power, the same way for each rule. Where the two differ (Fort Meade: 253,022
// hit, 11,694 in the dark), one quiet line bridges them so neither reads as a mistake.
function Bridge({ data, byKey }) {
  const { cascade } = useOverload()
  const own = byKey[baseRule(cascade)] || data.rules[0]
  const hit = cascadePeople(cascade).hit
  if (!own || !(hit - own.people_dark >= Math.max(100, 0.02 * hit))) return null
  return (
    <p className="df__bridge">
      Counted by the areas that lose power, the same way for all three rules. The {fmt(hit)} above also counts people whose power ran through a failed
      line but stayed on.
    </p>
  )
}

// How often a campus would step down is not something this model can say: Duke's national estimate, as theirs.
function Duke({ data }) {
  const src = data.sources?.find((x) => x.key === 'duke_flex')
  if (!src) return null
  return (
    <p className="df__duke">
      How often? Duke University&apos;s national estimate: new load that can be curtailed for 0.25 % of its maximum uptime would see curtailment in about
      85 hours a year, mostly partial (half of it still running for 88 % of that time).{' '}
      <a href={src.url} target="_blank" rel="noopener noreferrer" title={src.name}>
        Their study
      </a>
      , not this model.
    </p>
  )
}

// The planned data center the campus on the map is (picked from "Planned data centers" and dropped at its reported
// place), or null: Florida's five from the bundled list, any other state's from its catalog list (GET
// /api/catalog/places, the same list the dropdown shows: nothing is computed), fetched only once a proposal is picked.
const placesByState = new Map() // state code -> Promise<entries>
function statePlaces(region) {
  if (!placesByState.has(region))
    placesByState.set(
      region,
      api(`/api/catalog/places?state=${encodeURIComponent(region)}`)
        .then((d) => d.entries || [])
        .catch(() => {
          placesByState.delete(region) // a later look may try again
          return []
        }),
    )
  return placesByState.get(region)
}
function useProposalHere() {
  const { site, region } = useOverload()
  const picked = usePickedProposal()
  const [list, setList] = useState(null) // {region, entries}
  const other = !!picked && !!region && region !== 'FL' && region !== 'US'
  useEffect(() => {
    if (!other) return undefined
    let live = true
    statePlaces(region).then((entries) => live && setList({ region, entries }))
    return () => {
      live = false
    }
  }, [other, region])
  if (!picked || !site) return null
  const pool = region === 'FL' ? floridaFive.entries : list?.region === region ? list.entries : []
  const entry = pool.find((e) => e.id === picked)
  return entry && Number.isFinite(entry.lat) && Number.isFinite(entry.lon) && isDroppedAt(site, entry) ? entry : null
}

function Meeting() {
  const { region, grid } = useOverload()
  const here = useProposalHere()
  // a proposal page (#/vote/<id>) exists for the researched catalog only: a Compute Atlas site (origin
  // "compute-atlas") links to the state's list instead of a page that isn't there
  const entry = here && here.origin !== 'compute-atlas' ? here : null
  if (entry)
    return (
      <a
        className="df__meet"
        href={`#/vote/${entry.id}`}
        onClick={(e) => {
          e.preventDefault()
          openFirmQuestion(entry.id)
        }}
      >
        Take this question to the meeting <span aria-hidden="true">→</span>
      </a>
    )
  const state = grid?.meta?.region_name || 'this state'
  return (
    <a className="df__meet" href={`#/vote?state=${encodeURIComponent(region)}`} title={`Proposed data centers in ${state}: "Firm or flexible service?" is on each one's list`}>
      Take this question to the meeting <span aria-hidden="true">→</span>
    </a>
  )
}

function Sources({ data }) {
  if (!data.sources?.length) return null
  return (
    <details className="df__src">
      <summary>Sources</summary>
      <ul>
        {data.sources.map((x) => (
          <li key={x.key}>
            {x.text}{' '}
            <a href={x.url} target="_blank" rel="noopener noreferrer">
              {x.name}
            </a>
          </li>
        ))}
        <li>The three numbers are the engine&apos;s, on a synthetic grid model: people in the areas that lose power (estimate).</li>
      </ul>
    </details>
  )
}
