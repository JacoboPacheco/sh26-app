// FEATURE: "Three Gemini planners vs the engine" (backend/plan_agents.py) for Strengthen the grid. Who decides what
// needs fixing? Three Gemini agents, each with its own strategy, plan the upgrades through the engine's tools (native
// function calling); the engine's own greedy plan, cut at the knee of its cost curve, is the fourth competitor. A
// referee (code) re-runs every plan: the most campuses at once within the budget wins, then the cheapest.
//
// Self-contained: it fetches the budget bar when shown (a read of the finished study) and starts the race only when
// asked (LAZY). Four lanes stream each planner's tool calls as they happen ("Tries Crystal River" -> "The Jacksonville
// 64 transformer goes to 114 %"), a small meter per lane (campuses, money against the budget), the referee's verdict,
// then the finish: a leaderboard and one sentence. Sober (LOOK): green only for a verified plan, red only for a failed
// one, no glow or pops; reduced motion shows every row at once. Lanes sit four across, two across, or stacked (375 px),
// by the panel's own width (a container query), so it fits the Strengthen rail or a wide sheet.
//
// Props: region, mw, loadFactor, mode ('firm' | 'flexible') — the Strengthen study's case · onShowPlan(plan | null):
// "Show on the map" per VERIFIED plan (a plan the referee failed or found over the budget is never offered: it would
// read as a working plan on the meter) (plan = {lane, by, name, mode, mw, campuses, cost, upgrades, placements [{n, id, area, lat,
// lon}], projects [capacity's project records, both ends], steps (capacity's step shape, for the meter) | null,
// verified, verdict, engine: the engine's race plan}); null = back to the page's own plan; raceCapPlan(plan) (raceCap.js)
// turns it into the meter's and CapacityLayer's plan shape · shownLane: which lane's plan the map shows (controlled;
// omit to let the panel track it) · client: {start, get, knee} (default the live API; the preview passes a recorded
// race) · heading · className.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, ErrorBanner } from '../../ui'
import AgentTrace from '../ai/AgentTrace'
import AiBadge from '../ai/AiBadge'
import { groupTrace, loc } from '../ai/trace'
import { money } from './money'
import { liveClient } from './planraceApi'
import usePlanRace from './usePlanRace'
import './planrace.css'

// before the race: who is racing and how (the server's lanes replace these once it starts)
const ROSTER = [
  { id: 'engine', name: 'The engine', strategy: 'Its own greedy plan, cut at the knee', by: 'engine' },
  { id: 'cheapest', name: 'Cheapest first', strategy: 'The fewest dollars per campus', by: 'gemini' },
  { id: 'corridors', name: 'Corridors', strategy: 'Fix one weak corridor fully, then build along it', by: 'gemini' },
  { id: 'flexible', name: 'Flexible-aware', strategy: 'Room first; upgrades last', by: 'gemini' },
]
const VERDICT = {
  verified: 'Verified',
  over_budget: 'Over the budget',
  failed: 'Failed',
  empty: 'No campus',
}

