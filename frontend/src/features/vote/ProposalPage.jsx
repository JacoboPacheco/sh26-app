import { useEffect, useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Button, ErrorBanner, Loading } from '../../ui'
import Analyst from './Analyst'
import { Questions, Speak } from './Ask'
import CommentWriter from './CommentWriter'
import Money from './Money'
import Safe from './Safe'
import Simulation from './Simulation'
import { briefHref, proposalHref, useProposal } from './voteApi'

// One proposal, top to bottom: the sourced facts, where it stands, the money, what it could do to a grid,
// what would have to be built, what to ask, where to speak. The order is the order a resident needs it in.

const MODEL_MAX_MW = 50000 // the engine's cap (backend MW_MAX)

const SECTIONS = [
  ['stands', 'Where it stands'],
  ['money', 'The money'],
  ['grid', 'What it could do'],
  ['built', 'What would have to be built'],
  ['take', 'What it would take (AI)'],
  ['ask', 'Ask before you vote'],
  ['comment', 'Write your comment'],
  ['speak', 'Where to speak'],
]

function jump(id) {
  const el = document.getElementById(`vote-${id}`)
  if (!el) return
  const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  el.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' })
}

// Open the main map with this campus placed at its reported size (up to the engine's cap).
function WatchButton({ entry, sim }) {
  const o = useOverload()
  const [msg, setMsg] = useState(null)
  if (!sim?.tested || entry.lat == null) return null
  const go = () => {
    const mw = Math.min(entry.mw || 0, MODEL_MAX_MW)
    if (!o) return
    if (o.region === entry.state) {
      o.setMode?.('campus')
      o.setMw(mw)
      o.place(entry.lat, entry.lon)
    } else if (!o.setRegion(entry.state, { place: [entry.lat, entry.lon], mw })) {
      setMsg(`The ${entry.state_name} model isn't available.`)
      return
    }
    window.location.hash = '#/'
  }
  return (
    <>
      <Button onClick={go} disabled={!o}>
        Watch what could happen
      </Button>
      {msg && (
        <span className="vote-note" role="alert">
          {msg}
        </span>
      )}
    </>
  )
}

const cap = (t) => (t ? t[0].toUpperCase() + t.slice(1) : t)

// "approximate: Town of X" / "approximate. Reported site…" -> the part after the word (the sentence already says approximate)
const placeBasis = (b) => String(b || '').replace(/^\s*approximate\b[\s:;,.-]*/i, '').trim()

function Head({ d }) {
  const e = d.entry
  const where = e.place_text || [e.city, e.county_text, e.state_name].filter(Boolean).join(', ')
  const basis = placeBasis(e.location_basis)
  return (
    <header className="vote-head">
      <p className="vote-crumb">
        <a href="#/vote">Proposed data centers</a> <span aria-hidden="true">/</span> {e.state_name}
      </p>
      <h1 tabIndex={-1} className="vote-h1">
        {e.name}
      </h1>
      <p className="vote-sub">
        {e.company &&
          (/^developer not confirmed/i.test(e.company) ? (
            <>
              <span className="vote-sub__k">Developer:</span> not confirmed in the sources ·{' '}
            </>
          ) : (
            <>
              <span className="vote-sub__k">Developer, as reported:</span> {e.company} ·{' '}
            </>
          ))}
        {where}
      </p>
      <dl className="vote-facts">
        <div>
          <dt>Reported size</dt>
          <dd className="vote-facts__big">{e.mw ? `${fmt(e.mw)} MW` : 'Not found'}</dd>
        </div>
        <div>
          <dt>Status, as reported</dt>
          <dd>{e.status_text}</dd>
        </div>
        {e.year && (
          <div>
            <dt>Timing, as reported</dt>
            <dd>{cap(String(e.year))}</dd>
          </div>
        )}
        <div>
          <dt>Confidence in these facts</dt>
          <dd>{e.confidence ? cap(e.confidence) : 'Unknown'}</dd>
        </div>
      </dl>
      {e.mw_basis && <p className="vote-note">About the size, compiled from the sources: {e.mw_basis}</p>}
      <p className="vote-reported">
        As reported by{' '}
        {e.sources.map((s, i) => (
          <span key={s.url}>
            {i > 0 && ', '}
            <a href={s.url} target="_blank" rel="noopener noreferrer">
              {s.label}
            </a>
          </span>
        ))}
        {e.sources.length === 0 && 'no source link was kept for this entry'}. Location is approximate{basis ? `: ${basis}` : '.'}
      </p>
      <p className="vote-frame">
        Overload tests a campus of this reported size at this reported location on a <strong>synthetic</strong> grid model. It is not a prediction about the real project, its owners or its utility.
      </p>
      <nav className="vote-jumps" aria-label="On this page">
        {SECTIONS.map(([id, label]) => (
          <button key={id} type="button" className="vote-chip" onClick={() => jump(id)}>
            {label}
          </button>
        ))}
      </nav>
    </header>
  )
}

