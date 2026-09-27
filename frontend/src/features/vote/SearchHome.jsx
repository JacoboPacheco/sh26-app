import { useEffect, useRef, useState } from 'react'
import { fmt } from '../../geo'
import { EmptyState, ErrorBanner, Field, Loading } from '../../ui'
import { parseRoute, proposalHref, searchHash, searchProposals } from './voteApi'

// The start of "Before the vote": find a real proposed data center (or a place), then open its page.

function Row({ p }) {
  const where = p.place_text || [p.city, p.county_text, p.state_name].filter(Boolean).join(', ')
  return (
    <li>
      <a className="vote-row" href={proposalHref(p.id)}>
        <span className="vote-row__main">
          <span className="vote-row__name">{p.name}</span>
          <span className="vote-row__sub">
            {p.company ? `${p.company} · ` : ''}
            {where}
          </span>
        </span>
        <span className="vote-row__facts">
          <span className="vote-row__mw">{p.size_text || (p.mw ? `${fmt(p.mw)} MW` : 'Size not found')}</span>
          <span className={`vote-row__status${p.status === 'paused/canceled' ? ' vote-row__status--quiet' : ''}`}>{p.status_text}</span>
        </span>
      </a>
    </li>
  )
}

export default function SearchHome({ initialQ, initialState }) {
  const [q, setQ] = useState(initialQ)
  const [state, setState] = useState(initialState)
  const [res, setRes] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(true)
  const [nonce, setNonce] = useState(0)
  const seq = useRef(0)

  // search as you type (after a short pause); the address keeps the search so a link or Back returns to it
  useEffect(() => {
    const mine = ++seq.current
    setBusy(true)
    const t = window.setTimeout(
      () => {
        searchProposals({ q: q.trim(), state, limit: 30 })
          .then((r) => {
            if (seq.current !== mine) return
            setRes(r)
            setError(null)
            setBusy(false)
          })
          .catch((e) => {
            if (seq.current !== mine) return
            setError(e)
            setBusy(false)
          })
      },
      res ? 220 : 0,
    )
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, state, nonce])

  useEffect(() => {
    const h = searchHash(q.trim(), state)
    if (window.location.hash !== h) window.history.replaceState(null, '', h)
  }, [q, state])

  // a link to another search while this one is open (the Proposed data centers tab, Back) changes the search too;
  // typing only replaces the address, which fires no hashchange
  useEffect(() => {
    const f = () => {
      const r = parseRoute(window.location.hash)
      if (r.page !== 'home') return
      setQ(r.q)
      setState(r.state)
    }
    window.addEventListener('hashchange', f)
    return () => window.removeEventListener('hashchange', f)
  }, [])

  const rows = res?.proposals || []
  const featured = !q.trim() && !state ? rows.filter((r) => r.featured) : []
  const rest = !q.trim() && !state ? rows.filter((r) => !r.featured) : rows
  const stateName = state ? res?.states?.find((s) => s.code === state)?.name || state : ''

  return (
    <div className="vote-home">
      <h1 tabIndex={-1} className="vote-h1">
        Proposed data centers
      </h1>
      <p className="vote-lead">
        A data center is on the agenda. Look up the proposal, see what a campus of that size could do to a power grid and what it could cost, get the questions to ask before anyone approves it, and find where to speak.
      </p>

      <form className="vote-search" role="search" onSubmit={(e) => e.preventDefault()}>
        <Field
          label="Find a proposed data center or a place"
          hint="A project name, developer, town, county or state"
          type="search"
          value={q}
          maxLength={80}
          autoComplete="off"
          placeholder="Fort Meade, Polk, Palm Beach, a company…"
          onChange={(e) => setQ(e.target.value)}
        />
      </form>

      {res && (state || res.states.length > 0) && (
        <div className="vote-states" role="group" aria-label="Browse by state">
          <span className="vote-states__label">By state</span>
          {state && (
            <button type="button" className="vote-chip vote-chip--on" aria-pressed="true" aria-label={`${stateName}: show every state`} onClick={() => setState('')}>
              {stateName} <span aria-hidden="true">✕</span>
            </button>
          )}
          {!state &&
            res.states.slice(0, 12).map((s) => (
              <button key={s.code} type="button" className="vote-chip" aria-pressed="false" onClick={() => setState(s.code)}>
                {s.name} <span className="vote-chip__n">{s.count}</span>
              </button>
            ))}
        </div>
      )}

      <div aria-live="polite" className="vote-count">
        {res && !busy && (q.trim() || state ? `${res.total} ${res.total === 1 ? 'proposal' : 'proposals'}${stateName ? ` in ${stateName}` : ''}` : '')}
      </div>

      <ErrorBanner error={error} onRetry={() => setNonce((n) => n + 1)} />
      {!res && !error && <Loading label="Loading proposals…" />}

      {featured.length > 0 && (
        <section aria-labelledby="vote-featured">
          <h2 id="vote-featured" className="vote-h2">
            Real proposals in Florida
          </h2>
          <p className="vote-note">Each was reported in the news or by the company, at the size shown. The page for each says where the facts come from.</p>
          <ul className="vote-list">
            {featured.map((p) => (
              <Row key={p.id} p={p} />
            ))}
          </ul>
        </section>
      )}

      {rest.length > 0 && (
        <section aria-labelledby="vote-rest">
          <h2 id="vote-rest" className="vote-h2">
            {featured.length ? 'The largest others across the U.S.' : 'Results'}
          </h2>
          <ul className="vote-list">
            {rest.map((p) => (
              <Row key={p.id} p={p} />
            ))}
          </ul>
        </section>
      )}

      {res && !busy && rows.length === 0 && (
        <EmptyState title="No proposal matches that.">
          The list covers large campuses that were reported as built, under construction, announced or paused. A place with none listed may still have one that is not here. Try a county or a state name, or a shorter word.
        </EmptyState>
      )}

      <p className="vote-note vote-foot">
        Every simulation on these pages runs on a synthetic grid model (Breakthrough Energy / Texas A&amp;M, CC-BY 4.0): a campus of the reported size at the reported location, not a prediction about the real project or its utility.
      </p>
    </div>
  )
}