const plural = (n, one, many) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`

// A new case (state, size, load level, campus type) starts the panel afresh: its race, its budget bar, what the map shows.
export default function PlanRacePanel(props) {
  const { region = 'FL', mw = 1000, loadFactor = 1, mode = 'firm', client = liveClient } = props
  return <PlanRace key={`${region}|${mw}|${loadFactor}|${mode}`} {...props} region={region} mw={mw} loadFactor={loadFactor} mode={mode} client={client} />
}

function PlanRace({ region, mw, loadFactor, mode, onShowPlan, shownLane, client, heading, className }) {
  const rc = usePlanRace({ client, region, mw, loadFactor, mode })
  const [ownShown, setOwnShown] = useState(null)
  const [traceLane, setTraceLane] = useState(null)
  const shown = shownLane !== undefined ? shownLane : ownShown
  const lanes = rc.race?.lanes?.length ? rc.race.lanes : ROSTER.map((r) => ({ ...r, status: 'waiting', trace: [] }))
  const result = rc.race?.result || null
  const knee = rc.race?.knee || rc.knee
  const busy = rc.phase === 'starting' || rc.phase === 'running'
  const finished = rc.phase === 'done' && rc.revealed && result
  const board = useMemo(() => (finished ? Object.fromEntries(result.leaderboard.map((r) => [r.lane, r])) : {}), [finished, result])
  const ready = !!knee && !rc.kneeError
  const flex = mode === 'flexible'

  // the latest callback (a new race or a new case sends the map back to the page's own plan)
  const last = useRef(onShowPlan)
  useEffect(() => {
    last.current = onShowPlan
  })

  const show = (ln) => {
    const next = shown === ln.id ? null : ln.id
    setOwnShown(next)
    const engine = lanes.find((l) => l.id === 'engine')?.plan || null
    last.current?.(next ? { ...ln.plan, verified: ln.verdict?.status === 'verified', verdict: ln.verdict, engine } : null)
  }
  const start = () => {
    if (shown) last.current?.(null)
    setOwnShown(null)
    setTraceLane(null)
    rc.start()
  }

  const elapsed = rc.race?.elapsed_s
  const status = statusLine(rc, lanes, result, finished)

  return (
    <section className={`prc${className ? ` ${className}` : ''}`} aria-labelledby="prc-h">
      <header className="prc__head">
        <div className="prc__intro">
          <p className="prc__eyebrow">Who decides what needs fixing?</p>
          <h2 className="prc__h" id="prc-h">
            {heading || 'Three Gemini planners vs the engine'}
          </h2>
          <p className="prc__lede">
            Three Gemini agents plan the upgrades their own way, calling the engine’s tools; the engine’s own search is the fourth. A referee re-runs every plan: the
            most data centers {flex ? '(flexible) ' : ''}at once within the budget wins.
          </p>
        </div>
        <div className="prc__go">
          <Button variant={rc.phase === 'done' ? 'secondary' : 'primary'} onClick={start} busy={busy} disabled={!ready}>
            {busy ? `Racing${elapsed ? ` · ${Math.round(elapsed)} s` : '…'}` : rc.phase === 'done' ? 'Race again' : 'Start the race'}
          </Button>
          {result?.fallback ? <AiBadge by="fallback" why={result.why} compact /> : <AiBadge by="gemini" verified={!!finished && board[result?.winner]?.by === 'gemini'} />}
        </div>
      </header>

      {rc.kneeError ? (
        <p className="prc__note" role="note">
          {rc.kneeError}
        </p>
      ) : knee ? (
        <p className="prc__bar">
          <span className="prc__bar-k">Budget {money(knee.budget)}</span>
          <span className="prc__bar-t">
            The knee of the engine’s cost curve: {plural(knee.campuses, 'campus', 'campuses')} at once
            {knee.next ? `; the ${ord(knee.next.n)} alone would cost ${money(knee.next.cost_high)}, ${knee.next.times}× the ${money(knee.avg)} per upgraded campus so far` : ''}.
          </span>
        </p>
      ) : (
        <p className="prc__bar prc__bar--wait">Reading the study’s budget bar…</p>
      )}

      <p className="prc-sr" role="status" aria-live="polite">
        {status}
      </p>

      {rc.phase === 'error' && <ErrorBanner error={rc.error} onRetry={start} />}

      <ol className="prc__lanes" aria-label="The four planners">
        {lanes.map((ln) => (
          <Lane
            key={ln.id}
            ln={ln}
            knee={knee}
            count={rc.shown}
            row={board[ln.id]}
            winner={finished && result.winner === ln.id}
            canShow={!!onShowPlan}
            showing={shown === ln.id}
            onShow={() => show(ln)}
            waiting={rc.phase === 'idle' || rc.phase === 'starting'}
          />
        ))}
      </ol>

      {finished && (
        <section className="prc-board" aria-labelledby="prc-board-h">
          <h3 className="prc-board__h" id="prc-board-h">
            The finish
          </h3>
          <p className="prc-board__sentence">{result.sentence}</p>
          <table className="prc-table">
            <caption className="prc-sr">Ranked by the most campuses at once within the budget, then the lowest cost, then the fewest upgrades</caption>
            <thead>
              <tr>
                <th scope="col">Place</th>
                <th scope="col">Planner</th>
                <th scope="col">Campuses at once</th>
                <th scope="col">Cost (high end)</th>
                <th scope="col">Upgrades</th>
                <th scope="col">Referee</th>
              </tr>
            </thead>
            <tbody>
              {result.leaderboard.map((r) => (
                <tr key={r.lane} className={`${r.lane === result.winner ? 'is-win' : ''} ${r.verified ? 'is-ok' : r.status === 'failed' ? 'is-bad' : ''}`}>
                  <td data-label="Place" className="prc-table__n">
                    {r.place ?? '—'}
                  </td>
                  <th scope="row" data-label="Planner" data-place={r.place ?? undefined}>
                    <span className={`prc-who prc-who--${r.by}`} aria-hidden="true" />
                    {r.name}
                    {r.lane === result.winner && <span className="prc-table__win">{r.by === 'engine' ? ' · stands' : ' · wins'}</span>}
                    {r.outcome === 'matched' ? (
                      <span className="prc-table__tag"> · matched</span>
                    ) : (
                      r.tied && r.lane !== result.winner && <span className="prc-table__tag"> · tied</span>
                    )}
                  </th>
                  <td data-label="Campuses at once" className="prc-table__n">
                    {r.status === 'offline' ? '—' : r.campuses}
                  </td>
                  <td data-label="Cost (high end)" className="prc-table__n">
                    {r.status === 'offline' ? '—' : money(r.cost_high)}
                  </td>
                  <td data-label="Upgrades" className="prc-table__n">
                    {r.status === 'offline' ? '—' : r.upgrades}
                  </td>
                  <td data-label="Referee" className="prc-table__ref">
                    {r.status === 'offline' ? 'Didn’t run' : VERDICT[r.status] || r.status}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="prc-board__rules">{result.rules}</p>
          <div className="prc-board__trace">
            <span className="prc-board__trace-l" id="prc-trace-l">
              Every call, result and reason:
            </span>
            <div className="prc-board__trace-b" role="group" aria-labelledby="prc-trace-l">
              {result.lanes
                .filter((ln) => ln.trace?.length)
                .map((ln) => (
                  <button key={ln.id} type="button" className="prc-chip" aria-pressed={traceLane === ln.id} onClick={() => setTraceLane((t) => (t === ln.id ? null : ln.id))}>
                    {ln.name}
                  </button>
                ))}
            </div>
            {traceLane && (
              <AgentTrace
                className="prc-board__agt"
                trace={result.lanes.find((ln) => ln.id === traceLane)?.trace || []}
                heading={`${result.lanes.find((ln) => ln.id === traceLane)?.name}: every step`}
                animate={false}
              />
            )}
          </div>
        </section>
      )}

      <p className="prc__foot">
        Synthetic grid model (Breakthrough Energy / Texas A&amp;M), not any utility’s network. Costs are estimates (high end), priced by the engine
        {result?.model ? `; planners: ${result.model}` : ''}.{rc.cached && rc.phase === 'done' ? ' This race was already run for this case: replayed from the server.' : ''}
      </p>
    </section>
  )
}

function ord(n) {
  const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : { 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th'
  return `${n}${s}`
}

// a lane's place: a plan that matched the engine's says so (the same campuses for the same money is not a loss);
// plans the ranking can't tell apart share a place ("Tied 2nd")
function placeText(row) {
  if (row.outcome === 'matched') return 'Matched'
  return row.tied ? `Tied ${ord(row.place)}` : ord(row.place)
}

function statusLine(rc, lanes, result, finished) {
  if (rc.phase === 'idle') return ''
  if (rc.phase === 'starting') return 'Starting the race.'
  if (rc.phase === 'error') return `The race failed: ${rc.error}`
  if (finished) return result.sentence
  const done = lanes.filter((l) => l.verdict).length
  return `Racing: ${done} of ${lanes.length} plans judged.`
}

// ------------------------------------------------------------------ one lane
function Lane({ ln, knee, count, row, winner, canShow, showing, onShow, waiting }) {
  const rows = useMemo(() => (ln.trace || []).slice(0, count === Infinity ? undefined : count), [ln.trace, count])
  // the footer says why a lane didn't run: its feed doesn't repeat it
  const items = useMemo(() => groupTrace(rows.filter((r) => r.kind !== 'offline')), [rows])
  const all = (ln.trace || []).length
  const caughtUp = rows.length >= all
  // the meter follows the rows shown: the last one that carries the plan's state
  const st = useMemo(() => {
    for (let i = rows.length - 1; i >= 0; i--) if (rows[i].state) return rows[i].state
    return null
  }, [rows])
  const verdictRow = rows.find((r) => r.kind === 'verify')
  const verdict = verdictRow ? ln.verdict : null
  const offline = ln.status === 'offline' && caughtUp
  const thinking = !waiting && !verdict && !offline && caughtUp && (ln.status === 'working' || ln.status === 'judging' || ln.status === 'waiting')
  const tone = verdict ? (verdict.status === 'verified' ? 'ok' : verdict.status === 'failed' ? 'bad' : 'muted') : offline ? 'off' : 'run'

  const feed = useRef(null)
  useEffect(() => {
    const el = feed.current
    if (el) el.scrollTop = el.scrollHeight
  }, [items.length, thinking])

  const badge =
    ln.by === 'engine' ? <AiBadge by="engine" compact /> : offline ? <AiBadge by="fallback" why={ln.why} compact /> : <AiBadge by="gemini" verified={verdict?.status === 'verified'} />

  return (
    <li className={`prl prl--${ln.by} prl--${tone}${waiting && !items.length ? ' prl--waiting' : ''}${winner ? ' is-win' : ''}${showing ? ' is-showing' : ''}`}>
      <header className="prl__head">
        <div className="prl__row">
          <h3 className="prl__name">{ln.name}</h3>
          {winner && <span className="prl__win">{ln.by === 'engine' ? 'Stands' : 'Wins'}</span>}
          {!winner && row?.place && <span className="prl__place">{placeText(row)}</span>}
        </div>
        <p className="prl__strategy">{ln.strategy}</p>
        <div className="prl__badge">{badge}</div>
      </header>

      <Meter st={st} knee={knee} tone={tone} />

      <ol className="prl__feed" ref={feed} aria-label={`${ln.name}: its steps`}>
        {waiting && !items.length && <li className="prl-step prl-step--idle">Waiting for the start.</li>}
        {items.map((it) => (
          <Step key={it.key} it={it} />
        ))}
        {thinking && (
          <li className="prl-step prl-step--think" aria-hidden="true">
            <span className="prl-think" />
            {ln.by === 'engine' ? 'Engine working' : 'Gemini is choosing its next move'}
          </li>
        )}
      </ol>

      <footer className="prl__foot">
        {verdict ? (
          <p className={`prl__verdict prl__verdict--${tone}`}>
            <span className="prl__mark" aria-hidden="true" />
            <span>
              <strong>{VERDICT[verdict.status] || verdict.status}</strong>
              {verdict.status === 'verified' || verdict.status === 'over_budget'
                ? `: ${plural(verdict.campuses, 'campus', 'campuses')} at once, ${money(verdict.cost.high)}`
                : verdict.reason
                  ? `: ${verdict.reason}`
                  : ''}
              {ln.auto_submitted ? ' (its working plan when its run ended)' : ''}
            </span>
          </p>
        ) : offline ? (
          <p className="prl__verdict prl__verdict--off">Didn’t run: {ln.why || 'Gemini unavailable'}</p>
        ) : (
          <p className="prl__verdict prl__verdict--wait">{waiting ? 'Ready' : 'Planning…'}</p>
        )}
        {canShow && verdict?.status === 'verified' && ln.plan?.campuses > 0 && (
          <button type="button" className="prl__show" aria-pressed={showing} onClick={onShow}>
            {showing ? 'Back to the page’s plan' : 'Show on the map'}
          </button>
        )}
      </footer>
    </li>
  )
}

function Step({ it }) {
  const h = it.head
  const v = it.verdict
  if (h.kind === 'plan' || h.kind === 'retry' || h.kind === 'refused' || h.kind === 'offline' || h.kind === 'result' || h.kind === 'verify') {
    return <li className={`prl-step prl-step--note prl-step--${h.tone || 'info'}`}>{loc(h.title)}</li>
  }
  const pending = !v
  return (
    <li className={`prl-step prl-step--${v?.tone || 'info'}${h.kind === 'propose' ? ' prl-step--submit' : ''}`} title={loc(h.detail) || undefined}>
      <span className={`prc-who prc-who--${h.actor}`} aria-hidden="true" />
      <span className="prl-step__txt">
        <span className="prl-step__do">{loc(h.title)}</span>
        {pending ? <span className="prl-step__got prl-step__got--wait">…</span> : <span className="prl-step__got">{loc(v.title)}</span>}
      </span>
    </li>
  )
}

// campuses connected (cells, the knee's count marked) and the money spent against the budget; always one cell past
// the bar (a plan that beats it has somewhere to land), however high the bar is (Florida at 500 MW, flexible: 14)
const CELLS_MAX = 40

function Meter({ st, knee, tone }) {
  const bar = knee?.campuses || 0
  const n = st?.campuses || 0
  const cells = Math.min(CELLS_MAX, Math.max(bar + 1, n))
  const budget = knee?.budget || 0
  const cost = st?.cost_high || 0
  const pct = budget > 0 ? Math.min(100, (cost / budget) * 100) : 0
  const over = budget > 0 && cost > budget + 0.5
  return (
    <div className={`prl-meter prl-meter--${tone}`}>
      <div className="prl-meter__cells" role="img" aria-label={`${plural(n, 'campus', 'campuses')} connected at once${bar ? `; the engine's bar is ${bar}` : ''}`}>
        {Array.from({ length: cells }, (_, i) => (
          <span key={i} className={`prl-cell${i < n ? ' is-on' : ''}${i === bar - 1 ? ' is-bar' : ''}`} />
        ))}
      </div>
      <p className="prl-meter__t">
        <span className="prl-meter__n">{plural(n, 'campus', 'campuses')}</span>
        <span className="prl-meter__m">
          {money(cost)} of {money(budget)}
        </span>
      </p>
      <div className="prl-meter__spend" aria-hidden="true">
        <span className={over ? 'is-over' : ''} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}
