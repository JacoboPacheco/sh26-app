// FEATURE: the siting agent's trace (owned by the planner track), shown in the AI boom mode.
// Contract: default export AgentTrace({p}) — p = usePlanner(). One row per step as it streams in:
// who acted (Gemini, the built-in planner, the engine's check), the tool in plain words, the reason
// (Gemini's own sentence, or the rule the built-in planner followed), the call exactly as it ran on
// the engine, and the engine's answer, with the real times (Gemini's decision, the engine's work).
// Nothing here is made up on the client: every figure is a field the backend's step carries.
import { useEffect, useId, useRef, useState } from 'react'
import { fmt } from '../../geo'
import AiBadge from '../ai/AiBadge'
import './agent.css'

const TOOL = {
  headroom_top: 'Rank sites by room',
  split: 'Split the total',
  whatif: 'Solve the grid',
  move: 'Move a campus',
  shrink: 'Find the most that fits',
  fix: 'Find upgrades',
  cascade: 'Run the cascade',
  finish: 'Propose the plan',
  check: 'Verify the plan',
  note: 'Note',
}

const reduced = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// "1.4 s" / "84 ms" / "<1 ms"
function dur(ms) {
  if (ms == null) return ''
  if (ms < 1) return '<1 ms'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

// The call as it ran: the tool's own name and its arguments, sites by town (the step's sites carry the names)
function callText(s) {
  const c = s.call
  if (!c) return null
  const a = c.args || {}
  const bySub = new Map((s.sites || []).map((x) => [x.sub, x]))
  const parts = []
  if (a.total_mw != null) parts.push(`${fmt(a.total_mw)} MW`)
  if (Array.isArray(a.sites) && a.sites.length) {
    parts.push(a.sites.map((x) => `${bySub.get(x.sub)?.town || `substation ${x.sub}`} ${fmt(Number(x.mw) || 0)} MW`).join(', '))
  }
  if (a.n != null) parts.push(`top ${a.n}`)
  if (a.near) parts.push(`near ${a.near}`)
  if (a.use_upgrades) parts.push('with upgrades')
  if (a.upgrades) parts.push(`${a.upgrades} ${a.upgrades === 1 ? 'upgrade' : 'upgrades'}`)
  if (a.limit) parts.push(`limit: ${a.limit}`)
  return `${c.tool}(${parts.join(' · ')})`
}

function Who({ s }) {
  if (s.tool === 'check') return <AiBadge by="engine" title="The power-flow engine re-ran the finished plan: the what-if, then the cascade">check</AiBadge>
  if (s.tool === 'note') return <span className="agent-step__note-who">Note</span>
  if (s.by === 'gemini') return <AiBadge by="gemini" title="Gemini chose this step; the engine ran it" />
  return (
    <AiBadge by="engine" title="The built-in planner (rules, no AI) ran this step on the engine">
      built-in planner
    </AiBadge>
  )
}

// the step a Gemini step answers: the latest earlier step that failed (a line over, a failed check)
function revises(steps, i) {
  const s = steps[i]
  if (s.by !== 'gemini' || s.tool === 'headroom_top') return null
  for (let j = i - 1; j >= 0; j--) {
    const q = steps[j]
    if (q.tool === 'note') continue
    return q.ok === false ? q.n : null
  }
  return null
}

function Row({ s, i, steps, last }) {
  const fix = revises(steps, i)
  const cls = `agent-step agent-step--${s.by} agent-step--${s.tool}${s.ok === true ? ' agent-step--ok' : s.ok === false ? ' agent-step--bad' : ''}${last ? ' agent-step--last' : ''}`
  if (s.tool === 'note') {
    return (
      <li className={cls}>
        <p className="agent-step__notetext">{s.text}</p>
      </li>
    )
  }
  // a decision served from the cache (the same question asked before) says so instead of a time
  const gem = s.ai_cached ? 'Gemini, cached decision' : s.ai_ms != null ? `Gemini ${dur(s.ai_ms)}` : null
  const time = [gem, s.ms != null ? `engine ${dur(s.ms)}` : null].filter(Boolean).join(' · ')
  return (
    <li className={cls}>
      <div className="agent-step__head">
        <span className="agent-step__n">{String(s.n).padStart(2, '0')}</span>
        <Who s={s} />
        <span className="agent-step__tool">{TOOL[s.tool] || s.tool}</span>
        {time && <span className="agent-step__time">{time}</span>}
      </div>
      {fix && <p className="agent-step__revise">Responds to step {fix}</p>}
      {s.thought ? (
        <p className="agent-step__thought">“{s.thought}”</p>
      ) : s.why ? (
        <p className="agent-step__why">{s.why}</p>
      ) : null}
      {s.call && (
        <code className="agent-step__call" title="The tool call as it ran on the engine">
          {callText(s)}
        </code>
      )}
      <p className="agent-step__result">
        <span className="agent-step__arrow" aria-hidden="true">
          →
        </span>{' '}
        <span className="agent-sr">Engine: </span>
        {s.result || s.text}
      </p>
    </li>
  )
}

// a clock for the run while it is on screen (the whole run's time, not a per-step guess)
function useClock(p) {
  const [now, setNow] = useState(() => performance.now())
  const live = p.status === 'running' || p.shown < p.steps.length
  useEffect(() => {
    if (!live) return undefined
    const t = setInterval(() => setNow(performance.now()), reduced() ? 1000 : 100)
    return () => clearInterval(t)
  }, [live])
  const end = p.status === 'running' ? now : p.endedAt || now
  return p.startedAt ? Math.max(0, end - p.startedAt) : 0
}

// what the dots stand for: Gemini choosing, the engine checking, or the rows still being shown
function pendingLabel(p) {
  if (p.status !== 'running') return 'Showing the next step'
  const last = p.steps[p.steps.length - 1]
  if (last?.tool === 'finish') return 'The engine is checking the plan'
  if (last?.by === 'gemini' || (!p.steps.length && p.request)) return 'Gemini is choosing the next step'
  return p.steps.length ? 'The engine is working' : 'Reading the grid model'
}

export default function AgentTrace({ p }) {
  const shown = p.steps.slice(0, p.shown)
  const waiting = p.status === 'running' || p.shown < p.steps.length
  const id = useId()
  const tail = useRef(null)
  const elapsed = useClock(p)
  useEffect(() => {
    tail.current?.scrollIntoView?.({ block: 'nearest', behavior: reduced() ? 'auto' : 'smooth' })
  }, [p.shown])

  const gem = shown.filter((s) => s.by === 'gemini')
  const calls = p.result && p.shown >= p.steps.length ? p.result.ai?.calls || 0 : Math.max(0, ...gem.map((s) => s.call_n || 0))
  const thinking = gem.reduce((t, s) => t + (s.ai_cached ? 0 : s.ai_ms || 0), 0)
  const fromCache = gem.filter((s) => s.ai_cached).length
  const runs = shown.filter((s) => s.ms != null)
  const engine = runs.reduce((t, s) => t + s.ms, 0)

  return (
    <section className="agent-trace" aria-labelledby={`${id}-h`}>
      <div className="agent-trace__bar">
        <h3 className="panel-h" id={`${id}-h`}>
          What the agent did
        </h3>
        <span className="agent-trace__clock" aria-hidden="true">
          {dur(elapsed)}
        </span>
      </div>
      <p className="agent-trace__stats">
        {calls > 0 && (
          <>
            <span>
              Gemini: {calls} {calls === 1 ? 'call' : 'calls'}
              {fromCache ? ` (${fromCache} from cache)` : ''}
              {thinking ? `, ${dur(thinking)}` : ''}
            </span>
            <span aria-hidden="true"> · </span>
          </>
        )}
        <span>
          Engine: {runs.length} {runs.length === 1 ? 'run' : 'runs'}
          {runs.length ? `, ${dur(engine)}` : ''}
        </span>
      </p>
      <ol className="agent-steps" aria-live="polite">
        {shown.map((s, i) => (
          <Row key={s.n} s={s} i={i} steps={shown} last={i === shown.length - 1 && !waiting} />
        ))}
        {waiting && (
          <li className="agent-step agent-step--pending" ref={tail} aria-hidden={p.status !== 'running' ? true : undefined}>
            <span className="agent-step__pending-label">{pendingLabel(p)}</span>
            <span className="agent-step__bar" aria-hidden="true" />
          </li>
        )}
        {!waiting && <li ref={tail} className="agent-steps__end" aria-hidden="true" />}
      </ol>
    </section>
  )
}
