import { fmt } from '../../geo'
import { AgentTraceToggle } from '../ai/AgentTrace'
import AiBadge from '../ai/AiBadge'

// "What would have to be built for this to be safe": the engine's ranked plans for the reported size, each
// re-run by the engine before it is listed. GREEN marks a plan that holds. Costs are estimates.

// "…and verified 2; one is listed above." (the ones that held but cost more than a listed plan are not added)
function verdictLine(verified, added) {
  if (verified === 0) return 'none held.'
  if (added === 0) return `verified ${verified}. None beat the plans already listed.`
  if (added === verified) return `verified ${verified}: ${verified === 1 ? 'it is' : 'all are'} listed above.`
  return `verified ${verified}; ${added === 1 ? 'one is' : `${added} are`} listed above.`
}

function shareText(p) {
  if (p.family === 'onsite' && p.onsite_mw != null)
    return `Keeps the full planned size: ${fmt(p.onsite_mw)} MW generated on site, ${fmt(p.grid_mw ?? p.kept_mw)} MW from the grid`
  if (p.family === 'flexible' && p.kept_mw != null) return `Full size most of the year; down to ${fmt(p.kept_mw)} MW at the peak hour`
  if (p.full_size) return p.family === 'move' ? 'Keeps the full planned size, at another site on the model' : 'Keeps the full planned size'
  return `Keeps ${fmt(p.kept_mw)} MW (${fmt(p.kept_pct)}% of the planned size)`
}

function Plan({ p, open }) {
  const share = shareText(p)
  return (
    <li className="vote-plan">
      <details open={open}>
        <summary>
          <span className="vote-plan__rank" aria-hidden="true">
            {p.rank}.
          </span>
          <span className="vote-plan__title">{p.title}</span>
          <span className="vote-plan__cost">{p.cost ? `${p.cost.range} (estimate)` : 'No grid build'}</span>
        </summary>
        <div className="vote-plan__body">
          <p className="vote-plan__meta">
            <span className="vote-ok">{p.by === 'gemini' ? 'Proposed by AI, checked by the engine: holds' : 'Checked by the engine: holds'}</span> · {share}
          </p>
          {p.why && <p className="vote-plan__why">{p.why}</p>}
          <ul className="vote-plain">
            {p.steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      </details>
    </li>
  )
}

export default function Safe({ safe, sim, stalled, onRecheck }) {
  if (!safe) return null
  if (safe.status === 'unavailable')
    return <p className="vote-note">Not worked out for this proposal. {safe.reason}</p>
  const ag = safe.agentic
  const running = ag?.status === 'running'
  // the trace is worth opening only once Gemini proposed something (or while it runs): a run it never answered is said above
  const traced = ag?.trace?.length > 0 && (running || (ag.asked ?? 1) > 0)
  return (
    <div className="stack">
      <p className="vote-simhead">{safe.headline}</p>
      {safe.solutions.length > 0 && (
        <ol className="vote-plans">
          {safe.solutions.map((p, i) => (
            <Plan key={`${p.family}-${p.rank}-${p.title}`} p={p} open={i === 0} />
          ))}
        </ol>
      )}
      <p className="vote-note vote-agnote" role="status" aria-live="polite">
        {running && stalled ? (
          <>
            <AiBadge by="gemini">working</AiBadge> Gemini is still proposing plans; the plans above are already verified.{' '}
            <button type="button" className="vote-linkbtn" onClick={onRecheck}>
              Check for new plans
            </button>
          </>
        ) : running ? (
          <>
            <AiBadge by="gemini">working</AiBadge> Gemini is proposing more plans; the engine re-runs each one before it is added here…
          </>
        ) : ag?.status === 'done' && ag.asked > 0 ? (
          <>
            <AiBadge by="gemini" verified /> Gemini proposed {ag.asked} {ag.asked === 1 ? 'plan' : 'plans'} in {ag.rounds || 1} {(ag.rounds || 1) === 1 ? 'round' : 'rounds'}; the engine re-ran
            each one{ag.rounds > 1 ? ', sent back what still failed,' : ''} and {verdictLine(ag.verified || 0, ag.added || 0)}
          </>
        ) : ag?.status === 'done' && safe.solutions.length > 0 ? (
          <>
            <AiBadge by="fallback" why="Gemini did not answer" /> The plans above are the engine&apos;s own.
          </>
        ) : ag && ag.configured === false && safe.solutions.length > 0 ? (
          <>
            <AiBadge by="fallback" why="Gemini not configured" /> The plans above are the engine&apos;s own.
          </>
        ) : (
          ''
        )}
      </p>
      {traced && (
        <AgentTraceToggle
          trace={ag.trace}
          label={running ? 'Watch the AI work (live)' : 'Watch the AI work: every plan Gemini proposed and what the engine found'}
          heading={false}
          live={running}
          stepMs={420}
          follow
          className="vote-agt"
        />
      )}
      {safe.status === 'solutions' && sim?.tested && (safe.solutions[0]?.grid_here ?? false) && (
        <p className="vote-note">
          The first plan builds the campus at its full reported size at this site. Who pays for the grid work is the question a filed tariff answers (the second question below).
        </p>
      )}
      <p className="vote-note vote-note__frame">{safe.note}</p>
    </div>
  )
}
