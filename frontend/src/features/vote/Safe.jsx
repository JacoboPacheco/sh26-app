import { fmt } from '../../geo'

// "What would have to be built for this to be safe": the engine's ranked plans for the reported size, each
// re-run by the engine before it is listed. GREEN marks a plan that holds. Costs are estimates.

function Plan({ p, open }) {
  const share = p.full_size ? 'Keeps the full planned size' : `Keeps ${fmt(p.kept_mw)} MW (${fmt(p.kept_pct)}% of the planned size)`
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

export default function Safe({ safe, sim }) {
  if (!safe) return null
  if (safe.status === 'unavailable')
    return <p className="vote-note">Not worked out for this proposal. {safe.reason}</p>
  const running = safe.agentic?.status === 'running'
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
      <p className="vote-note" role="status" aria-live="polite">
        {running
          ? 'An AI is proposing more plans; the engine re-runs each one before it is added here…'
          : safe.agentic?.added > 0
            ? `${safe.agentic.added} ${safe.agentic.added === 1 ? 'plan above was' : 'plans above were'} proposed by an AI and re-run by the engine.`
            : ''}
      </p>
      {safe.status === 'solutions' && sim?.tested && safe.solutions[0]?.full_size && (
        <p className="vote-note">
          The first plan builds the campus at its full reported size. Who pays for the grid work is the question a filed tariff answers (the second question below).
        </p>
      )}
      <p className="vote-note vote-note__frame">{safe.note}</p>
    </div>
  )
}
