import { Fragment, useMemo, useState } from 'react'
import { Badge, Button, EmptyState, ErrorBanner, Field, Loading } from '../../ui'
import { useGridlock } from './context'
import PipelinePanel from './PipelinePanel'
import { KM_PER_MI, SAME_STATION, TIER_LABEL, TIER_ORDER, UTILITIES, displayName, fmtInt, fmtMi, pairDistance, toneOf, utilityShort, whenOf } from './format'
import './gridlock.css'

// The Build together rail: a short title and one sentence, the two filings, the filters folded away, then the
// ranked pairs at once (picking one opens its sheet over the map). The data pipeline (the part that makes every
// number traceable) and the full project list are one click away in the rail's footer.
export default function BuildPlansPanel({ extra }) {
  const g = useGridlock()
  const view = g.tab === 'pipeline' || g.tab === 'projects' ? g.tab : 'pairs'
  return (
    <div className="gl gl-side">
      {view === 'pairs' ? (
        <PairsView extra={extra} />
      ) : (
        <SubView view={view} />
      )}
      {view === 'pairs' && g.conn.status === 'ready' && <RailFoot />}
    </div>
  )
}

function PairsView({ extra }) {
  const g = useGridlock()
  return (
    <>
      <header className="gl-head">
        <div className="gl-head__row">
          <h1 className="gl-title">Build together</h1>
          {g.fallback && <Badge tone="warn">Sperry&apos;s worked example</Badge>}
          {extra}
        </div>
        <p className="gl-lede">
          Where two utilities&apos; public construction plans overlap in place and time, building together can share crews, equipment and
          outage windows. Pick a pair to draft an agreement.
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
          <Filters />
          <RankedList />
        </>
      )}
    </>
  )
}

function SourcesLine() {
  const g = useGridlock()
  const sources = g.summary?.sources || []
  if (!sources.length) return null
  return (
    <p className="gl-sources">
      Public filings:{' '}
      {sources.map((s, i) => (
        <span key={s.id || i}>
          {i > 0 && (i === sources.length - 1 ? ' and ' : ', ')}
          {s.url ? (
            <a href={s.url} target="_blank" rel="noreferrer" title={s.title}>
              {shortTitle(s)}
            </a>
          ) : (
            shortTitle(s)
          )}
        </span>
      ))}
      .
    </p>
  )
}

// the filings' full titles are long; the rail names them briefly (full titles in the data pipeline)
function shortTitle(s) {
  if (s.short_title) return s.short_title
  if (s.utility === 'DESC') return 'Dominion Energy SC projects, 2024–2028'
  if (s.utility === 'GA' || s.utility === 'GPC') return 'Georgia Power 2025 IRP, Vol. 3'
  return s.title
}

