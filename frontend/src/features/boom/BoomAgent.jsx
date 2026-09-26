// FEATURE: AI-boom mode — "Let the AI place them" (owned by the boom track).
// Contract: default export BoomAgent() — a section of BoomPanel. A goal ("place 3 GW of AI campuses
// in <state> without blacking anyone out", a size, how many sites, an optional preference), then the
// siting agent (backend/planner.py) works it live: Gemini picks one tool at a time, the grid engine
// runs it, the trace (planner/AgentTrace) shows each call and answer as it streams, the campuses drop
// on the map as the agent places them (agentSync.js, through useOverload().setExtraSites), and the
// engine's check of the finished plan closes it, with its label: Gemini engine-verified, or the plain
// built-in planner and why. Nothing runs until the button (LAZY).
import { useEffect, useId, useRef, useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Badge, Button, ErrorBanner, Field } from '../../ui'
import AiBadge from '../ai/AiBadge'
import HowAiIsUsed from '../ai/HowAiIsUsed'
import { peakPhrase } from '../heat/presets'
import AgentTrace from '../planner/AgentTrace'
import { fmtMw } from '../planner/plannerApi'
import { revealed, runPlan, usePlanner } from '../planner/plannerStore'
import { clearForAgent, showPlanVersion, useShownVersion } from './agentSync'
import { sizeLabel } from './boomData'

const TOTALS = [1000, 2000, 3000, 5000]
const SITES = [1, 2, 3, 4, 5, 6]
const SITE_MAX_MW = 5000 // the planner's biggest campus (backend/planner.py SITE_MAX_MW)
const PREF_MAX = 80
const REVEAL_MS = 950 // one step at a time, slow enough to watch each campus land
// the preference field's example, in the state on screen
const PREF_EXAMPLE = { FL: 'near Orlando, avoid Miami', TX: 'near Dallas, avoid Houston', GA: 'near Atlanta', CA: 'near Fresno, avoid Los Angeles', NY: 'avoid New York' }

// the form survives the panel closing (a mode switch)
let draft = { total: 3000, sites: 3, pref: '' }

// why the built-in planner made the plan when Gemini was asked (result.ai.status)
const FALLBACK_WHY = {
  slow: 'Gemini too slow',
  rejected: "Gemini's plan failed the engine check",
  out_of_calls: "Gemini's plan failed the engine check",
  supply: 'Gemini handed it over',
  not_configured: 'Gemini not configured',
  rate_limited: 'Gemini rate-limited',
  unavailable: 'Gemini unavailable',
}

