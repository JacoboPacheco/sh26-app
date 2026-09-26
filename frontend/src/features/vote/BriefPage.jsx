import { Button, ErrorBanner, Loading } from '../../ui'
import { briefToMarkdown, downloadText } from './brief'
import { proposalHref, useProposal } from './voteApi'

// The one-page brief: black on white, Letter, made to be printed or saved as a PDF. It renders
// proposal.brief, the same object the text download is built from.

function Item({ it }) {
  return (
    <li>
      {it.url ? (
        <a href={it.url} target="_blank" rel="noopener noreferrer">
          {it.text}
        </a>
      ) : (
        it.text
      )}
      {it.detail && <span className="brief-detail"> {it.detail}</span>}
    </li>
  )
}

export default function BriefPage({ id }) {
  const { data: d, error, loading, retry } = useProposal(id)
  if (error)
    return (
      <div className="vote-doc">
        <ErrorBanner error={error} onRetry={retry} />
      </div>
    )
  if (loading || !d)
    return (
      <div className="vote-doc">
        <Loading label="Preparing the brief…" />
      </div>
    )
  const b = d.brief
  return (
    <div className="brief-wrap">
      <div className="brief-bar">
        <a href={proposalHref(d.entry.id)}>← Back to the proposal</a>
        <div className="vote-actions">
          <Button onClick={() => window.print()}>Print or save as PDF</Button>
          <Button variant="secondary" onClick={() => downloadText(`before-the-vote-${d.entry.id}.md`, briefToMarkdown(b))}>
            Download as text
          </Button>
        </div>
      </div>

      <article className="brief-paper" aria-label="One-page brief">
        <header>
          <h1 tabIndex={-1} className="brief-title">
            {b.title}
          </h1>
          {b.subtitle && <p className="brief-sub">{b.subtitle}</p>}
          <p className="brief-meta">Prepared {b.generated} with Overload · {b.credit}</p>
        </header>
        {b.sections.map((s, n) => (
          <section key={s.heading} className={`brief-section${n === 0 ? ' brief-section--facts' : ''}`}>
            <h2>{s.heading}</h2>
            {s.paragraphs?.map((p, i) => (
              <p key={i}>{p}</p>
            ))}
            {s.items && (
              <ul>
                {s.items.map((it, i) => (
                  <Item key={i} it={it} />
                ))}
              </ul>
            )}
            {s.note && <p className="brief-note">{s.note}</p>}
          </section>
        ))}
        {b.sources?.length > 0 && (
          <section className="brief-section brief-sources">
            <h2>Sources</h2>
            <ul>
              {b.sources.map((s) => (
                <li key={s.url}>
                  {s.title}: <a href={s.url}>{s.url}</a>
                </li>
              ))}
            </ul>
          </section>
        )}
        <p className="brief-disclaimer">{b.disclaimer}</p>
      </article>
    </div>
  )
}
