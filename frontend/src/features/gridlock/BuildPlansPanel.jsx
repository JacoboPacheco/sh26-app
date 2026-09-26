import { useMemo, useState } from 'react'
import { Badge, Button, EmptyState, ErrorBanner, Field, Loading } from '../../ui'
import { useGridlock } from './context'
import PipelinePanel from './PipelinePanel'
import { KM_PER_MI, TIER_LABEL, TIER_ORDER, UTILITIES, fmtDistance, fmtInt, fmtTimeline, pairDistance, toneOf, utilityShort } from './format'
import './gridlock.css'

// The Build plans sidebar: the story, the sources, the comparison settings, the ranked list of
// coordination opportunities, and the data pipeline (the part that makes every number traceable).
export default function BuildPlansPanel({ extra }) {
  const g = useGridlock()
  return (
    <div className="gl gl-side">
      <header className="gl-head">
        <div className="gl-head__top">
          <span className="gl-module">Build plans</span>
          {g.conn.status === 'ready' && !g.fallback && <Badge>Real public filings</Badge>}
          {g.fallback && <Badge tone="warn">Sperry&apos;s worked example</Badge>}
          {extra}
        </div>
        <h1 className="gl-story">The AI boom is forcing the grid to grow. Are neighbors building it together?</h1>
        <p className="gl-lede">
          Utilities on both sides of the Savannah River publish their planned transmission work. We read both filings, place every
          project on the map, and rank the pairs that could be built together.
        </p>
        <SourcesLine />
        {g.fallback && (
          <p className="gl-sample" role="status">
            The pipeline hasn&apos;t built the full dataset yet, so this shows Sperry&apos;s ten-project worked example.
          </p>
        )}
      </header>

      {g.conn.status === 'loading' && <Loading label="Reading the construction plans…" />}
      {g.conn.status === 'error' && <ErrorBanner error={g.conn.error} onRetry={g.reload} />}
      {g.conn.status === 'ready' && (
        <>
          <div className="gl-tabs" role="tablist" aria-label="Build plans sections">
            {[
              ['opportunities', 'Opportunities'],
              ['projects', 'Projects'],
              ['pipeline', 'Data pipeline'],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                id={`gl-tab-${id}`}
                aria-controls={`gl-panel-${id}`}
                aria-selected={g.tab === id}
                className={`gl-tab${g.tab === id ? ' gl-tab--on' : ''}`}
                onClick={() => g.setTab(id)}
              >
                {label}
              </button>
            ))}
          </div>
          <div role="tabpanel" id={`gl-panel-${g.tab}`} aria-labelledby={`gl-tab-${g.tab}`} className="gl-tabpanel">
            {g.tab === 'opportunities' && (
              <>
                <Controls />
                <RankedList />
              </>
            )}
            {g.tab === 'projects' && <ProjectFinder />}
            {g.tab === 'pipeline' && <PipelinePanel />}
          </div>
        </>
      )}
    </div>
  )
}

function SourcesLine() {
  const g = useGridlock()
  const sources = g.summary?.sources || []
  if (!sources.length) return null
  return (
    <p className="gl-sources">
      Sources:{' '}
      {sources.map((s, i) => (
        <span key={s.id || i}>
          {i > 0 && '; '}
          {s.url ? (
            <a href={s.url} target="_blank" rel="noreferrer" title={s.title}>
              {shortTitle(s)}
            </a>
          ) : (
            shortTitle(s)
          )}
          {s.pages ? ` (${s.pages} page${s.pages === 1 ? '' : 's'})` : ''}
        </span>
      ))}
      .
    </p>
  )
}

// the filings' full titles are long; the header names them briefly (full titles in the pipeline tab)
function shortTitle(s) {
  if (s.short_title) return s.short_title
  if (s.utility === 'DESC') return 'Dominion Energy SC projects, 2024–2028'
  if (s.utility === 'GA' || s.utility === 'GPC') return 'Georgia Power 2025 IRP, Volume 3 (public disclosure)'
  return s.title
}