export default function BoomAgent() {
  const o = useOverload()
  const p = usePlanner()
  const [form, setForm] = useState(draft)
  const hId = useId()
  const { region, grid, regions } = o
  const national = region === 'US'
  const stateName = grid?.meta?.region === region ? grid.meta.region_name : regions?.find((r) => r.code === region)?.name || region
  const mine = p.origin === 'boom'
  const running = mine && p.status === 'running'
  const busy = running || (mine && p.status === 'done' && !revealed(p))

  const update = (patch) => {
    draft = { ...form, ...patch }
    setForm(draft)
  }
  const pref = form.pref.trim().replace(/[.\s]+$/, '')
  const goal = `Place ${sizeLabel(form.total)} of AI campuses in ${stateName} without blacking anyone out${pref ? `, ${pref}` : ''}`
  const invalid = Math.ceil(form.total / SITE_MAX_MW) > form.sites ? `A site takes at most ${fmt(SITE_MAX_MW)} MW: allow more sites or pick less power.` : null
  const replaces = !!(o.site || o.extraSites.length || o.trip.length || Object.keys(o.upgrades).length)

  function start(fresh = false, total = form.total) {
    if (national || running) return
    if (Math.ceil(total / SITE_MAX_MW) > form.sites) return
    const g = `Place ${sizeLabel(total)} of AI campuses in ${stateName} without blacking anyone out${pref ? `, ${pref}` : ''}`
    clearForAgent(o)
    runPlan(
      { region, goal: g, total_mw: total, max_sites: form.sites, load_factor: o.loadFactor, firm: o.firm, ...(fresh ? { fresh: true } : {}) },
      { origin: 'boom', revealMs: REVEAL_MS },
    )
  }
  // after a plan that holds: the next size up, one click (the agent works a harder case)
  const more = TOTALS.find((t) => t > (p.request?.total_mw || form.total) && Math.ceil(t / SITE_MAX_MW) <= form.sites)
  const askMore = more
    ? () => {
        update({ total: more })
        start(false, more)
      }
    : null

  return (
    <section className="stack boom-agent" aria-labelledby={hId}>
      <div className="boom-agent__head">
        <h3 className="panel-h boom-agent__title" id={hId}>
          Let the AI place them
        </h3>
        <HowAiIsUsed surface="planner" className="boom-agent__how" />
      </div>
      <p className="boom-agent__lede">
        Gemini places the campuses one decision at a time, calling the grid engine as its tool. The engine runs every call and checks the finished plan.
      </p>
      {national ? (
        <p className="boom-agent__hint">Pick a state in the menu at the top left, then let the AI place campuses on its grid.</p>
      ) : (
        <>
          <p className="boom-agent__goal">
            <span className="boom-agent__goal-label">Goal</span>
            {goal}
          </p>
          <div className="boom-agent__pair">
            <Field as="select" label="Total" value={form.total} onChange={(e) => update({ total: Number(e.target.value) })} disabled={busy}>
              {TOTALS.map((t) => (
                <option key={t} value={t}>
                  {sizeLabel(t)}
                </option>
              ))}
            </Field>
            <Field as="select" label="Sites, at most" value={form.sites} onChange={(e) => update({ sites: Number(e.target.value) })} disabled={busy}>
              {SITES.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Field>
          </div>
          <Field
            label="Preference (optional)"
            value={form.pref}
            maxLength={PREF_MAX}
            placeholder={PREF_EXAMPLE[region] || 'near a town, or avoid one'}
            onChange={(e) => update({ pref: e.target.value })}
            disabled={busy}
          />
          {invalid && (
            <p className="boom-agent__invalid" role="alert">
              {invalid}
            </p>
          )}
          <Button onClick={() => start(false)} busy={busy} disabled={!!invalid || !grid}>
            {running ? 'Gemini is placing them…' : busy ? 'Showing the steps…' : 'Let the AI place them'}
          </Button>
          <p className="boom-agent__context">
            At {peakPhrase(o.loadFactor)} · {o.firm ? 'firm' : 'flexible'} campuses · synthetic grid model
            {replaces && !busy ? ' · replaces what is on the map' : ''}
          </p>
        </>
      )}

      {mine && p.status === 'error' && <ErrorBanner error={p.error} onRetry={() => start(false)} />}
      {mine && p.status !== 'idle' && (p.status === 'running' || p.steps.length > 0) && <AgentTrace p={p} />}
      {mine && revealed(p) && p.result && <Verdict p={p} onFresh={() => start(true, p.request?.total_mw || form.total)} onMore={askMore} more={more} />}
    </section>
  )
}

// who made the plan, with the shared AI label
function PlanBy({ r }) {
  if (r.by === 'gemini') {
    return (
      <AiBadge by="gemini" verified title="Gemini chose each step; the engine ran every step and re-checked the finished plan">
        {r.cached ? 'replay' : r.ai?.cached_calls && r.ai.cached_calls >= r.ai.calls ? 'cached decisions' : null}
      </AiBadge>
    )
  }
  if (r.fallback) {
    return (
      <AiBadge by="fallback" why={FALLBACK_WHY[r.ai?.status]} className="aib--wrap">
        built-in planner
      </AiBadge>
    )
  }
  return <AiBadge by="engine">built-in planner</AiBadge>
}

function Verdict({ p, onFresh, onMore, more }) {
  const o = useOverload()
  const shownVersion = useShownVersion()
  // the verdict closes the trace: bring it into view when it lands
  const top = useRef(null)
  useEffect(() => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    top.current?.scrollIntoView?.({ block: 'center', behavior: reduce ? 'auto' : 'smooth' })
  }, [p.runId])
  const r = p.result
  const v = r.verification
  const plan = r.plan
  const alt = r.without_upgrades
  if (!plan || !v) {
    return <p className="boom-agent__verdict boom-agent__verdict--bad">No plan: there is nowhere in {r.region_name} to place a campus.</p>
  }
  const n = plan.sites.length
  const ups = plan.upgrade_lines || []
  const steps = v.cascade_steps ? `the cascade runs ${v.cascade_steps} ${v.cascade_steps === 1 ? 'step' : 'steps'}` : 'nothing trips'
  const onAlt = shownVersion === 'alt' && alt
  return (
    <section className="stack boom-agent__result" aria-label="The plan the engine checked">
      <div ref={top} className={`boom-agent__verdict ${v.ok ? 'boom-agent__verdict--ok' : 'boom-agent__verdict--bad'}`} role="status">
        <strong>{v.ok ? 'Verified by the engine: nobody loses power' : 'The plan does not pass the engine check'}</strong>
        <span>
          {fmtMw(plan.total_mw)} on {n} {n === 1 ? 'site' : 'sites'}
          {plan.partial ? ` (of the ${fmtMw(r.total_mw)} asked for)` : ''} in {r.region_name} ·{' '}
          {v.lines_over ? `${v.lines_over} ${v.lines_over === 1 ? 'line' : 'lines'} over the limit` : `busiest line at ${Math.round(v.busiest_pct)} %`} · {steps} ·{' '}
          {fmt(v.people)} people without power (estimate)
        </span>
      </div>
      <div className="row boom-agent__badges">
        <PlanBy r={r} />
        <Badge>Synthetic grid model</Badge>
      </div>

      {ups.length > 0 && (
        <div className="boom-agent__must">
          <p>
            <strong>
              To build {fmtMw(plan.total_mw)} here, you have to do this:
            </strong>
          </p>
          <ul>
            {ups.slice(0, 4).map((u) => (
              <li key={u.id}>
                Re-rate {u.label} from {fmt(u.old_mva)} to {fmt(u.new_mva)} MVA
              </li>
            ))}
            {ups.length > 4 && <li className="muted">and {ups.length - 4} more</li>}
          </ul>
          <p className="muted">Higher ratings on existing lines (+{fmt(plan.added_mva)} MVA); no new lines, no cost model.</p>
        </div>
      )}
      {plan.partial && (
        <p className="boom-agent__note">
          {plan.partial_reason === 'supply'
            ? `This synthetic model's generators and imports can't cover the full ${fmtMw(r.total_mw)}, and no line upgrade changes that: this is the most it serves with everyone's power on.`
            : `Re-rating lines can't carry the full ${fmtMw(r.total_mw)}: this is the most these sites take without new lines.`}
        </p>
      )}
      {alt && (
        <p className="boom-agent__alt">
          {onAlt ? `On the map: the ${fmtMw(alt.total_mw)} version, no upgrades. ` : `Without upgrades, the same sites take ${fmtMw(alt.total_mw)}. `}
          <button type="button" className="boom-agent__link" onClick={() => showPlanVersion(o, p, onAlt ? 'plan' : 'alt')}>
            {onAlt ? `Back to the full ${fmtMw(plan.total_mw)}` : 'Show that version'}
          </button>
        </p>
      )}
      {r.by === 'gemini' && (r.cached || r.ai?.cached_calls > 0) && (
        <p className="boom-agent__replay">
          {r.cached
            ? 'A replay of an earlier Gemini run with this same goal (no new calls). '
            : `${r.ai.cached_calls === r.ai.calls ? "All of Gemini's decisions" : `${r.ai.cached_calls} of Gemini's ${r.ai.calls} decisions`} came from its cache (the same question asked earlier); the engine re-ran every step. `}
          <button type="button" className="boom-agent__link" onClick={onFresh}>
            Ask Gemini again, live
          </button>
        </p>
      )}
      {v.ok && <p className="boom-agent__next">Press Run the cascade at the bottom to watch the grid hold.</p>}
      {v.ok && onMore && !plan.partial && (
        <p className="boom-agent__more">
          <button type="button" className="boom-agent__link" onClick={onMore}>
            Ask the agent for {sizeLabel(more)}
          </button>{' '}
          on the same grid.
        </p>
      )}
      <p className="boom-agent__fine">
        Room and people are estimates on a synthetic grid model (Breakthrough Energy / Texas A&amp;M), not a prediction about any real site or utility.
      </p>
    </section>
  )
}
