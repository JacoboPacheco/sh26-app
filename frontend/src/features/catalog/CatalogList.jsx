import { useMemo, useState } from 'react'
import { fmt } from '../../geo'
import { Button, EmptyState, ErrorBanner, Field, Loading } from '../../ui'
import { selectEntry, setStateFilter, useCatalog } from './catalogApi'
import { placeLine, riskScore, statusLabel, verdictOf } from './format'
import './catalog.css'

// Every real campus in the catalog: search, sort, filter; each row shows its reported size and what it
// does to its state's synthetic model (tested alone). Picking a row selects it for DataCenterCard.
// Duplicate listings are folded into one row by the backend (hidden here).
const SORTS = [
  { id: 'mw', label: 'Largest first', cmp: (a, b) => (b.mw || 0) - (a.mw || 0) },
  {
    id: 'risk',
    label: 'Most people (estimate)',
    cmp: (a, b) => {
      const ra = riskScore(a)
      const rb = riskScore(b)
      return rb[0] - ra[0] || rb[1] - ra[1] || rb[2] - ra[2]
    },
  },
  { id: 'name', label: 'Name', cmp: (a, b) => a.name.localeCompare(b.name) },
]

export default function CatalogList({ onSelect }) {
  const { data, error, loading, progress, selected, stateFilter, reload } = useCatalog()
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('mw')
  const [status, setStatus] = useState('')
  const [confidence, setConfidence] = useState('')

  const rows = useMemo(() => (data?.entries || []).filter((e) => !e.duplicate_of), [data])
  const states = useMemo(() => {
    const m = new Map()
    rows.forEach((e) => m.set(e.state, { name: e.state_name, n: (m.get(e.state)?.n || 0) + 1 }))
    return [...m.entries()].sort((a, b) => a[1].name.localeCompare(b[1].name))
  }, [rows])
  const statuses = useMemo(() => [...new Set(rows.map((e) => e.status))].sort(), [rows])
  const confidences = useMemo(() => [...new Set(rows.map((e) => e.confidence))].sort(), [rows])

  const shown = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
    const cmp = SORTS.find((s) => s.id === sort)?.cmp || SORTS[0].cmp
    return rows
      .filter((e) => !stateFilter || e.state === stateFilter)
      .filter((e) => !status || e.status === status)
      .filter((e) => !confidence || e.confidence === confidence)
      .filter((e) => {
        if (!words.length) return true
        const hay = `${e.name} ${e.company} ${e.city} ${e.county} ${e.state} ${e.state_name}`.toLowerCase()
        return words.every((w) => hay.includes(w))
      })
      .sort(cmp)
  }, [rows, query, sort, status, confidence, stateFilter])

  const filtered = !!(query || status || confidence || stateFilter)
  const clear = () => {
    setQuery('')
    setStatus('')
    setConfidence('')
    setStateFilter('')
  }
  const pick = (id) => {
    selectEntry(id)
    onSelect?.(id)
  }

  if (!data) {
    if (error) return <ErrorBanner error={error} onRetry={reload} />
    return <Loading label={loading ? 'Loading the data-center catalog…' : 'Loading…'} />
  }
  const testing = progress?.status === 'computing'

  return (
    <section className="cat-list stack" aria-label="Data-center catalog">
      <div className="cat-list__controls">
        <Field label="Search" type="search" placeholder="Name, developer, city or state" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Field label="Sort" as="select" value={sort} onChange={(e) => setSort(e.target.value)}>
          {SORTS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </Field>
        <Field label="State" as="select" value={stateFilter} onChange={(e) => setStateFilter(e.target.value)}>
          <option value="">All states</option>
          {states.map(([code, s]) => (
            <option key={code} value={code}>
              {s.name} ({s.n})
            </option>
          ))}
        </Field>
        <Field label="Status" as="select" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          {statuses.map((s) => (
            <option key={s} value={s}>
              {statusLabel(s)}
            </option>
          ))}
        </Field>
        <Field label="Sourcing" as="select" value={confidence} onChange={(e) => setConfidence(e.target.value)}>
          <option value="">Any confidence</option>
          {confidences.map((c) => (
            <option key={c} value={c}>
              {c[0].toUpperCase() + c.slice(1)} confidence
            </option>
          ))}
        </Field>
      </div>

      <div className="cat-list__meta">
        <p className="muted" aria-live="polite">
          {shown.length === rows.length ? `${rows.length} campuses` : `Showing ${shown.length} of ${rows.length} campuses`}
          {testing && ` · testing ${progress.done} of ${progress.total} on their state models…`}
        </p>
        {filtered && (
          <Button variant="secondary" onClick={clear}>
            Clear filters
          </Button>
        )}
      </div>
      <ErrorBanner error={error} onRetry={reload} />

      {shown.length === 0 ? (
        <EmptyState title="No campus matches">Try another name, or clear the filters.</EmptyState>
      ) : (
        <ul className="cat-rows">
          {shown.map((e) => {
            const v = verdictOf(e.test)
            return (
              <li key={e.id}>
                <button type="button" className={`cat-row${selected === e.id ? ' cat-row--on' : ''}`} aria-pressed={selected === e.id} onClick={() => pick(e.id)}>
                  <span className="cat-row__name">{e.name}</span>
                  <span className="cat-row__mw">{fmt(e.mw || 0)} MW</span>
                  <span className="cat-row__meta">
                    {e.company} · {placeLine(e)} · {statusLabel(e.status)}
                  </span>
                  <span className={`cat-chip cat-chip--${v.key}`} title={v.title}>
                    {v.label}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
      <p className="cat-note">Sizes as reported by the cited sources. Each chip is a campus of that size tested alone on its state&apos;s synthetic grid model: not a prediction about the real project or utility.</p>
    </section>
  )
}
