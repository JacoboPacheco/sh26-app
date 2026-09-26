import { useEffect, useMemo, useState } from 'react'
import { STATES, fmt } from '../../geo'
import { SEED_MARK, useOverload } from '../../store'
import { Button, EmptyState, ErrorBanner, Field, Loading } from '../../ui'
import { caseOf, fetchScenario, openCase, resultOf } from './caseOps'
import { Optional, hasModule } from './optional'
import { go, href } from './router'
import { scenarioSentence } from './sentence'

// The pages without a map: the library (saved scenarios), compare (two side by side), a brief (one
// scenario as a page you can share), and not-found. The library track's LibraryPage replaces the
// simple list here when it lands.

const VERDICT = { holds: 'Holds', trips: 'Lines trip, lights stay on', blackout: 'Blackout' }
const tone = (v) => (v === 'blackout' ? 'bad' : v === 'trips' ? 'warn' : v === 'holds' ? 'ok' : 'idle')

function DocHead({ eyebrow, title, children }) {
  return (
    <header className="nx-dochead">
      {eyebrow && <p className="nx-eyebrow">{eyebrow}</p>}
      <h1 className="nx-h1">{title}</h1>
      {children && <div className="nx-dochead__actions">{children}</div>}
    </header>
  )
}

// ------------------------------------------------------------------ library
export function LibraryPage() {
  return (
    <div className="nx-docwrap">
      <DocHead eyebrow="Your scenarios" title="Library">
        <a className="btn btn--secondary" href={href('/compare')}>
          Compare two
        </a>
        <a className="btn" href={href('/state/FL')}>
          New scenario
        </a>
      </DocHead>
      <Optional from="library/LibraryPage" fallback={<LibraryList />} loading={<Loading label="Loading the library…" />} />
    </div>
  )
}