function Controls() {
  const g = useGridlock()
  const { params, setParams, toggleUtility } = g
  const counts = useMemo(() => {
    const c = {}
    for (const p of g.projects.list || []) c[p.utility] = (c[p.utility] || 0) + 1
    return c
  }, [g.projects.list])
  const mi = params.max_km / KM_PER_MI
  const shown = UTILITIES.filter((u) => u.id === 'DESC' || u.id === 'GPC' || counts[u.id])
  const on = UTILITIES.filter((u) => params.utilities[u.id])
  return (
    <section className="gl-controls" aria-label="Comparison settings">
      <Field
        label={`Within ${params.max_km} km (${mi.toFixed(mi < 10 ? 1 : 0)} mi)`}
        hint="Sperry's cutoff is 25 mi (40 km)."
        type="range"
        min={1}
        max={50}
        step={1}
        value={params.max_km}
        aria-valuetext={`${params.max_km} kilometers`}
        onChange={(e) => setParams({ max_km: Number(e.target.value) })}
      />
      <fieldset className="gl-seg">
        <legend>Measure between</legend>
        {[
          ['closest', 'Closest points'],
          ['center', "Centers (Sperry's method)"],
        ].map(([id, label]) => (
          <label key={id} className={params.method === id ? 'gl-seg--on' : ''}>
            <input type="radio" name="gl-method" value={id} checked={params.method === id} onChange={() => setParams({ method: id })} />
            {label}
          </label>
        ))}
      </fieldset>
      <details className="gl-more">
        <summary>
          More settings{' '}
          <span className="gl-fine">
            {params.window_months}-month build window · {on.map((u) => u.short).join(', ') || 'no utilities'}
          </span>
        </summary>
        <div className="gl-more__body">
          <Field
            label={`Build window: ${params.window_months} months before in service`}
            hint={
              g.ov.window_assumed
                ? `Used for the ${fmtInt(g.ov.window_assumed.projects)} of ${fmtInt(g.ov.window_assumed.of)} projects whose filing gives no start date; the rest keep their filed window. Pairs building at the same time rank higher.`
                : 'Pairs whose build windows share months rank higher.'
            }
            type="range"
            min={6}
            max={60}
            step={6}
            value={params.window_months}
            aria-valuetext={`${params.window_months} months`}
            onChange={(e) => setParams({ window_months: Number(e.target.value) })}
          />
          <fieldset className="gl-utils">
            <legend>Utilities</legend>
            {shown.map((u) => (
              <label key={u.id} className="gl-check-row">
                <input type="checkbox" checked={!!params.utilities[u.id]} onChange={() => toggleUtility(u.id)} />
                <span className={`gl-swatch gl-swatch--${u.tone}`} aria-hidden="true" />
                <span className="gl-check-row__name">{u.name}</span>
                <span className="gl-check-row__n">{counts[u.id] != null ? fmtInt(counts[u.id]) : ''}</span>
              </label>
            ))}
            <p className="gl-fine">Georgia&apos;s sponsors plan one integrated system together, so each is compared with DESC.</p>
          </fieldset>
        </div>
      </details>
    </section>
  )
}

const ORDERS = [
  ['score', 'Best score'],
  ['distance', 'Closest first'],
]

