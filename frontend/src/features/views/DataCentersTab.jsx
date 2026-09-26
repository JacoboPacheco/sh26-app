import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api'
import { ErrorBanner, Field } from '../../ui'
import { Swatch } from './charts'
import SiteCard from './SiteCard'
import UsMap, { MapLegend } from './UsMap'
import MapLink from './MapLink'
import { DEFAULT_STATUSES, STATUS_LABEL, STATUS_ORDER, fmt, fmt1, mwText } from './viewsKit'

// Data centers: where the reported sites are, filterable by state, company, status and size. The totals are
// the sites' reported MW (as reported, per source) and, as a scale only, the share of each state model's load.
// Nothing here says a company or a site causes an outage: the grid models contain no real campus.

const NON_CONUS = new Set(['AK', 'HI', 'PR', 'GU', 'VI', 'MP'])
const SIZES = [
  { v: 0, label: 'Any size' },
  { v: 50, label: '50 MW and up' },
  { v: 100, label: '100 MW and up' },
  { v: 500, label: '500 MW and up' },
  { v: 1000, label: '1,000 MW and up' },
]

const parseHashFilters = () => {
  const qs = window.location.hash.split('?')[1] || ''
  const p = new URLSearchParams(qs)
  return {
    q: p.get('q') || '',
    states: p.getAll('state').flatMap((s) => s.split(',')).map((s) => s.trim().toUpperCase()).filter(Boolean),
    companies: p.getAll('company').filter(Boolean),
    statuses: DEFAULT_STATUSES,
    minMw: 0,
    crypto: false,
  }
}

// Company chips read in one unit: 9,617 MW sits beside 10 GW as 9.6 GW
const chipMw = (mw) => (mw >= 1000 ? `${fmt1(mw / 1000)} GW` : `${fmt(mw)} MW`)

// D.C., the territories and sites with no state are counted with the states
const NOT_STATES = new Set(['DC', 'PR', 'GU', 'VI', 'MP', 'AS'])
const placesWord = (codes, n) => (n === 1 ? 'state' : codes.some((c) => NOT_STATES.has(c)) ? 'states and territories' : 'states')

function buildQuery(f, q) {
  const p = new URLSearchParams()
  f.states.forEach((s) => p.append('state', s))
  f.companies.forEach((c) => p.append('company', c))
  f.statuses.forEach((s) => p.append('status', s))
  if (f.minMw) p.set('min_mw', String(f.minMw))
  if (q.trim()) p.set('q', q.trim())
  p.set('kind', f.crypto ? 'all' : 'data_center')
  p.set('limit', '3000')
  return p.toString()
}