// Every comparison setting in one closed fold, its summary saying what is set.
function Filters() {
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
  const georgia = on.filter((u) => u.id !== 'DESC')
  const summary = [
    `within ${mi.toFixed(mi < 10 ? 1 : 0)} mi`,
    params.method === 'center' ? 'center to center' : 'closest points',
    `${params.window_months}-month windows`,
    georgia.length === 1 && georgia[0].id === 'GPC' && params.utilities.DESC ? null : on.map((u) => u.short).join(', ') || 'no utilities',
    g.tierFilter ? `only ${TIER_LABEL[g.tierFilter].toLowerCase()}` : null,
  ].filter(Boolean)
  return (
    <details className="gl-filters">
      <summary>
        <span className="gl-filters__label">Filters</span>
        <span className="gl-filters__now">{summary.join(', ')}</span>
      </summary>
      <div className="gl-filters__body">
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
        <Field
          label={`Build window: ${params.window_months} months before in service`}
          hint={
            g.ov.window_assumed
              ? `Used for the ${fmtInt(g.ov.window_assumed.projects)} of ${fmtInt(g.ov.window_assumed.of)} projects whose filing gives no start date; the rest keep their filed window.`
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
        {g.ov.by_tier && (g.ov.flagged || 0) > 0 && (
          <fieldset className="gl-tierset">
            <legend>Show</legend>
            <div className="gl-tierset__opts">
              <button type="button" className={`gl-chip${!g.tierFilter ? ' is-on' : ''}`} aria-pressed={!g.tierFilter} onClick={() => g.setTierFilter(null)}>
                Every pair
              </button>
              {TIER_ORDER.filter((t) => g.ov.by_tier[t]).map((t) => (
                <button
                  key={t}
                  type="button"
                  className={`gl-chip gl-tier gl-tier--${t}${g.tierFilter === t ? ' is-on' : ''}`}
                  aria-pressed={g.tierFilter === t}
                  onClick={() => g.setTierFilter(g.tierFilter === t ? null : t)}
                >
                  {TIER_LABEL[t]} ({fmtInt(g.ov.by_tier[t])})
                </button>
              ))}
            </div>
          </fieldset>
        )}
      </div>
    </details>
  )
}

const ORDERS = [
  ['score', 'Best match'],
  ['distance', 'Closest'],
]

function RankedList() {
  const g = useGridlock()
  const { ov, overlaps, params } = g
  const [all, setAll] = useState(false)
  const [order, setOrder] = useState('score')
  const tier = g.tierFilter && ov.by_tier?.[g.tierFilter] ? g.tierFilter : null
  // the engine ranks by score; "closest" and the tier filter only re-order / narrow what it sent. Best match keeps
  // the engine's order but lists the pairs whose shared months have already passed (as filed) after the rest, under
  // their own line, each keeping its rank (the engine's score still counts a passed window in full)
  const listed = useMemo(() => {
    const rows = tier ? overlaps.filter((o) => o.tier === tier) : overlaps
    if (order !== 'distance') return [...rows.filter((o) => o.ahead !== 'past'), ...rows.filter((o) => o.ahead === 'past')]
    const d = (o) => pairDistance(o, params.method).km ?? Infinity
    return [...rows].sort((a, b) => d(a) - d(b) || (a.rank ?? 0) - (b.rank ?? 0))
  }, [overlaps, tier, order, params.method])
  const shown = all ? listed : listed.slice(0, 12)
  const firstPast = order === 'distance' ? -1 : listed.findIndex((o) => o.ahead === 'past')
  const total = ov.total_pairs || 0
  const flagged = ov.flagged ?? overlaps.length
  const mi = params.max_km / KM_PER_MI

  return (
    <section className="gl-ranked" aria-labelledby="gl-ranked-h">
      <div className="gl-listhead">
        <div className="gl-listhead__txt">
          <h2 id="gl-ranked-h" className="gl-listhead__h">
            {ov.status === 'loading' ? (
              'Comparing the plans…'
            ) : (
              <>
                <strong>
                  {fmtInt(tier ? listed.length : flagged)} pair{(tier ? listed.length : flagged) === 1 ? '' : 's'}
                </strong>{' '}
                within {mi.toFixed(mi < 10 ? 1 : 0)} mi
              </>
            )}
          </h2>
          {total > 0 && ov.status !== 'loading' && <p className="gl-listhead__of">of {fmtInt(total)} cross-state pairs compared</p>}
        </div>
        {flagged > 1 && (
          <div className="gl-sort" role="group" aria-label="Sort the pairs">
            {ORDERS.map(([id, label]) => (
              <button key={id} type="button" className={order === id ? 'is-on' : ''} aria-pressed={order === id} onClick={() => setOrder(id)}>
                {label}
              </button>
            ))}
          </div>
        )}
      </div>
      {ov.status === 'loading' && <Loading label="Ranking the pairs…" />}
      {ov.status === 'error' && <ErrorBanner error={ov.error} onRetry={g.loadOverlaps} />}
      {(ov.status === 'ready' || ov.status === 'refreshing') && (
        <>
          {!g.pairs.length && <EmptyState title="Nothing to compare">Switch on DESC and at least one Georgia utility under Filters.</EmptyState>}
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
            >
              Widen the distance under Filters.
            </EmptyState>
          )}
          {ov.truncated && <p className="gl-fine">The engine sent the top {fmtInt(overlaps.length)}; narrow the distance to see the rest ranked.</p>}
          {order !== 'score' && flagged > 1 && <p className="gl-fine">Sorted by distance; each keeps its best-match rank.</p>}
          {order === 'score' && listed[0]?.shared_station && (
            <p className="gl-fine">Listed first: pairs whose filings work at the same substation, where the two systems meet.</p>
          )}
          {order === 'score' && firstPast > 0 && <p className="gl-fine">Pairs whose shared months have passed are listed last; each keeps its rank.</p>}
          <ol className={`gl-rows${ov.status === 'refreshing' ? ' gl-rows--stale' : ''}`} aria-busy={ov.status === 'refreshing' || undefined}>
            {shown.map((o, i) => (
              <Fragment key={o.id}>
                {i === firstPast && i > 0 && (
                  <li className="gl-rows__sep" aria-hidden="true">
                    Shared months already passed, as filed
                  </li>
                )}
                <PairRow o={o} />
              </Fragment>
            ))}
          </ol>
          {listed.length > 12 && (
            <button type="button" className="gl-more-rows" onClick={() => setAll((v) => !v)} aria-expanded={all}>
              {all ? 'Show the top 12' : `Show all ${listed.length} pairs`}
            </button>
          )}
        </>
      )}
    </section>
  )
}

