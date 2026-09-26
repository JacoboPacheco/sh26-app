// FEATURE: the siting planner (owned by the planner track).
// Contract: default export PlannerPanel() — a panel (no props). It reads the region, the load level
// and the firm switch from useOverload(), and changes the case only through it ("Load this plan").
//
// A goal form (a goal in words, the state, the total and how many sites), then the steps the planner
// takes, one by one — Gemini choosing each action and the engine running it, or the built-in planner
// when Gemini isn't available — then the plan the engine checked, and one button that loads it into
// the workspace. Not a chat: one goal in, one checked plan out.
import { useEffect, useId, useMemo, useRef } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Badge, Button, ErrorBanner, Field } from '../../ui'
import { peakPhrase } from '../heat/presets'
import './planner.css'
import { EXAMPLES, GOAL_MAX, SITES_MAX, TOTAL_MAX, TOTAL_MIN, fmtMw, loadIntoWorkspace, readGoal } from './plannerApi'
import { markLoaded, revealed, runPlan, setDraft, useDraft, usePlanner } from './plannerStore'

export default function PlannerPanel() {
  const o = useOverload()
  const p = usePlanner()
  const d = useDraft()
  const { goal, total, sites, read } = d
  const mapRegion = o.region && o.region !== 'US' ? o.region : null
  // the form follows the map's state until you pick one yourself
  const region = (d.picked && d.region) || mapRegion || d.region || 'FL'
  const running = p.status === 'running'
  const hId = useId()

  const regions = useMemo(() => [...(o.regions || [])].sort((a, b) => a.name.localeCompare(b.name)), [o.regions])
  const regionName = regions.find((r) => r.code === region)?.name || (region === 'FL' ? 'Florida' : region)

  const onGoal = (text) => {
    const got = readGoal(text, regions)
    setDraft({
      goal: text,
      read: Object.keys(got).length ? got : null,
      ...(got.mw ? { total: String(got.mw) } : {}),
      ...(got.sites ? { sites: String(got.sites) } : {}),
      ...(got.region ? { region: got.region, picked: true } : {}),
    })
  }
  const pickExample = (ex) => setDraft({ goal: ex.goal, region: ex.region, picked: true, total: String(ex.total), sites: String(ex.sites), read: null })

  // when the plan is in and the map shows its state, frame the campuses (once per run)
  const framed = useRef(0)
  const { grid, focus } = o
  const done = revealed(p) ? p.result : null
  useEffect(() => {
    if (!done?.plan || framed.current === p.runId || p.loaded) return
    if ((grid?.meta?.region || 'FL') !== done.region) return
    framed.current = p.runId
    focus(padded(done.plan.sites.map((s) => [s.lon, s.lat])))
  }, [done, grid, focus, p.runId, p.loaded])

  const totalN = Number(total)
  const sitesN = Number(sites)
  const invalid =
    !Number.isFinite(totalN) || totalN < TOTAL_MIN || totalN > TOTAL_MAX
      ? `Total must be between ${TOTAL_MIN} and ${fmt(TOTAL_MAX)} MW`
      : Math.ceil(totalN / 5000) > sitesN
        ? 'A site takes at most 5,000 MW: allow more sites or ask for less power'
        : null

  const submit = (e) => {
    e.preventDefault()
    if (invalid || running) return
    runPlan({
      region,
      goal: goal.trim(),
      total_mw: Math.round(totalN),
      max_sites: sitesN,
      load_factor: o.loadFactor,
      firm: o.firm,
    })
  }

  return (
    <div className="stack panel-body planner">
      <section className="stack planner-form-sec" aria-labelledby={hId}>
        <div className="planner-head">
          <h3 className="panel-h" id={hId}>
            Siting planner
          </h3>
          <p className="planner-lede">
            Give it a goal. Gemini works the grid engine one step at a time, and the engine checks the plan it hands back.
          </p>
        </div>
        <form className="stack planner-form" onSubmit={submit} noValidate>
          <Field
            as="textarea"
            label="Goal"
            rows={2}
            maxLength={GOAL_MAX}
            value={goal}
            onChange={(e) => onGoal(e.target.value)}
            placeholder={`Place ${fmt(totalN || 2000)} MW of AI campuses in ${regionName} without blacking anyone out`}
            hint={read ? `Read from your goal: ${readText(read, regions)}` : 'Name a town to stay near, or one to avoid.'}
          />
          <div className="planner-examples" role="group" aria-label="Example goals">
            <span className="planner-examples__label">Try</span>
            {EXAMPLES.map((ex) => (
              <button key={ex.label} type="button" className="planner-chip" onClick={() => pickExample(ex)} disabled={running}>
                {ex.label}
              </button>
            ))}
          </div>
          <Field
            as="select"
            label="State"
            value={region}
            onChange={(e) => setDraft({ region: e.target.value, picked: true })}
          >
            {regions.length ? (
              regions.map((r) => (
                <option key={r.code} value={r.code}>
                  {r.name}
                </option>
              ))
            ) : (
              <option value={region}>{regionName}</option>
            )}
          </Field>
          <div className="planner-pair">
            <Field
              label="Total (MW)"
              type="number"
              inputMode="numeric"
              min={TOTAL_MIN}
              max={TOTAL_MAX}
              step={10}
              value={total}
              onChange={(e) => setDraft({ total: e.target.value })}
              aria-invalid={invalid ? true : undefined}
            />
            <Field as="select" label="Sites, at most" value={sites} onChange={(e) => setDraft({ sites: e.target.value })}>
              {Array.from({ length: SITES_MAX }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Field>
          </div>
          <p className="planner-context">
            At {peakPhrase(o.loadFactor)} · {o.firm ? 'firm' : 'flexible'} campuses · synthetic grid model
          </p>
          {invalid && (
            <p className="planner-invalid" role="alert">
              {invalid}
            </p>
          )}
          <div className="row">
            <Button type="submit" busy={running} disabled={!!invalid}>
              {running ? 'Planning…' : 'Make a plan'}
            </Button>
          </div>
        </form>
      </section>

      {p.status === 'error' && <ErrorBanner error={p.error} onRetry={p.request ? () => runPlan(p.request) : undefined} />}
      {(running || p.steps.length > 0) && <Steps p={p} />}
      {revealed(p) && <PlanResult p={p} />}
    </div>
  )
}

function readText(got, regions) {
  const bits = []
  if (got.mw) bits.push(`${fmt(got.mw)} MW`)
  if (got.region) bits.push(regions.find((r) => r.code === got.region)?.name || got.region)
  if (got.sites) bits.push(`up to ${got.sites} ${got.sites === 1 ? 'site' : 'sites'}`)
  return bits.join(' · ')
}

// ------------------------------------------------------------------ the steps
const WHO = { gemini: 'Gemini', planner: 'Built-in planner' }

function who(step) {
  if (step.tool === 'check') return 'Engine check'
  if (step.tool === 'note') return 'Note'
  return WHO[step.by] || 'Planner'
}

// keep the newest step (then the verdict) in view as the list grows
const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
function useKeepInView(ref, key) {
  useEffect(() => {
    ref.current?.scrollIntoView?.({ block: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' })
  }, [ref, key])
}

function Steps({ p }) {
  const shown = p.steps.slice(0, p.shown)
  const waiting = p.status === 'running' || p.shown < p.steps.length
  const listId = useId()
  const tail = useRef(null)
  useKeepInView(tail, p.shown)
  return (
    <section className="stack planner-steps-sec" aria-labelledby={`${listId}-h`}>
      <h3 className="panel-h" id={`${listId}-h`}>
        What the planner did
      </h3>
      <ol className="planner-steps" aria-live="polite">
        {shown.map((s, i) => (
          <li key={s.n} ref={i === shown.length - 1 ? tail : undefined} className={`planner-step planner-step--${s.by}${s.ok === true ? ' planner-step--ok' : s.ok === false ? ' planner-step--bad' : ''}`}>
            <span className="planner-step__who">
              {s.n}. {who(s)}
            </span>
            {s.thought && <p className="planner-step__thought">“{s.thought}”</p>}
            <p className="planner-step__text">{s.text}</p>
          </li>
        ))}
        {waiting && (
          <li className="planner-step planner-step--pending" aria-hidden={p.status !== 'running' ? true : undefined}>
            <span className="planner-step__who">{pendingLabel(p)}</span>
            <span className="planner-dots" aria-label={p.status === 'running' ? 'Working on the next step' : undefined}>
              <span />
              <span />
              <span />
            </span>
          </li>
        )}
      </ol>
    </section>
  )
}

// what loading will do to the workspace, when it's more than adding the plan
function loadHint(o, r) {
  const hasCase = !!(o.site || o.extraSites.length || o.trip.length || Object.keys(o.upgrades).length)
  const away = o.region !== r.region
  if (!away && !hasCase) return null
  const here = o.region === 'US' ? 'the U.S.' : o.grid?.meta?.region_name || o.region
  const move = away ? `The map shows ${here}; loading the plan takes it to ${r.region_name}` : 'Loading the plan'
  return hasCase ? `${move} and replaces the current scenario.` : `${move}.`
}

// what the dots stand for: Gemini choosing its next action, or the engine working
function pendingLabel(p) {
  if (p.status !== 'running') return 'Next step'
  const last = p.steps[p.steps.length - 1]
  if (last?.by === 'gemini' && last.tool !== 'finish') return 'Gemini is choosing the next step'
  if (last?.tool === 'finish') return 'The engine is checking the plan'
  return p.steps.length ? 'Working' : 'Reading the grid model'
}

// ------------------------------------------------------------------ the plan
// why the built-in planner made the plan when Gemini was asked (result.ai.status)
const FALLBACK_BADGE = {
  slow: 'Gemini too slow: built-in planner',
  rejected: "Gemini's plan failed the check: built-in planner",
  out_of_calls: "Gemini's plan failed the check: built-in planner",
  supply: 'Handed to the built-in planner',
}
function PlanResult({ p }) {
  const o = useOverload()
  const r = p.result
  const v = r.verification
  const plan = r.plan
  const alt = r.without_upgrades
  const pendingFocus = useRef(null)
  const top = useRef(null)
  const resultId = useId()
  useKeepInView(top, p.runId)

  // after a load, once the workspace's what-if for the plan lands, frame every campus
  const { result: wf, focus } = o
  useEffect(() => {
    const want = pendingFocus.current
    if (!want || !wf || wf.region !== want.region || (wf.sites?.length || 0) !== want.pts.length) return
    pendingFocus.current = null
    focus(padded(want.pts))
  }, [wf, focus])

  if (!plan || !v) {
    return (
      <section className="planner-result" aria-label="Plan">
        <p className="planner-verdict planner-verdict--bad">No plan: there is nowhere in {r.region_name} to place a campus.</p>
      </section>
    )
  }

  const load = (kind) => {
    const src = kind === 'alt' ? alt : { sites: plan.sites, case: r.case }
    pendingFocus.current = { region: src.case.region, pts: src.sites.map((s) => [s.lon, s.lat]) }
    loadIntoWorkspace(o, src.sites, src.case)
    markLoaded(kind)
  }

  const n = plan.sites.length
  const ups = plan.upgrade_lines || []
  const people = v.people
  return (
    <section className="stack planner-result" aria-labelledby={resultId}>
      <div ref={top} className={`planner-verdict ${v.ok ? 'planner-verdict--ok' : 'planner-verdict--bad'}`}>
        <strong id={resultId}>{v.ok ? 'Checked: nobody loses power' : 'This plan does not pass the check'}</strong>
        <span>
          {fmtMw(plan.total_mw)} on {n} {n === 1 ? 'site' : 'sites'}
          {plan.partial ? ` (of the ${fmtMw(r.total_mw)} asked for)` : ''} in {r.region_name} ·{' '}
          {v.lines_over ? `${v.lines_over} ${v.lines_over === 1 ? 'line' : 'lines'} over the limit` : `busiest line at ${Math.round(v.busiest_pct)} %`} ·{' '}
          {people ? `${fmt(people)} people without power (estimate)` : '0 people without power (estimate)'}
        </span>
      </div>
      <div className="row planner-badges">
        {r.by === 'gemini' ? (
          <Badge>Planned by Gemini{r.cached ? ' · earlier run' : ''}</Badge>
        ) : r.fallback ? (
          <Badge tone="warn">{FALLBACK_BADGE[r.ai?.status] || 'Gemini unavailable: built-in planner'}</Badge>
        ) : (
          <Badge>Built-in planner</Badge>
        )}
        <Badge>Synthetic grid model</Badge>
      </div>

      <ol className="planner-sites" aria-label="Sites">
        {plan.sites.map((s, i) => (
          <li key={s.sub} className="planner-site">
            <span className="planner-site__n" aria-hidden="true">
              {i + 1}
            </span>
            <span className="planner-site__name">
              <strong>{s.town}</strong>
              <span className="muted">
                {s.name} · {fmt(s.kv)} kV
              </span>
            </span>
            <span className="planner-site__mw">{fmtMw(s.mw)}</span>
          </li>
        ))}
      </ol>

      {ups.length > 0 && (
        <div className="planner-ups">
          <p>
            <strong>
              +{fmt(plan.added_mva)} MVA of upgrades on {ups.length} {ups.length === 1 ? 'line' : 'lines'}
            </strong>{' '}
            <span className="muted">(higher ratings only; no new lines, no cost model)</span>
          </p>
          <ul>
            {ups.slice(0, 4).map((u) => (
              <li key={u.id}>
                {u.label}: {fmt(u.old_mva)} to {fmt(u.new_mva)} MVA
              </li>
            ))}
            {ups.length > 4 && <li className="muted">and {ups.length - 4} more</li>}
          </ul>
        </div>
      )}
      {plan.partial && (
        <p className="planner-note">
          {plan.partial_reason === 'supply'
            ? `This synthetic model's generators and imports can't cover the full ${fmtMw(r.total_mw)} of new load, and no line upgrade changes that; this is the most it serves with everyone's power on.`
            : `Re-rating lines can't carry the full ${fmtMw(r.total_mw)}; this is the most these sites take without new lines.`}
        </p>
      )}

      <div className="row">
        <Button onClick={() => load('plan')} disabled={!r.case}>
          Load this plan into the workspace
        </Button>
      </div>
      {!p.loaded && loadHint(o, r) && <p className="planner-context">{loadHint(o, r)}</p>}
      {alt && (
        <p className="planner-alt">
          Without upgrades, the same sites take {fmtMw(alt.total_mw)}.{' '}
          <button type="button" className="planner-link" onClick={() => load('alt')}>
            Load that version instead
          </button>
        </p>
      )}
      {p.loaded && (
        <p className="planner-loaded" role="status">
          Loaded {p.loaded === 'alt' ? `the ${fmtMw(alt.total_mw)} version` : 'the plan'}: the map shows {r.region_name} with {p.loaded === 'alt' ? alt.sites.length : n}{' '}
          {(p.loaded === 'alt' ? alt.sites.length : n) === 1 ? 'campus' : 'campuses'}. Run the cascade to watch it hold.
        </p>
      )}
      <p className="planner-fine">
        Room and people are estimates on a synthetic grid model (Breakthrough Energy / Texas A&amp;M), not a prediction about any real site or
        utility.
      </p>
    </section>
  )
}

// The map's focus frames points edge to edge, but panels float over the map's edges: widen the box
// (a third of its size, at least ~0.3°) so the campuses land in the open middle.
function padded(pts) {
  if (!pts.length) return pts
  const lons = pts.map((q) => q[0])
  const lats = pts.map((q) => q[1])
  const [x0, x1, y0, y1] = [Math.min(...lons), Math.max(...lons), Math.min(...lats), Math.max(...lats)]
  const dx = Math.max((x1 - x0) / 3, 0.3)
  const dy = Math.max((y1 - y0) / 3, 0.3)
  return [...pts, [x0 - dx, y0 - dy], [x1 + dx, y1 + dy]]
}
