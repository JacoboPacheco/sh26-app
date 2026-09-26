// "Ask before you vote": neutral questions with why they matter, what to listen for, and a source; then the
// state's own large-load rules where they were researched.

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
// '2026-05' -> 'May 2026' (anything else is shown as written)
const monthText = (d) => {
  const m = /^(\d{4})-(\d{2})$/.exec(d || '')
  return m && MONTHS[Number(m[2]) - 1] ? `${MONTHS[Number(m[2]) - 1]} ${m[1]}` : d
}

export function Questions({ questions, state }) {
  return (
    <div className="stack">
      <ol className="vote-questions">
        {questions.map((q) => (
          <li key={q.id}>
            <details>
              <summary>{q.question}</summary>
              <div className="vote-q__body">
                <p>
                  <strong>Why it matters.</strong> {q.why}
                </p>
                <p>
                  <strong>Listen for.</strong> {q.listen_for}
                </p>
                {q.sources?.length > 0 && (
                  <p className="vote-q__src">
                    Read more:{' '}
                    {q.sources.map((s, i) => (
                      <span key={s.url}>
                        {i > 0 && '; '}
                        <a href={s.url} target="_blank" rel="noopener noreferrer" title={s.supports || undefined}>
                          {s.title}
                        </a>
                      </span>
                    ))}
                  </p>
                )}
              </div>
            </details>
          </li>
        ))}
      </ol>

      <div className="vote-policy">
        <h3 className="vote-h3">What {state.name} has on the books for large loads</h3>
        {state.policy?.length > 0 ? (
          <ul className="vote-plain">
            {state.policy.map((p) => (
              <li key={p.title}>
                {p.url ? (
                  <a href={p.url} target="_blank" rel="noopener noreferrer">
                    {p.title}
                  </a>
                ) : (
                  p.title
                )}
                {p.date ? ` (${monthText(p.date)})` : ''}
                {p.summary ? `: ${p.summary}` : ''}
              </li>
            ))}
          </ul>
        ) : (
          <p className="vote-note">
            {state.researched
              ? 'No large-load or data-center rule was found for this state in the sources checked.'
              : 'Not researched yet. To find it, search the state utility commission’s site for “large load” or “data center” tariffs and proceedings.'}
          </p>
        )}
      </div>
    </div>
  )
}

function Link({ href, children }) {
  return href ? (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ) : (
    children
  )
}

export function Speak({ entry, civic, state }) {
  const b = civic.decision_body
  const who = entry.county_text || 'the county'
  const town = (entry.city || '').replace(/\s*\([^)]*\)\s*$/, '') // "Lockhart (Caldwell County)" -> "Lockhart"
  return (
    <div className="vote-speak">
      <div>
        <h3 className="vote-h3">The local decision</h3>
        {b ? (
          <>
            <p>
              <Link href={b.url}>{b.name}</Link>
            </p>
            {b.comment_url && (
              <p>
                <Link href={b.comment_url}>How to give public comment</Link>
              </p>
            )}
            {b.how_to_comment && <p className="vote-note">How to comment: {b.how_to_comment}</p>}
          </>
        ) : (
          <p className="vote-note">
            Not researched yet for this proposal. To find it: ask the {who} clerk{town ? ` (or the clerk in ${town})` : ''} which board hears it, and for the meeting date, the agenda and how to sign up to speak.
          </p>
        )}
        {civic.municipality || civic.county ? (
          <p className="vote-note">{[civic.municipality, civic.county].filter(Boolean).join(' · ')}</p>
        ) : null}
        {civic.utility && (
          <p className="vote-note">
            Utility named in the sources: <Link href={civic.utility.source}>{civic.utility.name}</Link>
          </p>
        )}
      </div>

      <div>
        <h3 className="vote-h3">The state utility regulator</h3>
        {state.commission ? (
          <>
            <p>
              <Link href={state.url}>{state.commission}</Link>
            </p>
            {state.comment_url && (
              <p>
                <Link href={state.comment_url}>How to comment or search its dockets</Link>
              </p>
            )}
            {state.note && <p className="vote-note">{state.note}</p>}
          </>
        ) : (
          <p className="vote-note">
            Not researched yet for {entry.state_name}. To find it: search for the state’s public service (or utilities) commission and open its docket search.
          </p>
        )}
        {civic.cases.length > 0 && (
          <>
            <h4 className="vote-h4">Cases that may matter</h4>
            <ul className="vote-plain">
              {civic.cases.map((c, i) => (
                <li key={`${c.number}-${i}`}>
                  <Link href={c.url}>{[c.commission, c.number, c.title].filter(Boolean).join(' ')}</Link>
                  {c.why ? `: ${c.why}` : ''}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}