function RankedList() {
  const g = useGridlock()
  const { ov, overlaps, params, sel, byId } = g
  const [all, setAll] = useState(false)
  const [order, setOrder] = useState('score')
  const [tier, setTier] = useState(null)
  // the engine ranks by score; "closest first" and a tier filter only re-order / narrow what it sent
  const listed = useMemo(() => {
    const rows = tier ? overlaps.filter((o) => o.tier === tier) : overlaps
    if (order !== 'distance') return rows
    const d = (o) => pairDistance(o, params.method).km ?? Infinity
    return [...rows].sort((a, b) => d(a) - d(b) || (a.rank ?? 0) - (b.rank ?? 0))
  }, [overlaps, tier, order, params.method])
  const shown = all ? listed : listed.slice(0, 10)
  const total = ov.total_pairs || 0
  const flagged = ov.flagged ?? overlaps.length
  const share = total ? flagged / total : 0
  const tierOn = tier && ov.by_tier?.[tier] ? tier : null

  return (
    <section className="gl-ranked" aria-labelledby="gl-ranked-h">
      <div className="gl-sec__row">
        <h2 id="gl-ranked-h" className="gl-h2">
          Top coordination opportunities
        </h2>
        {ov.status === 'refreshing' && <span className="gl-fine">Updating…</span>}
      </div>
      {ov.status === 'loading' && <Loading label="Comparing the plans…" />}
      {ov.status === 'error' && <ErrorBanner error={ov.error} onRetry={g.loadOverlaps} />}
      {(ov.status === 'ready' || ov.status === 'refreshing') && (
        <>
          {!g.pairs.length ? (
            <EmptyState title="Nothing to compare">Switch on DESC and at least one Georgia utility.</EmptyState>
          ) : (
            <div className="gl-share">
              <p>
                <strong>{fmtInt(flagged)}</strong> of {fmtInt(total)} cross-state pairs are within {params.max_km} km
                {params.method === 'center' ? ', center to center' : ''}. Most planned projects aren&apos;t near each other; these are the
                ones that are.
              </p>
              <div className="gl-share__bar" aria-hidden="true">
                <span style={{ width: `${Math.max(flagged ? 1.5 : 0, share * 100)}%` }} />
              </div>
              {ov.by_tier && flagged > 0 && (
                <ul className="gl-tiers" aria-label="Show only one distance tier">
                  {TIER_ORDER.filter((t) => ov.by_tier[t]).map((t) => (
                    <li key={t}>
                      <button
                        type="button"
                        className={`gl-tier gl-tier--${t} gl-tierbtn${tierOn === t ? ' gl-tierbtn--on' : ''}`}
                        aria-pressed={tierOn === t}
                        onClick={() => setTier((cur) => (cur === t ? null : t))}
                      >
                        {fmtInt(ov.by_tier[t])} {TIER_LABEL[t].toLowerCase()}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {ov.truncated && <p className="gl-fine">The engine sent the top {fmtInt(overlaps.length)}; narrow the distance to see the rest ranked.</p>}
            </div>
          )}
          {g.pairs.length > 0 && !flagged && (
            <EmptyState
              title={`No pairs within ${params.max_km} km`}
              action={
                params.max_km < 40 ? (
                  <Button variant="secondary" onClick={() => g.setParams({ max_km: 40 })}>
                    Widen to 40 km
                  </Button>
                ) : null
              }
            />
          )}
          {flagged > 0 && (
            <div className="gl-order" role="group" aria-label="Order the list">
              {ORDERS.map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  className={`gl-order__btn${order === id ? ' gl-order__btn--on' : ''}`}
                  aria-pressed={order === id}
                  onClick={() => setOrder(id)}
                >
                  {label}
                </button>
              ))}
              {(order !== 'score' || tierOn) && <span className="gl-fine">Numbers are the score rank.</span>}
            </div>
          )}
          <ol className={`gl-list${ov.status === 'refreshing' ? ' gl-list--stale' : ''}`}>
            {shown.map((o) => {
              const a = byId[o.a]
              const b = byId[o.b]
              const on = sel?.kind === 'overlap' && sel.id === o.id
              return (
                <li key={o.id}>
                  <button
                    type="button"
                    className={`gl-item gl-item--${o.tier}${on ? ' gl-item--on' : ''}`}
                    aria-current={on || undefined}
                    onClick={() => {
                      g.openOverlap(o)
                      // stacked on a phone: bring the map (and the card under it) into view
                      if (window.matchMedia?.('(max-width: 760px)').matches) document.querySelector('.gl-map')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                    }}
                    onPointerEnter={(e) => e.pointerType !== 'touch' && g.setHover({ kind: 'overlap', id: o.id })}
                    onPointerLeave={() => g.setHover(null)}
                    onFocus={(e) => e.currentTarget.matches(':focus-visible') && g.setHover({ kind: 'overlap', id: o.id })}
                    onBlur={() => g.setHover(null)}
                  >
                    <span className="gl-item__rank">{o.displayRank ?? o.rank}</span>
                    <span className="gl-item__body">
                      <span className="gl-item__top">
                        <span className={`gl-tier gl-tier--${o.tier}`}>{o.tier_label}</span>
                        <span className="gl-item__dist">{fmtDistance(pairDistance(o, params.method).km, pairDistance(o, params.method).mi)}</span>
                      </span>
                      <ProjLine p={a} id={o.a} />
                      <ProjLine p={b} id={o.b} />
                      <span className="gl-item__time">
                        {fmtTimeline(o)}
                        {o.sperry && <span className="gl-sperrytag">In Sperry&apos;s example ({o.sperry})</span>}
                      </span>
                    </span>
                  </button>
                </li>
              )
            })}
          </ol>
          {listed.length > 10 && (
            <Button variant="secondary" onClick={() => setAll((v) => !v)}>
              {all ? 'Show the first 10' : `Show all ${listed.length}`}
            </Button>
          )}
        </>
      )}
    </section>
  )
}

function ProjLine({ p, id }) {
  const u = p?.utility
  return (
    <span className="gl-item__proj">
      <span className={`gl-swatch gl-swatch--${toneOf(u)}`} aria-hidden="true" />
      <span className="gl-item__who">{utilityShort(u)}</span>
      <span className="gl-item__name">{p?.name || id}</span>
    </span>
  )
}

// Every located project, searchable: pick one to fly to it and open "Where this came from".
function ProjectFinder() {
  const g = useGridlock()
  const [q, setQ] = useState('')
  const list = g.projects.list || []
  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    const hit = (p) => {
      const hay = `${p.name} ${p.id} ${p.teams_no || ''} ${p.utility} ${utilityShort(p.utility)}`.toLowerCase()
      return words.every((w) => hay.includes(w))
    }
    return (g.projects.list || []).filter((p) => g.params.utilities[p.utility] ?? true).filter(hit)
  }, [g.projects.list, q, g.params.utilities])
  const setAside = (g.projects.quarantine || []).length
  if (g.projects.status === 'loading') return <Loading label="Loading projects…" />
  if (g.projects.status === 'error') return <ErrorBanner error={g.projects.error} onRetry={g.loadProjects} />
  const sel = g.sel?.kind === 'project' ? g.sel.id : null
  return (
    <section className="gl-finder" aria-label="All located projects">
      <Field label="Find a project" type="search" value={q} placeholder="A name, place or project number" onChange={(e) => setQ(e.target.value)} />
      <p className="gl-fine">
        {fmtInt(shown.length)} of {fmtInt(list.length)} located projects
        {setAside ? `; ${fmtInt(setAside)} more set aside (see Data pipeline)` : ''}. Only switched-on utilities are listed.
      </p>
      {!shown.length ? (
        <EmptyState title="No project matches">Try a substation name, like McIntosh or Thurmond.</EmptyState>
      ) : (
        <ul className="gl-plist">
          {shown.slice(0, 80).map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className={`gl-prow${sel === p.id ? ' gl-prow--on' : ''}`}
                aria-current={sel === p.id || undefined}
                onClick={() => g.openProject(p.id, { fly: true })}
                onPointerEnter={(e) => e.pointerType !== 'touch' && g.setHover({ kind: 'project', id: p.id })}
                onPointerLeave={() => g.setHover(null)}
              >
                <span className="gl-item__proj">
                  <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
                  <span className="gl-item__who">{utilityShort(p.utility)}</span>
                  <span className="gl-prow__name">{p.name}</span>
                </span>
                <span className="gl-item__time">
                  {[p.in_service ? `in service ${p.in_service.slice(0, 7)}` : 'no date', `${p.confidence || 'unknown'} confidence`, p.id].join(' · ')}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {shown.length > 80 && <p className="gl-fine">Showing the first 80; type to narrow.</p>}
    </section>
  )
}