function PairRow({ o }) {
  const g = useGridlock()
  const a = g.byId[o.a]
  const b = g.byId[o.b]
  const on = g.draft?.id === o.id
  const d = pairDistance(o, g.params.method)
  const when = whenOf(o)
  const rank = o.displayRank ?? o.rank
  const dist = d.km == null ? 'distance unknown' : d.km < 0.05 ? (o.crosses ? 'crossing' : 'touching') : `${fmtMi(d.mi)} apart`
  return (
    <li>
      <button
        type="button"
        className={`gl-row gl-row--${when.tone}${on ? ' is-on' : ''}`}
        aria-current={on || undefined}
        data-pair={o.id}
        onClick={() => g.openDraft(o)}
        onPointerEnter={(e) => e.pointerType !== 'touch' && g.setHover({ kind: 'overlap', id: o.id })}
        onPointerLeave={() => g.setHover(null)}
        onFocus={(e) => e.currentTarget.matches(':focus-visible') && g.setHover({ kind: 'overlap', id: o.id })}
        onBlur={() => g.setHover(null)}
      >
        <span className="gl-row__rank">
          <span className="gl-sr">Pair </span>
          {rank}
        </span>
        <span className="gl-row__body">
          <ProjLine p={a} id={o.a} />
          <ProjLine p={b} id={o.b} />
          <span className="gl-row__meta">
            <span>{dist}</span>
            <span className={`gl-when gl-when--${when.tone}`}>{when.row}</span>
          </span>
          {/* a shared substation first (the engine lists these pairs above every tier); else the tier only when it isn't
              the usual one (crews and equipment: most pairs); the map key and Filters list all */}
          {(o.shared_station || o.tier !== 'crews' || o.sperry) && (
            <span className="gl-row__foot">
              {o.shared_station ? (
                <span className="gl-stationtag" title={o.shared_station.reason}>
                  {SAME_STATION.en.tag(o.shared_station.name)}
                </span>
              ) : (
                o.tier !== 'crews' && <span className={`gl-tier gl-tier--${o.tier}`}>{o.tier_label}</span>
              )}
              {o.sperry && <span className="gl-sperrytag">In Sperry&apos;s example ({o.sperry})</span>}
            </span>
          )}
        </span>
      </button>
    </li>
  )
}

function ProjLine({ p, id }) {
  const g = useGridlock()
  const u = p?.utility
  // two filed projects can read almost alike (DESC-6809E "… 46kV Rebuilds", DESC-6809G "… 46kV"): add the number
  const dup = g.nearDup?.has(id)
  return (
    <span className="gl-row__proj">
      <span className={`gl-swatch gl-swatch--${toneOf(u)}`} aria-hidden="true" />
      <span className="gl-sr">{utilityShort(u)}: </span>
      <span className="gl-row__name" title={p?.name ? `${utilityShort(u)}: ${displayName(p.name)} (${id})` : undefined}>
        {displayName(p?.name) || id}
      </span>
      {dup && <span className="gl-row__id">{id}</span>}
    </span>
  )
}

// the way into the pipeline and the full project list, always in view under the pairs
function RailFoot() {
  const g = useGridlock()
  const n = (g.projects.list || []).length
  return (
    <nav className="gl-railfoot" aria-label="How this data was built">
      <button type="button" className="gl-railfoot__main" onClick={() => g.setTab('pipeline')}>
        <span className="gl-railfoot__t">How we built this</span>
        <span className="gl-railfoot__d">The data pipeline, every check shown</span>
      </button>
      <button type="button" className="gl-railfoot__side" onClick={() => g.setTab('projects')}>
        {n ? `All ${fmtInt(n)} projects` : 'All projects'}
      </button>
    </nav>
  )
}

function SubView({ view }) {
  const g = useGridlock()
  return (
    <>
      <header className="gl-subhead">
        <button type="button" className="gl-back" onClick={() => g.setTab('opportunities')}>
          <span aria-hidden="true">‹</span> Pairs
        </button>
        <h1 className="gl-title">{view === 'pipeline' ? 'How we built this' : 'All projects'}</h1>
      </header>
      {g.conn.status === 'loading' && <Loading label="Reading the construction plans…" />}
      {g.conn.status === 'error' && <ErrorBanner error={g.conn.error} onRetry={g.reload} />}
      {g.conn.status === 'ready' && (view === 'pipeline' ? <PipelinePanel /> : <ProjectFinder />)}
    </>
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
        {setAside ? `; ${fmtInt(setAside)} more set aside (see How we built this)` : ''}. Only switched-on utilities are listed.
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
                <span className="gl-row__proj">
                  <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
                  <span className="gl-item__who">{utilityShort(p.utility)}</span>
                  <span className="gl-prow__name">{displayName(p.name)}</span>
                </span>
                <span className="gl-fine">
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
