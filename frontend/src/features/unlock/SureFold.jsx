// "How sure is this number?": a quiet fold under the capacity meter (backend capacity.sensitivity). The headline is
// the shipped case; this re-runs the always-on search on copies of the model under other assumptions (the server names
// them: the next MW pro rata to the plants' output, a 90 % planning limit, and where the model has estimated ratings,
// those doubled and with half their room: both sides) and shows each case's campuses today, what the headline's budget
// buys, and what the headline's count costs; then the lines and transformers that bind in every case, and the
// single-outage (N-1) screen of the headline's campuses, since the count itself is N-0. A case that ran out of time
// says so instead of reading as a smaller answer. Florida's warm study has the cases already; elsewhere (LAZY)
// opening the fold asks for them, with the time it takes.
import { fmt } from '../../geo'
import { Button } from '../../ui'
import { shortMoney } from './budget'
import { N1Line } from './CapacityCard'
import { count } from './capacity'
import { requestSensitivity } from './unlockStore'

const ALL_BUT = 4 // lines named under "in all but one case"

// "a, b and c" (typographic apostrophes: the server's words use straight ones)
function andList(xs) {
  const w = xs.map((x) => x.replace(/'/g, '’'))
  return w.length < 2 ? w.join('') : `${w.slice(0, -1).join(', ')} and ${w.at(-1)}`
}

export default function SureFold({ r, flex }) {
  const c = r?.capacity
  const sv = c?.sensitivity
  if (!c || !sv || sv.status === 'skipped' || !c.firm?.steps?.length) return null
  const status = sv.status
  const done = status === 'done'
  const rows = done ? sv.rows : []
  const todays = rows.map((x) => x.today)
  const lo = done ? Math.min(...todays) : 0
  const hi = done ? Math.max(...todays) : 0
  const est = sv.estimate_s
  const cases = sv.cases || []
  const other = cases.length ? `${count(cases.length)} other assumptions` : 'other assumptions'
  const which = cases.length ? `: ${andList(cases.map((x) => x.phrase))}` : ''
  const late = done ? rows.filter((x) => x.stop === 'time').length : 0
  const hint = done
    ? `today ${lo === hi ? fmt(lo) : `${fmt(lo)} to ${fmt(hi)}`} under ${count(rows.length)} sets of assumptions${late ? ` (${count(late)} ran out of time)` : ''}`
    : status === 'pending'
      ? 'checking it under other assumptions…'
      : status === 'error'
        ? 'the check didn’t finish'
        : `check it under ${other}${est ? ` (about ${fmt(est)} s)` : ''}`
  const onToggle = (e) => {
    if (e.currentTarget.open && status === 'not_run') requestSensitivity()
  }

  return (
    <details className="st-sure" onToggle={onToggle}>
      <summary>
        <span className="st-sure__q">How sure is this number?</span>
        <span className="st-sure__hint">{hint}</span>
      </summary>
      <div className="st-sure__body">
        {done ? (
          <Cases sv={sv} flex={flex} />
        ) : status === 'pending' ? (
          <p className="st-sure__wait" role="status">
            Re-running the always-on search on a copy of the model under {other}
            {which}
            {est ? `. About ${fmt(est)} s.` : '.'}
          </p>
        ) : (
          <div className="st-sure__wait">
            <p>
              {status === 'error' ? `The check didn’t finish${sv.error ? `: ${sv.error}` : ''}. ` : ''}
              It re-runs the always-on search on a copy of the model under {other}
              {which}
              {est ? `, in about ${fmt(est)} s` : ''}.
            </p>
            <Button variant="secondary" onClick={requestSensitivity}>
              {status === 'error' ? 'Try again' : 'Run the check'}
            </Button>
          </div>
        )}
        <p className="st-sure__n1">
          The count is N-0 (every line in service). <N1Line n1={c.n1} />
        </p>
      </div>
    </details>
  )
}

// the lines that bind in all but one case, grouped by the case they miss: [[missing case id, [lines]]]
function butOne(most, ids) {
  const out = new Map()
  for (const b of most) {
    const miss = ids.find((id) => !b.cases.includes(id))
    if (miss) out.set(miss, [...(out.get(miss) || []), b])
  }
  return [...out]
}

function Cases({ sv, flex }) {
  const ids = sv.rows.map((x) => x.id)
  const label = Object.fromEntries(sv.rows.map((x) => [x.id, x.label]))
  const n = sv.headline_campuses
  const money = sv.budget
  const every = sv.binds_every_case || []
  const early = sv.binds_early_every_case || []
  const most = (sv.binds_all_but_one || []).slice(0, ALL_BUT)
  const name = (b) => `${b.short}${b.rate_est ? ' (rating estimated)' : ''}`
  return (
    <>
      <div className="st-sure__scroll">
        <table className="st-sure__t">
          <caption className="st-sr">The always-on search under each set of assumptions</caption>
          <thead>
            <tr>
              <th scope="col">Assumption</th>
              <th scope="col">Today</th>
              {money > 0 && <th scope="col">With {shortMoney(money)}</th>}
              <th scope="col">Cost of {fmt(n)}</th>
            </tr>
          </thead>
          <tbody>
            {sv.rows.map((x) => {
              // a case that ran out of time: its counts are lower bounds and what it never reached is unknown, not "past it"
              const late = x.stop === 'time'
              const atLeast = (v) => (late && v === x.campuses ? `at least ${fmt(v)}` : fmt(v))
              return (
                <tr key={x.id} className={x.id === 'shipped' ? 'is-shipped' : undefined}>
                  <th scope="row">
                    {x.id === 'shipped' ? 'As shipped (the headline)' : x.label}
                    <span className="st-sure__d">{x.detail}</span>
                    {late && (
                      <span className="st-sure__d st-sure__late">
                        Ran out of time ({fmt(sv.time_s || 30)} s) after {count(x.campuses)} {x.campuses === 1 ? 'campus' : 'campuses'}: not a result past that.
                      </span>
                    )}
                  </th>
                  <td>{atLeast(x.today)}</td>
                  {money > 0 && (
                    <td>
                      {atLeast(x.budget.campuses)}
                      {x.budget.cost_high > 0 && x.budget.cost_high < money - 0.5 && <span className="st-sure__d">for {shortMoney(x.budget.cost_high)}</span>}
                    </td>
                  )}
                  <td>
                    {x.headline.cost_high == null ? (
                      <span className="st-sure__d">{late ? 'didn’t finish in time' : 'past the search'}</span>
                    ) : (
                      shortMoney(x.headline.cost_high)
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p>
        {every.length ? (
          <>
            Binds in every case: <strong>{every.map(name).join(', ')}</strong>.{' '}
          </>
        ) : (
          'No single line or transformer binds in every case. '
        )}
        {early.length
          ? `Early (for the first paid campuses) in every case: ${early.map(name).join(', ')}.`
          : 'None binds early in every case: which limit comes first depends on the assumptions.'}
      </p>
      {butOne(most, ids).map(([miss, list]) => (
        <p key={miss}>
          In every case but {miss === 'shipped' ? 'the shipped one' : label[miss]?.toLowerCase()}: {list.map(name).join(', ')}.
        </p>
      ))}
      <p className="st-sure__foot">
        The headline is the shipped case. Each case re-runs the always-on search on a copy of the model{flex ? ' (the flexible plan’s own cases aren’t run)' : ''}, with
        the same time limit{sv.time_s ? ` (${fmt(sv.time_s)} s)` : ''}; all of them took {fmt(Math.max(1, sv.seconds))} s. A line binds in a case when it stops a campus or
        the plan raises it.
        {sv.estimated_ratings > 0 ? ` ${fmt(sv.estimated_ratings)} ratings in this model are estimates the build step made (the dataset gives none).` : ''}
      </p>
    </>
  )
}
