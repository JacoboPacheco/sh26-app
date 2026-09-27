// FEATURE: "Harden before the storm (Gemini)", in hurricane mode's results once the storm has landed and its cascade
// has run. Pick a budget; Gemini plans which knocked-out lines to rebuild to withstand the storm, working through the
// engine's tools (list the lines in the storm's reach, who each one serves, what hardening it costs, test a set);
// the engine re-runs the storm and the cascade on every plan it submits and sends back where people are still out;
// Gemini revises (up to 3 plans). The engine's own greedy plan is the bar, and the answer without a key.
// The map (HardenLayer) draws the plan's lines in green and replays the storm with them.
//
// Props: hits (the hurricane store's hits for this storm: the plan belongs to it)
import { useEffect, useRef, useState } from 'react'
import { useOverload } from '../../store'
import { Button, ErrorBanner } from '../../ui'
import AgentTrace, { AgentTraceToggle } from '../ai/AgentTrace'
import AiBadge from '../ai/AiBadge'
import { money } from '../cost/money'
import { getHurricane } from '../hurricane/hurricaneStore'
import { framePoints } from '../hurricane/trackGeom'
import './harden.css'
import {
  AREAS_MS,
  BUDGETS,
  SWEEP_MS,
  closeMap,
  getHarden,
  loadHardenInfo,
  pauseReplay,
  planHarden,
  reducedMotion,
  planOf,
  replayAt,
  resumeReplay,
  setHarden,
  showPlan,
  stopPlanning,
  useHarden,
} from './hardenStore'

const n = (v) => Math.max(0, Math.round(Number(v) || 0)).toLocaleString('en-US')
// a plan's price with one decimal: $149.5 million never reads as the $150 million budget
const usd = (x) => (x >= 1e9 ? `$${(x / 1e9).toFixed(2)} billion` : x >= 1e6 ? `$${(x / 1e6).toFixed(1)} million` : money(x))
const plural = (k, one, many) => `${n(k)} ${k === 1 ? one : many}`
// a plan's price as its typical range (the budget is checked at the high end): "$93.0–149.5 million"
function usdRange(lo, hi) {
  if (!(lo > 0) || hi - lo < 0.05e6) return usd(hi)
  if (lo >= 1e9) return `$${(lo / 1e9).toFixed(2)}–${(hi / 1e9).toFixed(2)} billion`
  if (lo >= 1e6 && hi < 1e9) return `$${(lo / 1e6).toFixed(1)}–${(hi / 1e6).toFixed(1)} million`
  return `${usd(lo)} to ${usd(hi)}`
}
const NOT_CONFIGURED = 'Gemini is not configured on this server'

function progressText(p, trace) {
  if (!p) return 'Sending the storm to the engine…'
  if (p.phase === 'prep' || p.phase === 'singles')
    return p.total ? `The engine re-runs the storm with each of the ${n(p.total)} lines kept alone: ${n(p.done)} of ${n(p.total)}` : 'The engine re-runs the storm…'
  if (p.phase === 'engine') return 'The engine builds its own plan: the most people kept on per dollar'
  if (p.phase === 'gemini') {
    const rounds = trace.filter((r) => r.kind === 'verify').length
    return rounds ? `Gemini is revising: ${plural(rounds, 'plan', 'plans')} checked by the engine so far` : 'Gemini is exploring the storm’s lines through the engine’s tools'
  }
  return 'Finishing…'
}

// re-render once the replay has played out (Pause turns into Replay)
function useReplayDone(clock, shown) {
  const total = SWEEP_MS + AREAS_MS
  const [, bump] = useState(0)
  const at = replayAt(clock)
  const done = !shown || at >= total
  useEffect(() => {
    if (done || clock.pausedAt != null) return undefined
    const id = setTimeout(() => bump((x) => x + 1), total - at + 30)
    return () => clearTimeout(id)
  }, [done, clock, at, total])
  return done
}