function Section({ id, title, children }) {
  return (
    <section id={`vote-${id}`} className="vote-section" aria-labelledby={`vote-${id}-h`}>
      <h2 id={`vote-${id}-h`} className="vote-h2">
        {title}
      </h2>
      {children}
    </section>
  )
}

export default function ProposalPage({ id }) {
  const { data: d, error, loading, stalled, retry, recheck } = useProposal(id)

  // an id that is another listing of the same campus opens the listing that was kept
  useEffect(() => {
    if (d?.alias_of && d.entry.id !== id) window.history.replaceState(null, '', proposalHref(d.entry.id))
  }, [d, id])

  if (error)
    return (
      <div className="vote-doc">
        <p className="vote-crumb">
          <a href="#/vote">Proposed data centers</a>
        </p>
        <ErrorBanner error={error} onRetry={retry} />
      </div>
    )
  if (loading || !d)
    return (
      <div className="vote-doc">
        <Loading label="Looking up the proposal and testing it on the model…" />
      </div>
    )

  const { entry: e, civic, state, simulation: sim, cost, safe, questions } = d
  return (
    <article className="vote-doc">
      <Head d={d} />

      <Section id="stands" title="Where it stands">
        <p className="vote-body">
          {civic.status_note || (
            <>
              Status as reported by the sources above: {e.status_text.toLowerCase()}
              {e.year ? `; timing as reported: ${e.year}` : ''}.
            </>
          )}
        </p>
        {!civic.researched && <p className="vote-note">The local decision and any regulator cases were not researched for this proposal yet; the “Where to speak” section says how to find them.</p>}
        {civic.researched && civic.checked && <p className="vote-note">Civic facts last checked {civic.checked}.</p>}
      </Section>

      <Section id="money" title="The money, in estimates">
        <Money cost={cost} sim={sim} />
      </Section>

      <Section id="grid" title="What a campus this size could do to a grid">
        <Simulation sim={sim} mw={e.mw || 0} />
        <div className="vote-actions">
          <WatchButton entry={e} sim={sim} />
        </div>
      </Section>

      <Section id="built" title="What would have to be built for this to be safe">
        <Safe safe={safe} sim={sim} stalled={stalled} onRecheck={recheck} />
      </Section>

      <Section id="take" title="What it would take: the AI analyst">
        <Analyst key={e.id} entry={e} sim={sim} />
      </Section>

      <Section id="ask" title="Ask before you vote">
        <Questions questions={questions} state={state} />
      </Section>

      <Section id="comment" title="Write your public comment">
        <CommentWriter key={e.id} entry={e} />
      </Section>

      <Section id="speak" title="Where to speak">
        <Speak entry={e} civic={civic} state={state} />
      </Section>

      <section className="vote-section vote-final" aria-label="Take it with you">
        <div className="vote-actions">
          <a className="btn" href={briefHref(e.id)}>
            Print or save the one-page brief
          </a>
          <WatchButton entry={e} sim={sim} />
        </div>
        <p className="vote-note">{d.credit} Every figure is an estimate; the assumption behind each is one click away.</p>
      </section>
    </article>
  )
}