export default function DataCentersTab({ initialSite }) {
  const [f, setF] = useState(parseHashFilters)
  const [qNow, setQNow] = useState(f.q) // the search box's text, applied after a short pause
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [doneQuery, setDoneQuery] = useState('') // the query the data on screen answers
  const [selectedId, setSelectedId] = useState(initialSite || null)
  const [companyText, setCompanyText] = useState('')
  const [shown, setShown] = useState(12)
  const [nonce, setNonce] = useState(0) // Retry after an error
  const seq = useRef(0)

  useEffect(() => {
    const t = window.setTimeout(() => setQNow(f.q), 260)
    return () => window.clearTimeout(t)
  }, [f.q])

  // A link into this tab while it is already open (Population's "See Florida's data centers", a shared
  // #/views/datacenters?site=… link) applies its filters and site too; mounting read them once already.
  useEffect(() => {
    const on = () => {
      const m = window.location.hash.match(/^#\/views\/datacenters\?(.*)/)
      if (!m) return
      const next = parseHashFilters()
      if (next.states.length || next.companies.length || next.q) {
        setF(next)
        setQNow(next.q)
      }
      const site = new URLSearchParams(m[1]).get('site')
      if (site) setSelectedId(site)
    }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const query = buildQuery(f, qNow)
  useEffect(() => {
    const mine = ++seq.current
    api(`/api/views/datacenters?${query}`)
      .then((d) => {
        if (mine !== seq.current) return
        setData(d)
        setError(null)
        setDoneQuery(query)
      })
      .catch((e) => {
        if (mine !== seq.current) return
        setError(e)
        setDoneQuery(query)
      })
  }, [query, nonce])
  const loading = doneQuery !== query

  const set = (patch) => setF((cur) => ({ ...cur, ...patch }))
  const toggle = (key, v) => setF((cur) => ({ ...cur, [key]: cur[key].includes(v) ? cur[key].filter((x) => x !== v) : [...cur[key], v] }))
  const toggleStatus = (s) =>
    setF((cur) => {
      const on = cur.statuses.includes(s)
      if (on && cur.statuses.length === 1) return cur // keep at least one status
      return { ...cur, statuses: on ? cur.statuses.filter((x) => x !== s) : [...cur.statuses, s] }
    })
  const clearAll = () => {
    setF({ q: '', states: [], companies: [], statuses: DEFAULT_STATUSES, minMw: 0, crypto: false })
    setQNow('')
  }

  const sites = useMemo(() => data?.sites || [], [data])
  const mapSites = useMemo(() => sites.filter((s) => s.lat != null && !NON_CONUS.has(s.state)), [sites])
  const offMap = sites.length - mapSites.length
  const facets = data?.facets
  const totals = data?.totals
  const stateName = useMemo(() => Object.fromEntries((facets?.states || []).map((s) => [s.state, s.name])), [facets])
  const topCompanies = useMemo(() => {
    const list = (facets?.companies || []).filter((c) => c.company !== 'Undisclosed')
    const top = list.slice(0, 10)
    const have = new Set(top.map((c) => c.company))
    f.companies.forEach((name) => {
      if (!have.has(name)) top.push(list.find((c) => c.company === name) || { company: name, sites: 0, mw: 0 })
    })
    return top
  }, [facets, f.companies])

  const addCompany = (value) => {
    const hit = (facets?.companies || []).find((c) => c.company.toLowerCase() === value.trim().toLowerCase())
    if (hit && !f.companies.includes(hit.company)) set({ companies: [...f.companies, hit.company] })
    if (hit) setCompanyText('')
    else setCompanyText(value)
  }

  const active = f.states.length + f.companies.length + (f.q ? 1 : 0) + (f.minMw ? 1 : 0) + (f.crypto ? 1 : 0) + (f.statuses.length !== DEFAULT_STATUSES.length || DEFAULT_STATUSES.some((s) => !f.statuses.includes(s)) ? 1 : 0)
  const statusFacet = Object.fromEntries((facets?.statuses || []).map((s) => [s.status, s]))
  const sizedNote = totals && totals.sites > totals.sized ? ` · ${fmt(totals.sized)} of ${fmt(totals.sites)} sites report a size` : ''

  return (
    <div className="vw-dc">
      <div className="vw-filters" role="search" aria-label="Filter the data centers">
        <div className="vw-filters__row">
          <Field label="Search sites, companies, places" type="search" value={f.q} placeholder="Try Loudoun, Stargate, Meta" onChange={(e) => set({ q: e.target.value })} />
          <Field
            as="select"
            label="Add a state"
            value=""
            onChange={(e) => {
              if (e.target.value && !f.states.includes(e.target.value)) set({ states: [...f.states, e.target.value] })
            }}
          >
            <option value="">{f.states.length ? 'Add another state…' : 'Any state'}</option>
            {(facets?.states || []).filter((s) => !f.states.includes(s.state)).map((s) => (
              <option key={s.state} value={s.state}>
                {s.name} ({fmt(s.sites)})
              </option>
            ))}
          </Field>
          <Field label={facets ? `Add a company (${fmt(facets.companies.length)})` : 'Add a company'} list="vw-companies" value={companyText} placeholder="Any company" onChange={(e) => addCompany(e.target.value)} />
          <datalist id="vw-companies">
            {(facets?.companies || []).map((c) => (
              <option key={c.company} value={c.company}>{`${fmt(c.sites)} sites · ${mwText(c.mw)}`}</option>
            ))}
          </datalist>
          <Field as="select" label="Size" value={f.minMw} onChange={(e) => set({ minMw: Number(e.target.value) })}>
            {SIZES.map((s) => (
              <option key={s.v} value={s.v}>
                {s.label}
              </option>
            ))}
          </Field>
        </div>

        <div className="vw-filters__row vw-filters__row--chips">
          <span className="vw-filters__k" id="vw-status-k">
            Status
          </span>
          <div className="vw-chips" role="group" aria-labelledby="vw-status-k">
            {STATUS_ORDER.map((s) => {
              const on = f.statuses.includes(s)
              const fc = statusFacet[s]
              return (
                <button key={s} type="button" className={on ? 'vw-chip vw-chip--on' : 'vw-chip'} aria-pressed={on} onClick={() => toggleStatus(s)}>
                  <Swatch color={`var(--vw-st-${s.replace(/[^a-z]+/g, '-')})`} ring={s === 'announced' || s === 'paused/canceled'} />
                  {STATUS_LABEL[s]}
                  {fc ? <span className="vw-chip__n">{fmt(fc.sites)}</span> : null}
                </button>
              )
            })}
          </div>
          <label className="vw-check">
            <input type="checkbox" checked={f.crypto} onChange={(e) => set({ crypto: e.target.checked })} />
            Include crypto-mining sites
          </label>
        </div>

        <div className="vw-filters__row vw-filters__row--chips">
          <span className="vw-filters__k" id="vw-co-k">
            Company
          </span>
          <div className="vw-chips" role="group" aria-labelledby="vw-co-k">
            {topCompanies.map((c) => {
              const on = f.companies.includes(c.company)
              return (
                <button key={c.company} type="button" className={on ? 'vw-chip vw-chip--on' : 'vw-chip'} aria-pressed={on} onClick={() => toggle('companies', c.company)}>
                  {c.company}
                  <span className="vw-chip__n">{chipMw(c.mw)}</span>
                </button>
              )
            })}
            {!topCompanies.length && <span className="muted">No companies match.</span>}
          </div>
        </div>

        {(f.states.length > 0 || active > 0) && (
          <div className="vw-filters__row vw-filters__row--chips" aria-live="polite">
            <span className="vw-filters__k">Filters on</span>
            <div className="vw-chips">
              {f.states.map((s) => (
                <button key={s} type="button" className="vw-chip vw-chip--tag" onClick={() => toggle('states', s)} aria-label={`Remove the state filter ${stateName[s] || s}`}>
                  {stateName[s] || s} <span aria-hidden="true">×</span>
                </button>
              ))}
              {f.companies.map((c) => (
                <button key={c} type="button" className="vw-chip vw-chip--tag" onClick={() => toggle('companies', c)} aria-label={`Remove the company filter ${c}`}>
                  {c} <span aria-hidden="true">×</span>
                </button>
              ))}
              {active > 0 && (
                <button type="button" className="vw-linkbtn" onClick={clearAll}>
                  Clear all filters
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      <ErrorBanner error={error && { message: `Couldn't load the data centers: ${error.message}` }} onRetry={() => setNonce((n) => n + 1)} />

      <div className="vw-dc__grid">
        <div className="vw-dc__main">
          <div className="vw-mapcard">
            <UsMap
              sites={mapSites}
              stateFilter={f.states}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onToggleState={(code) => toggle('states', code)}
              loading={loading}
            />
            <MapLegend crypto={f.crypto} />
          </div>
          <p className="vw-fine">
            {data ? `${fmt(mapSites.length)} sites on the map` : 'Loading the sites…'}
            {offMap > 0 ? ` · ${fmt(offMap)} more are outside the lower 48 or have no point (counted in the totals, not drawn)` : ''}. Drag to pan, scroll to zoom, click a state to filter to it, click a mark for its card. Points are approximate: usually a city or site centre.
          </p>

          <section aria-label="Largest sites in this selection" className="vw-list">
            <h2 className="vw-h3">Largest in this selection</h2>
            {sites.length === 0 && !loading ? (
              <p className="muted">No sites match these filters.</p>
            ) : (
              <>
                <div className="vw-tablewrap">
                <table className="vw-table vw-table--list">
                  <thead>
                    <tr>
                      <th scope="col">Site</th>
                      <th scope="col">Company</th>
                      <th scope="col">State</th>
                      <th scope="col">Status</th>
                      <th scope="col" className="num">
                        Reported MW
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sites.slice(0, shown).map((s) => (
                      <tr key={s.id} className={s.id === selectedId ? 'is-on' : undefined}>
                        <td>
                          <button type="button" className="vw-linkbtn vw-linkbtn--row" aria-pressed={s.id === selectedId} onClick={() => setSelectedId(s.id === selectedId ? null : s.id)}>
                            {s.name}
                          </button>
                        </td>
                        <td>{s.company}</td>
                        <td>{s.state || '—'}</td>
                        <td>{STATUS_LABEL[s.status] || s.status}</td>
                        <td className="num">{s.mw ? fmt(s.mw) : 'not reported'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
                {sites.length > shown && (
                  <button type="button" className="vw-linkbtn" onClick={() => setShown((n) => Math.min(n + 24, 120))}>
                    Show more ({fmt(Math.min(sites.length, 120) - shown)} more of the largest)
                  </button>
                )}
              </>
            )}
          </section>
        </div>

        <aside className="vw-dc__side" aria-label="Selection details">
          {selectedId && <SiteCard key={selectedId} id={selectedId} onClose={() => setSelectedId(null)} />}

          <section className="vw-card" aria-label="Totals for this selection">
            <p className="vw-kicker">In this selection</p>
            {totals ? (
              <>
                <p className="vw-big">
                  {mwText(totals.mw)} <span className="vw-big__u">reported</span>
                </p>
                <p className="vw-fine">
                  {fmt(totals.sites)} sites · {fmt(totals.companies)} companies · {fmt(totals.states)} {placesWord(totals.by_state.map((s) => s.state), totals.states)}{sizedNote}
                </p>
                <ul className="vw-status">
                  {totals.by_status.map((s) => (
                    <li key={s.status}>
                      <Swatch color={`var(--vw-st-${s.status.replace(/[^a-z]+/g, '-')})`} ring={s.status === 'announced' || s.status === 'paused/canceled'} />
                      <span>{STATUS_LABEL[s.status]}</span>
                      <span className="vw-status__n">{fmt(s.sites)} sites</span>
                      <span className="vw-status__mw">{mwText(s.mw)}</span>
                    </li>
                  ))}
                </ul>
                {totals.share_of_model_load_pct != null && (
                  <p className="vw-scale">
                    <strong className="vw-fig">{fmt1(totals.share_of_model_load_pct)}%</strong> of the base load of the {fmt(totals.modeled_states)} state grid model{totals.modeled_states === 1 ? '' : 's'} involved. {totals.note}
                  </p>
                )}
              </>
            ) : (
              <p className="muted">Loading…</p>
            )}
          </section>

          {totals && totals.by_state.length > 0 && (
            <section className="vw-card" aria-label="By state">
              <p className="vw-kicker">By state: reported MW against the model&apos;s load</p>
              <div className="vw-tablewrap">
              <table className="vw-table vw-table--states">
                <thead>
                  <tr>
                    <th scope="col">State</th>
                    <th scope="col" className="num">
                      Sites
                    </th>
                    <th scope="col" className="num">
                      Reported MW
                    </th>
                    <th scope="col" className="num">
                      Of model load
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {totals.by_state.slice(0, 10).map((s) => (
                    <tr key={s.state} className={f.states.includes(s.state) ? 'is-on' : undefined}>
                      <td>
                        <button type="button" className="vw-linkbtn vw-linkbtn--row" aria-pressed={f.states.includes(s.state)} onClick={() => toggle('states', s.state)} title="Filter to this state">
                          {s.name}
                        </button>
                      </td>
                      <td className="num">{fmt(s.sites)}</td>
                      <td className="num">{fmt(s.mw)}</td>
                      <td className="num">
                        {s.share_pct != null ? (
                          <MapLink state={s.state} title={`Open ${s.name}'s grid model (${fmt(s.model_load_mw)} MW base load)`}>
                            {fmt1(s.share_pct)}%
                          </MapLink>
                        ) : (
                          <span className="muted" title="No grid model for this state">
                            no model
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              {totals.states > 10 && (
                <p className="vw-fine">
                  The 10 largest of {fmt(totals.states)} {placesWord(totals.by_state.map((s) => s.state), totals.states)}.
                </p>
              )}
              <p className="vw-fine">A share above 100% only means the reported MW is larger than that model&apos;s base load; the models hold no real campus, so this is a scale, not an effect.</p>
            </section>
          )}
        </aside>
      </div>
    </div>
  )
}
