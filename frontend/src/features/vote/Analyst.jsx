import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, ErrorBanner } from '../../ui'
import AgentTrace from '../ai/AgentTrace'
import AiBadge from '../ai/AiBadge'
import HowAiIsUsed from '../ai/HowAiIsUsed'
import { getAnalystJob, startAnalyst } from './voteApi'

// "What it would take": a Gemini agent works the proposal out with the engine as its tools (a size what-if, the
// nearest substations, nearby sites, the verified ways to build it, firm against flexible service, the time of day),
// then writes a short memo whose every number is checked against those results (backend/analyst.py). Run on demand;
// the trace grows live while it works. A finished analysis comes back from the cache and replays step by step.
// Mounted with key={proposal id}, so another proposal starts again from the button.

const POLL_MS = 900
const POLL_MAX = 60
const REPLAY_STEP_MS = 380
const LIVE_STEP_MS = 300

const IDLE = { phase: 'idle', trace: [], result: null, error: null, replay: false }

function Memo({ result }) {
  const m = result.memo
  const gemini = result.by === 'gemini'
  const ref = useRef(null)
  // it lands under the trace the viewer was watching: bring its first lines into view
  useEffect(() => {
    const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    ref.current?.scrollIntoView?.({ block: 'nearest', behavior: calm ? 'auto' : 'smooth' })
  }, [])
  return (
    <article className="vote-memo" aria-labelledby="vote-memo-h" ref={ref}>
      <p className="vote-memo__kicker">
        <AiBadge by={gemini ? 'gemini' : 'fallback'} verified={gemini} why={result.why || undefined} className="aib--wrap">
          {gemini ? `${m.numbers_checked} numbers checked` : null}
        </AiBadge>
      </p>
      <h3 id="vote-memo-h" className="vote-memo__head">
        {m.headline}
      </h3>
      <dl className="vote-memo__parts">
        {m.sections.map((s) => (
          <div key={s.key}>
            <dt>{s.heading}</dt>
            <dd>{s.text}</dd>
          </div>
        ))}
      </dl>
      <p className="vote-note">
        {gemini
          ? `Written by Gemini from ${result.tool_calls} engine ${result.tool_calls === 1 ? 'run' : 'runs'} it chose (${result.calls} Gemini ${result.calls === 1 ? 'call' : 'calls'}); each of the ${m.numbers_checked} numbers in it matched one of those results.`
          : `The plain version (${result.why || 'Gemini unavailable'}): ${result.tool_calls} engine runs in a fixed order and a template memo; every number comes from those runs.`}
        {result.cached ? ' From an earlier run of this case.' : ''}
      </p>
      <div className="vote-note">
        <HowAiIsUsed surface="analyst" />
      </div>
      <p className="vote-note vote-note__frame">{m.frame}</p>
    </article>
  )
}

export default function Analyst({ entry, sim }) {
  const [st, setSt] = useState(IDLE)
  const [memoOn, setMemoOn] = useState(false)
  const timer = useRef(0)
  const runId = useRef(0)

  useEffect(
    () => () => {
      runId.current += 1
      clearTimeout(timer.current)
    },
    [],
  )

  const run = useCallback(async () => {
    const me = ++runId.current
    clearTimeout(timer.current)
    setMemoOn(false)
    setSt({ ...IDLE, phase: 'running' })
    const live = () => runId.current === me
    const fail = (error) => live() && setSt((s) => ({ ...s, phase: 'error', error }))
    try {
      const first = await startAnalyst(entry.id)
      if (!live()) return
      if (first.status === 'done') {
        setSt({ phase: 'done', trace: first.result.trace, result: first.result, error: null, replay: true })
        return
      }
      let polls = 0
      const step = (view) => {
        if (!live()) return
        if (view.status === 'done') return setSt({ phase: 'done', trace: view.result.trace, result: view.result, error: null, replay: false })
        if (view.status === 'error') return fail(new Error(view.error || 'The analyst could not finish this case.'))
        setSt((s) => ({ ...s, trace: view.trace }))
        polls += 1
        if (polls > POLL_MAX) return fail(new Error('The analyst is taking too long. Try again in a moment.'))
        timer.current = window.setTimeout(() => getAnalystJob(view.job).then(step).catch(fail), POLL_MS)
      }
      step(first)
    } catch (e) {
      fail(e)
    }
  }, [entry.id])

  if (!sim?.tested)
    return <p className="vote-note">The analyst works on a case the grid model can test; this proposal can&apos;t be run on a model ({sim?.reason || 'not tested'}).</p>

  const { phase, trace, result, error, replay } = st
  const done = phase === 'done' && result
  const badge =
    phase === 'running' ? (
      <AiBadge by="gemini">working</AiBadge>
    ) : done ? (
      <AiBadge by={result.by === 'gemini' ? 'gemini' : 'fallback'} verified={result.by === 'gemini'} why={result.why || undefined} compact />
    ) : null
  const totals = done ? `${result.tool_calls} engine ${result.tool_calls === 1 ? 'run' : 'runs'} · ${result.calls} Gemini ${result.calls === 1 ? 'call' : 'calls'} · ${(result.ms / 1000).toFixed(1)} s` : null

  return (
    <div className="vote-analyst stack">
      <p className="vote-body">
        Gemini works this proposal out as an agent. It chooses which engine runs to make (a size what-if, the nearest substations, nearby sites that take it, the verified
        ways to build it, firm against flexible service), reads each result, and writes a short memo. Every number in the memo is checked against the engine&apos;s results
        before it is shown.
      </p>
      {phase === 'idle' && (
        <div className="vote-actions">
          <Button onClick={run}>Run the AI analyst</Button>
        </div>
      )}
      {phase !== 'idle' && (
        <AgentTrace
          trace={trace}
          live={phase === 'running'}
          heading="Watch the AI work"
          badge={badge}
          totals={totals}
          stepMs={replay ? REPLAY_STEP_MS : LIVE_STEP_MS}
          onDone={done ? () => setMemoOn(true) : undefined}
          follow
          className="vote-agt"
        />
      )}
      {done && memoOn && <Memo result={result} />}
      {phase === 'error' && <ErrorBanner error={error} onRetry={run} />}
    </div>
  )
}