export default function HardenControl({ hits }) {
  const O = useOverload()
  const h = useHarden()
  const mine = h.forHits === hits
  const status = mine ? h.status : 'idle'
  const res = mine ? h.result : null
  const noGemini = h.info?.configured === false // known before a run: the engine's plan is the plain version here
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    loadHardenInfo()
    return () => {
      alive.current = false
    }
  }, [])

  // draw a plan and replay the storm with it: the camera frames the storm, and on a phone (the map sits above this
  // panel) the map scrolls into view, both when the plan lands and on every "Show on the map" / "Replay"
  function show(which) {
    const r = getHarden().result
    if (r) O.focus(framePoints(r.storm.points, r.storm.radius_km))
    showPlan(which)
    if (window.matchMedia?.('(max-width: 860px)').matches) document.querySelector('.mc .map')?.scrollIntoView?.({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' }) // centre: the sticky top bar covers the top of the page
  }

  function start() {
    const s = getHurricane()
    const body = { ...O.caseBody, preset: s.presetId || undefined, points: s.points, category: s.category, radius_km: s.radiusKm, budget_usd: h.budget }
    delete body.trip // the storm's lines come from the storm itself
    // the plan lands after a while: show it the same way, unless this panel has closed meanwhile (then just draw it)
    planHarden(body, hits, O.cascade, (which) => (alive.current ? show(which) : showPlan(which)))
  }

  return (
    <section className="hd" aria-label="Harden before the storm">
      <header className="hd__head">
        <h3 className="hd__h">Harden before the storm</h3>
        {res && res.by !== 'gemini' ? (
          <AiBadge by="fallback" why={res.why || 'Gemini unavailable'} compact />
        ) : !res && noGemini ? (
          <AiBadge by="fallback" why={NOT_CONFIGURED} compact />
        ) : (
          <AiBadge by="gemini" verified />
        )}
      </header>
      <p className="hd__lede">
        Which lines, rebuilt to withstand this storm, keep the most people&apos;s power on?{' '}
        {noGemini
          ? `${NOT_CONFIGURED}, so the engine plans within a budget (the most people kept on per dollar) and re-runs the storm on its plan.`
          : 'Gemini plans within a budget; the engine re-runs the storm on every plan.'}
      </p>

      <div className="hd__budget" role="group" aria-label="Budget">
        <span className="hd__budget-l">Budget</span>
        {BUDGETS.map((b) => (
          <button
            key={b}
            type="button"
            className="hd__chip"
            aria-pressed={h.budget === b}
            disabled={status === 'running'}
            onClick={() => h.budget !== b && setHarden({ budget: b, ...(mine && status !== 'running' ? { status: 'idle', result: null, trace: [], shown: false, error: null } : {}) })}
            aria-label={money(b)}
          >
            ${b / 1e6}M
          </button>
        ))}
      </div>

      {status === 'running' ? (
        <div className="hd__run" role="status">
          <div className="row">
            <Button busy>Planning…</Button>
            <Button variant="secondary" onClick={stopPlanning}>
              Stop
            </Button>
          </div>
          <p className="hd__progress">{progressText(h.progress, h.trace)}</p>
          {h.progress?.phase === 'singles' && h.progress.total > 0 && (
            <div className="hd__bar" aria-hidden="true">
              <span style={{ width: `${Math.round((100 * h.progress.done) / h.progress.total)}%` }} />
            </div>
          )}
          {h.trace.length > 0 && <AgentTrace trace={h.trace} live follow brief heading="Watch the AI work" className="hd__trace" stepMs={380} />}
        </div>
      ) : (
        !res && <Button onClick={start}>{noGemini ? 'Harden before the storm' : 'Harden before the storm (Gemini)'}</Button>
      )}

      {mine && status === 'error' && <ErrorBanner error={h.error} onRetry={start} />}
      {res && <Result res={res} h={h} show={show} />}
    </section>
  )
}


function PlanRow({ plan, label, badge, best, on, why, onShow }) {
  return (
    <li className={`hd-cmp__row${best ? ' hd-cmp__row--best' : ''}${on ? ' hd-cmp__row--on' : ''}`}>
      <div className="hd-cmp__who">
        <span className="hd-cmp__label">{label}</span>
        {badge}
        {best && <span className="hd-cmp__best">Best</span>}
      </div>
      {plan ? (
        <>
          <p className="hd-cmp__out">
            <b>{n(plan.people_out)}</b> still out <span className="hd-cmp__cost">(for {usdRange(plan.cost_low_usd, plan.cost_usd)}, typical)</span>
          </p>
          <p className="hd-cmp__sub">
            keeps <b className="hd-kept">{n(plan.people_kept)}</b> on · hardens {plural(plan.lines.length, 'line', 'lines')}
          </p>
          <button type="button" className="hd-cmp__show" aria-pressed={on} onClick={onShow}>
            {on ? 'On the map' : 'Show on the map'}
          </button>
        </>
      ) : (
        <p className="hd-cmp__sub">{why}</p>
      )}
    </li>
  )
}

function Result({ res, h, show }) {
  const done = useReplayDone(h.clock, h.shown)
  const paused = h.clock.pausedAt != null
  const e = res.engine
  const g = res.gemini
  const bestBy = res.best?.by
  const drawn = h.shown ? planOf(res, h.which) : null
  const cur = drawn || planOf(res, 'best')
  const whose = cur === g ? 'Gemini’s' : 'the engine’s'
  const towns = (list) => list.slice(0, 4).map((t) => `${t.town} (${n(t.people)})`).join(', ')
  return (
    <div className="hd-res">
      <ol className="hd-cmp" aria-label="People still without power when the grid settles">
        <li className="hd-cmp__row hd-cmp__row--none">
          <div className="hd-cmp__who">
            <span className="hd-cmp__label">Without hardening</span>
          </div>
          <p className="hd-cmp__out">
            <b className="hd-lost">{n(res.none.people_out)}</b> still out
          </p>
          <p className="hd-cmp__sub">the storm knocks out {plural(res.storm.lines, 'line', 'lines')}, then the cascade</p>
        </li>
        <PlanRow plan={e} label="Engine plan" badge={<AiBadge by="engine" />} best={bestBy === 'engine'} on={drawn === e} onShow={() => show('engine')} />
        <PlanRow
          plan={g}
          label="Gemini’s plan"
          badge={res.by === 'gemini' ? <AiBadge by="gemini" verified /> : <AiBadge by="fallback" why={res.why || 'Gemini unavailable'} compact />}
          best={!!g && bestBy === 'gemini'}
          on={!!g && drawn === g}
          why={`${res.why || 'Gemini unavailable'}: the engine plan is the answer.`}
          onShow={() => show('gemini')}
        />
      </ol>
      <p className="hd-cmp__foot">Costs are typical ranges from public filings and vary; each budget is checked at the high end.</p>

      {h.shown && (
        <>
          <div className="row hd-res__ctl">
            {!done ? (
              <Button variant="secondary" onClick={paused ? resumeReplay : pauseReplay}>
                {paused ? 'Play' : 'Pause'}
              </Button>
            ) : (
              <Button variant="secondary" onClick={() => show(h.which)}>
                Replay the storm
              </Button>
            )}
            <Button variant="secondary" onClick={closeMap}>
              Hide from the map
            </Button>
          </div>
          <ul className="hd-key" aria-label="On the map">
            <li>
              <i className="hd-key__sw hd-key__sw--held" /> hardened, holds
            </li>
            <li>
              <i className="hd-key__sw hd-key__sw--hit" /> knocked out
            </li>
            <li>
              <i className="hd-key__dot hd-key__dot--kept" /> kept on
            </li>
            <li>
              <i className="hd-key__dot hd-key__dot--out" /> still out
            </li>
          </ul>
        </>
      )}

      {cur && (cur.kept_towns?.length > 0 || cur.still_out?.length > 0 || cur.newly_out?.length > 0) && (
        <dl className="hd-towns" aria-label="Town by town (people, estimate)">
          {cur.kept_towns?.length > 0 && (
            <div>
              <dt>Kept on by {whose} plan</dt>
              <dd>{towns(cur.kept_towns)}</dd>
            </div>
          )}
          {cur.newly_out?.length > 0 && (
            <div className="hd-towns__newly">
              <dt>Newly out with {whose} plan</dt>
              <dd>
                {towns(cur.newly_out)}. With these lines kept, the cascade takes another path; the {n(cur.people_kept)} kept on is net of them.
              </dd>
            </div>
          )}
          {cur.still_out?.length > 0 && (
            <div>
              <dt>Still without power</dt>
              <dd>{towns(cur.still_out)}</dd>
            </div>
          )}
        </dl>
      )}

      <AgentTraceToggle
        trace={res.trace}
        label={res.by === 'gemini' ? `Watch the AI work (${plural(res.rounds.length, 'plan', 'plans')}, ${plural(res.function_calls, 'tool call', 'tool calls')})` : 'How the engine planned it'}
        heading={res.by === 'gemini' ? undefined : 'How the engine planned it'}
        brief
        className="hd__trace"
        stepMs={320}
        totals={res.model ? `${plural(res.calls, 'Gemini call', 'Gemini calls')} (${res.model}); every plan re-run by the engine` : 'The engine only: no AI in this run'}
      />

      <details className="hd-src">
        <summary>What hardening costs here (typical, varies)</summary>
        <p>{res.cost_method}</p>
        <ul>
          {res.sources.map((s) => (
            <li key={s.id}>
              <a href={s.url} target="_blank" rel="noreferrer">
                {s.name}
              </a>
            </li>
          ))}
        </ul>
      </details>
      <p className="hd-note">
        {res.note} Counted: {res.metric}.
      </p>
    </div>
  )
}