function LibraryList() {
  const o = useOverload()
  const { scenarios, scenarioError, loadScenarios, user } = o
  const [pick, setPick] = useState([])
  if (user === undefined) return <Loading label="Signing in to the demo account…" />
  if (user === null) return <EmptyState title="Not signed in">The demo account could not sign in, so the library can&apos;t load. Reload the page to try again.</EmptyState>
  if (scenarioError) return <ErrorBanner error={scenarioError} onRetry={loadScenarios} />
  if (scenarios === undefined) return <Loading label="Loading the library…" />
  if (!scenarios.length) return <EmptyState title="No saved scenarios yet">Open a state, build a scenario, and save it.</EmptyState>
  const toggle = (id) => setPick((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p.slice(-1), id]))
  return (
    <div className="stack">
      <ul className="nx-lib">
        {scenarios.map((sc) => {
          const r = sc.result
          const c = caseOf(sc)
          return (
            <li key={sc.id} className="nx-lib__item">
              <div className="nx-lib__main">
                <p className="nx-lib__name">
                  {sc.name} {String(sc.note || '').includes(SEED_MARK) && <span className="nx-tag">Example</span>}
                </p>
                <p className="muted nx-small">
                  {r?.sentence || (c ? `${STATES[c.region || 'FL']?.name || c.region} · ${c.mw ? `${fmt(c.mw)} MW` : 'no campus'}` : '')}
                </p>
                {r && (
                  <p className="nx-small">
                    <span className={`nx-tag nx-tag--${tone(r.verdict)}`}>{VERDICT[r.verdict] || r.verdict}</span> {fmt(r.people_peak ?? r.people ?? 0)} people at the
                    worst (estimate) · {fmt(r.steps || 0)} steps
                  </p>
                )}
              </div>
              <div className="nx-lib__actions">
                <label className="nx-check">
                  <input type="checkbox" checked={pick.includes(sc.id)} onChange={() => toggle(sc.id)} />
                  Compare
                </label>
                <a className="btn btn--secondary" href={href(`/brief/${sc.id}`)}>
                  Brief
                </a>
                <Button
                  onClick={() => {
                    if (!c) return
                    const code = openCase(o, c)
                    go(`/state/${code}`)
                  }}
                  disabled={!c}
                >
                  Open
                </Button>
              </div>
            </li>
          )
        })}
      </ul>
      <div className="row">
        <Button variant="secondary" disabled={pick.length !== 2} onClick={() => go(`/compare/${pick[0]}/${pick[1]}`)}>
          Compare the two picked
        </Button>
        <span className="muted nx-small">{pick.length === 2 ? 'Ready.' : `Pick ${2 - pick.length} more to compare.`}</span>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ one scenario, loaded and solved
// A saved scenario (a numeric id) belongs to the demo account, so it waits for the sign-in on load:
// asked for before the token exists, the backend answers 401 and api.js signs the whole app out.
// A share slug is public and loads at once.
function useScenario(id) {
  const { user } = useOverload()
  const own = !!id && /^\d+$/.test(String(id))
  const signing = own && user === undefined
  const signedOut = own && user === null
  const [state, setState] = useState({ status: id ? 'loading' : 'none' })
  useEffect(() => {
    if (!id) {
      setState({ status: 'none' })
      return undefined
    }
    if (signing) {
      setState({ status: 'loading' })
      return undefined
    }
    if (signedOut) {
      setState({ status: 'error', error: new Error('The demo account could not sign in, so saved scenarios can’t load. Reload the page to try again.') })
      return undefined
    }
    let live = true
    setState({ status: 'loading' })
    fetchScenario(id)
      .then(async (sc) => {
        const result = await resultOf(sc)
        if (live) setState({ status: 'ready', sc, result })
      })
      .catch((error) => live && setState({ status: 'error', error }))
    return () => {
      live = false
    }
  }, [id, signing, signedOut])
  return state
}

function sentenceOf(sc, r) {
  if (r?.sentence) return r.sentence
  const c = caseOf(sc)
  if (!c) return ''
  return (
    scenarioSentence({
      regionName: STATES[c.region || 'FL']?.name,
      site: c.lat != null ? { lat: c.lat, lon: c.lon } : null,
      mw: c.mw,
      area: r?.sub_area,
      extraSites: c.sites || [],
      loadFactor: c.load_factor ?? 1,
      trip: c.trip || [],
      upgrades: c.upgrades || {},
      firm: c.firm,
    }) || ''
  )
}

// ------------------------------------------------------------------ compare
const ROWS = [
  ['Verdict', (r) => VERDICT[r.verdict] || '—', null],
  ['People without power at the end (estimate)', (r) => fmt(r.people || 0), (r) => r.people || 0],
  ['At the worst moment (estimate)', (r) => fmt(r.people_peak ?? r.people ?? 0), (r) => r.people_peak ?? r.people ?? 0],
  ['Cascade steps', (r) => fmt(r.steps || 0), (r) => r.steps || 0],
  ['Load lost (MW)', (r) => fmt(r.lost_mw || 0), (r) => r.lost_mw || 0],
  ['Room at the main site (MW)', (r) => (r.headroom_mw != null ? fmt(r.headroom_mw) : '—'), null],
  ['Campus cut off', (r) => (r.site_cut_off ? 'Yes' : 'No'), null],
  ['Service', (r) => (r.firm ? 'Firm' : 'Flexible'), null],
  ['Areas hit hardest', (r) => (r.areas_top?.length ? r.areas_top.map((a) => a.area).join(', ') : '—'), null],
]

export function ComparePage({ a: rawA, b: rawB }) {
  const o = useOverload()
  const a = rawA && rawA !== '_' ? rawA : null // '_' holds an empty slot in the address
  const b = rawB && rawB !== '_' ? rawB : null
  const A = useScenario(a)
  const B = useScenario(b)
  const list = o.scenarios || []
  const choose = (which) => (e) => {
    const v = e.target.value || '_'
    go(`/compare/${which === 'a' ? v : a || '_'}/${which === 'b' ? v : b || '_'}`)
  }
  const ready = A.status === 'ready' && B.status === 'ready'
  return (
    <div className="nx-docwrap">
      <DocHead eyebrow="Two scenarios side by side" title="Compare">
        <a className="btn btn--secondary" href={href('/library')}>
          Back to the library
        </a>
      </DocHead>
      <div className="nx-cmp__pick">
        <Field as="select" label="Scenario A" value={a || ''} onChange={choose('a')}>
          <option value="">Choose…</option>
          {list.map((sc) => (
            <option key={sc.id} value={sc.id}>
              {sc.name}
            </option>
          ))}
        </Field>
        <Field as="select" label="Scenario B" value={b || ''} onChange={choose('b')}>
          <option value="">Choose…</option>
          {list.map((sc) => (
            <option key={sc.id} value={sc.id}>
              {sc.name}
            </option>
          ))}
        </Field>
      </div>
      {o.scenarioError && <ErrorBanner error={o.scenarioError} onRetry={o.loadScenarios} />}
      {[A, B].some((s) => s.status === 'loading') && <Loading label="Solving both scenarios on the synthetic model…" />}
      {[A, B].map((s, i) => s.status === 'error' && <ErrorBanner key={i} error={s.error} />)}
      {(A.status === 'none' || B.status === 'none') && <EmptyState title="Pick two scenarios">Choose A and B above, or tick two in the library.</EmptyState>}
      {ready && (
        <>
          <div className="nx-tablewrap">
            <table className="nx-cmp">
              <thead>
                <tr>
                  <th scope="col">
                    <span className="sr-only">Measure</span>
                  </th>
                  <th scope="col">A: {A.sc.name}</th>
                  <th scope="col">B: {B.sc.name}</th>
                  <th scope="col">B minus A</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">The scenario</th>
                  <td>{sentenceOf(A.sc, A.result)}</td>
                  <td>{sentenceOf(B.sc, B.result)}</td>
                  <td />
                </tr>
                {ROWS.map(([label, show, num]) => {
                  const d = num ? num(B.result) - num(A.result) : null
                  return (
                    <tr key={label}>
                      <th scope="row">{label}</th>
                      <td>{show(A.result)}</td>
                      <td>{show(B.result)}</td>
                      <td className={d > 0 ? 'nx-up' : d < 0 ? 'nx-down' : undefined}>{d === null ? '' : d === 0 ? 'same' : `${d > 0 ? '+' : '−'}${fmt(Math.abs(d))}`}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="nx-fine">Both solved on synthetic grid models; people are estimates (lost load&apos;s share of the state model&apos;s load × Census population).</p>
          <div className="row">
            {[A, B].map((s, i) => (
              <Button
                key={i}
                variant="secondary"
                onClick={() => {
                  const c = caseOf(s.sc)
                  if (c) go(`/state/${openCase(o, c)}`)
                }}
              >
                Open {i ? 'B' : 'A'} in the workspace
              </Button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ brief
export function BriefPage({ id }) {
  const o = useOverload()
  const s = useScenario(id)
  const [copied, setCopied] = useState(false)
  const r = s.result
  const why = useMemo(() => (r ? briefWhy(r) : null), [r])
  if (s.status === 'loading') return <div className="nx-docwrap"><Loading label="Solving this scenario on the synthetic model…" /></div>
  if (s.status === 'error')
    return (
      <div className="nx-docwrap">
        <DocHead eyebrow="Brief" title="This brief isn't available" />
        <ErrorBanner error={s.error} />
        <a className="nx-link" href={href('/library')}>
          Go to the library
        </a>
      </div>
    )
  if (s.status !== 'ready') return null
  const sc = s.sc
  const c = caseOf(sc)
  const regionName = STATES[c?.region || 'FL']?.name
  return (
    <article className="nx-docwrap nx-brief">
      <DocHead eyebrow={`Brief · ${regionName || ''} · synthetic grid model`} title={sc.name}>
        <Button
          variant="secondary"
          onClick={() => {
            navigator.clipboard?.writeText(window.location.href).then(
              () => setCopied(true),
              () => setCopied(false),
            )
          }}
        >
          {copied ? 'Link copied' : 'Copy link'}
        </Button>
        {hasModule('briefing/BriefRoute') && /^\d+$/.test(String(id)) && (
          <a className="btn btn--secondary" href={href(`/brief/${encodeURIComponent(id)}`)}>
            Play the briefing
          </a>
        )}
        {c && (
          <Button onClick={() => go(`/state/${openCase(o, c)}`)} disabled={!o.user && !sc.shared}>
            Open in the workspace
          </Button>
        )}
      </DocHead>
      <p className="nx-brief__q">{sentenceOf(sc, r)}</p>
      <section className={`nx-verdict nx-verdict--${tone(r.verdict)}`}>
        <p className="nx-verdict__k">What happened</p>
        <p className="nx-verdict__label">{VERDICT[r.verdict] || 'Result'}</p>
        <p className="nx-verdict__s">
          {r.verdict === 'holds'
            ? 'No line goes over its limit and no one loses power.'
            : r.verdict === 'trips'
              ? `Lines trip over ${fmt(r.steps || 0)} steps, but every light stays on.`
              : `The cascade runs ${fmt(r.steps || 0)} steps. About ${fmt(r.people_peak ?? r.people)} people lose power at the worst moment, ${fmt(r.people)} at the end (estimates).`}
        </p>
      </section>
      <dl className="nx-kpis nx-kpis--wide">
        <div>
          <dt>People without power, worst (estimate)</dt>
          <dd>{fmt(r.people_peak ?? r.people ?? 0)}</dd>
        </div>
        <div>
          <dt>At the end (estimate)</dt>
          <dd>{fmt(r.people || 0)}</dd>
        </div>
        <div>
          <dt>Load lost</dt>
          <dd>{fmt(r.lost_mw || 0)} MW</dd>
        </div>
        <div>
          <dt>Cascade steps</dt>
          <dd>{fmt(r.steps || 0)}</dd>
        </div>
      </dl>
      {why && (
        <section className="nx-why-card">
          <h2 className="nx-h3">{why.title}</h2>
          <p>{why.sentence}</p>
        </section>
      )}
      {r.areas_top?.length > 0 && (
        <section className="stack">
          <h2 className="nx-h2">Areas hit hardest</h2>
          <ol className="nx-areas">
            {r.areas_top.map((x) => (
              <li key={x.area}>
                <span>{x.area}</span>
                <span className="muted">about {fmt(roundAbout(x.people))} people (estimate)</span>
              </li>
            ))}
          </ol>
        </section>
      )}
      <Optional from="briefing/BriefingSummary" scenario={sc} result={r} loading={null} />
      <section className="stack nx-assume">
        <h2 className="nx-h2">How this was computed</h2>
        <ul>
          <li>The grid is a synthetic model (Breakthrough Energy Sciences U.S. Test System from Texas A&amp;M ACTIVSg grids, CC-BY 4.0), not any utility&apos;s network.</li>
          <li>A DC power flow finds every line&apos;s loading; the cascade trips the most overloaded line, re-solves, and repeats until the grid settles or splits.</li>
          <li>People without power = the lost load&apos;s share of the state model&apos;s load × the state&apos;s Census 2024 population. An estimate.</li>
          <li>Flexible service lets the campus be cut off; firm service holds its lines and cuts other customers instead.</li>
          {r.live && <li>Solved just now: this scenario had no saved result.</li>}
        </ul>
      </section>
    </article>
  )
}

// "about 329,782" reads as more precise than an estimate is: keep three significant figures
const roundAbout = (n) => {
  const v = Number(n) || 0
  if (v < 1000) return Math.round(v)
  const p = 10 ** (Math.floor(Math.log10(v)) - 2)
  return Math.round(v / p) * p
}

function briefWhy(r) {
  if (!(r.mw_total > 0) || !r.steps) return null
  if (!r.firm && r.site_cut_off)
    return {
      title: 'The data center was cut off',
      sentence:
        "The lines feeding the campus tripped during the cascade, so it stopped drawing power. Above the site's room, a bigger campus usually trips the same lines and blacks out about the same people.",
    }
  if (r.firm && r.firm_held)
    return { title: 'Kept on: firm service', sentence: `The operator held the campus's lines and cut ${fmt(r.shed_mw || 0)} MW of other customers instead.` }
  if (r.firm && r.firm_held === false)
    return { title: 'Firm service couldn’t hold', sentence: `The operator cut ${fmt(r.shed_mw || 0)} MW of other customers first, but the campus's own lines still tripped.` }
  return null
}

// ------------------------------------------------------------------ not found
export function MissingPage() {
  return (
    <div className="nx-docwrap">
      <DocHead eyebrow="Not found" title="There's no page here" />
      <p className="muted">The address may be out of date.</p>
      <div className="row">
        <a className="btn" href={href()}>
          Go to the map of America
        </a>
        <a className="btn btn--secondary" href={href('/library')}>
          Open the library
        </a>
      </div>
    </div>
  )
}
