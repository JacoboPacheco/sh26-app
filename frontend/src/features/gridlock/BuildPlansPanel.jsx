import { Fragment, useCallback, useMemo, useState } from 'react'
import { Badge, Button, EmptyState, ErrorBanner, Field, Loading } from '../../ui'
import CalendarView from './CalendarView'
import ChangesView from './ChangesView'
import { useGridlock } from './context'
import Gloss from './Gloss'
import { GLOSS } from './glossary'
import Intro, { RailSteps } from './Intro'
import { PairsNote, SetAsideView, SperryMarks, SperryView } from './PipelineFunnel'
import PipelinePanel from './PipelinePanel'
import {
  KM_PER_MI,
  SAME_STATION,
  TIER_LABEL,
  TIER_ORDER,
  UTILITIES,
  displayName,
  fmtInt,
  limitMi,
  limitText,
  pairDistance,
  toneOf,
  utilityShort,
  whenOf,
} from './format'
import { nearEndsOnly, reasonLine } from './plain'
import './gridlock.css'
import './plans.css'

// The Build together rail: a short title and one sentence, the two filings, the pipeline as one quiet line of numbers
// (each a way in), the filters folded away, then the ranked pairs at once (picking one opens its sheet over the map).
// The data pipeline (the part that makes every number traceable) and the full project list are one click away in the
// rail's footer; the funnel also opens the records set aside and Sperry's worked example as the start.
const SUB_VIEWS = {
  pipeline: 'How we built the data',
  projects: 'All projects',
  setaside: 'Set aside by the checks',
  sperry: "Sperry's worked example",
  changes: 'What changed since the last filing',
}

export default function BuildPlansPanel({ extra }) {
  const g = useGridlock()
  const view = SUB_VIEWS[g.tab] ? g.tab : 'pairs'
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
          <Intro />
          <div className="bt-step1">
            <RailSteps />
            <CompareWith />
          </div>
          <Filters />
          <RankedList />
          <Unplaced />
          <SourcesLine />
        </>
      )}
    </>
  )
}

// The filings, as published, in one line: DESC's current list (2026-2030), Georgia's public-disclosure copy (what that
// means, and that CEII-marked content isn't used) and DESC's earlier list (only the projects the new one no longer
// carries), then the way into what changed between DESC's two lists.
const SOURCE_LABEL = { desc_2026: 'DESC 2026–2030', ga_irp: 'Georgia Power 2025 IRP, Vol. 3', desc: 'DESC 2024–2028' }
function SourcesLine() {
  const g = useGridlock()
  const sources = g.summary?.sources || []
  if (!sources.length) return null
  const kept = g.summary?.edition?.earlier_kept?.read
  const note = (s) =>
    s.public_note ||
    (s.role === 'earlier'
      ? `DESC's earlier list. Only the ${kept != null ? fmtInt(kept) : ''} projects its 2026–2030 list no longer carries are compared (marked "DESC 2024–2028 list"); a project in both lists is read from the newer one. Sperry's worked example is built from this edition.`
      : s.role === 'current'
        ? "DESC's current list, read with the same pipeline and checks."
        : null)
  return (
    <div className="gl-sources bt-sources">
      <p>
        Compared from these public filings, as filed:{' '}
        {sources.map((s, i) => (
          <span key={s.id || i} className="gl-sources__one">
            {i > 0 && <span aria-hidden="true"> · </span>}
            {s.url ? (
              <a href={s.url} target="_blank" rel="noreferrer" title={s.title}>
                {SOURCE_LABEL[s.id] || shortTitle(s)}
              </a>
            ) : (
              SOURCE_LABEL[s.id] || shortTitle(s)
            )}
            {s.id === 'ga_irp' && <span className="gl-sources__pd"> (public disclosure)</span>}
            {note(s) && (
              <>
                {' '}
                <Gloss tip={note(s)} icon label={`About ${SOURCE_LABEL[s.id] || shortTitle(s)}`} />
              </>
            )}
          </span>
        ))}
      </p>
      {g.summary?.edition && (
        <button type="button" className="gl-link gl-sources__changes" onClick={() => g.setTab('changes')}>
          What changed since DESC&apos;s last filing <span aria-hidden="true">›</span>
        </button>
      )}
    </div>
  )
}

// the filings' full titles are long; the rail names them briefly (full titles in the data pipeline)
function shortTitle(s) {
  if (s.short_title) return s.short_title
  if (s.id === 'desc_2026') return 'DESC 2026–2030 project list'
  if (s.utility === 'DESC') return 'DESC 2024–2028 project list'
  if (s.utility === 'GA' || s.utility === 'GPC') return 'Georgia Power 2025 IRP, Vol. 3 (public disclosure)'
  return s.title
}

// Which Georgia utilities DESC is compared with: Georgia Power alone (the default) or all four of Georgia's planning
// partners in the same filing (Georgia Power, GTC, MEAG, Dalton). One click, no fold.
function CompareWith() {
  const g = useGridlock()
  const n = g.summary?.funnel?.default_view?.not_compared_total ?? null
  return (
    <div className="bt-compare" role="group" aria-label="Compare DESC with">
      <span className="bt-compare__label">Compare DESC with</span>
      <div className="gl-sort">
        <button type="button" className={!g.allGeorgia ? 'is-on' : ''} aria-pressed={!g.allGeorgia} onClick={() => g.setAllGeorgia(false)}>
          Georgia Power
        </button>
        <button
          type="button"
          className={g.allGeorgia ? 'is-on' : ''}
          aria-pressed={g.allGeorgia}
          onClick={() => g.setAllGeorgia(true)}
          title={`Also Georgia Transmission Corp., MEAG Power and Dalton Utilities, from the same Georgia filing${n ? `: ${n} more projects` : ''}`}
        >
          All 4 Georgia utilities
        </button>
      </div>
      {g.allGeorgia && g.ov.status === 'ready' && g.overlaps.length > 0 && g.overlaps.every((o) => (o.b_utility || 'GPC') === 'GPC') && (
        <p className="gl-fine bt-compare__note" role="status">
          No GTC, MEAG or Dalton project comes within {limitMi(g.params.max_km)} of a DESC project at these settings: every pair is still
          Georgia Power&apos;s{g.ov.total_pairs ? ` (${fmtInt(g.ov.total_pairs)} pairs compared)` : ''}.
        </p>
      )}
    </div>
  )
}

// Projects the pipeline read but could not place on the map (no endpoint matched), listed with the place names their
// description gives: they may overlap, but no distance can be measured, so they are never ranked.
function Unplaced() {
  const g = useGridlock()
  const u = g.ov.unplaced
  const list = u?.projects || []
  const sources = g.summary?.sources || []
  if (!list.length) return null
  // a filing page link: the PDF opens at the page; a docket page can't, so it opens as is
  const link = (x) => {
    const s = sources.find((f) => f.id === x.source)
    if (!s?.url) return null
    return /\.pdf($|[?#])/i.test(s.url) && x.page ? `${s.url.split('#')[0]}#page=${x.page}` : s.url
  }
  return (
    <details className="bt-unplaced">
      <summary>
        {u.label || 'Possible overlaps, unplaced'} <span className="bt-unplaced__n">{fmtInt(u.count ?? list.length)}</span>
      </summary>
      <p className="gl-fine">
        {u.what ||
          'No end of these projects could be placed on the map, so no distance is measured and they are not ranked.'}
      </p>
      <ul>
        {list.map((x, i) => {
          const p = x.project || x
          const href = p.source_url || link(p)
          return (
            <li key={p.id || i}>
              <span className="gl-row__proj">
                <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
                <span className="gl-sr">{utilityShort(p.utility)}: </span>
                <span className="gl-row__name">{displayName(p.name) || p.id}</span>
              </span>
              <span className="gl-fine">
                {[
                  x.why || x.reason,
                  x.same_months_as ? `shares build months with ${fmtInt(x.same_months_as)} of the other side's projects` : null,
                  x.near?.length ? `near ${x.near.join(', ')}` : null,
                ]
                  .filter(Boolean)
                  .join('; ')}
                {href && (
                  <>
                    {' '}
                    <a href={href} target="_blank" rel="noreferrer">
                      {p.page ? `p. ${p.page}` : 'filing'}
                    </a>
                  </>
                )}
              </span>
            </li>
          )
        })}
      </ul>
    </details>
  )
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
  const wholeMi = Math.round(params.max_km / KM_PER_MI)
  const shown = UTILITIES.filter((u) => u.id === 'DESC' || u.id === 'GPC' || counts[u.id])
  const on = UTILITIES.filter((u) => params.utilities[u.id])
  const georgia = on.filter((u) => u.id !== 'DESC')
  const summary = [
    `within ${wholeMi} mi`,
    params.method === 'center' ? 'center to center' : 'closest points',
    // the build window assumed when a filing gives only an end date (said in plain words)
    `${params.window_months} months of building assumed if only an end date is filed`,
    georgia.length === 1 && georgia[0].id === 'GPC' && params.utilities.DESC ? null : on.map((u) => u.short).join(', ') || 'no utilities',
    g.tierFilter ? `only ${TIER_LABEL[g.tierFilter].toLowerCase()}` : null,
  ].filter(Boolean)
  return (
    <details className="gl-filters" open={g.filtersOpen} onToggle={(e) => g.setFiltersOpen(e.currentTarget.open)}>
      <summary>
        <span className="gl-filters__label">Filters</span>
        <span className="gl-filters__now">{summary.join(', ')}</span>
      </summary>
      <div className="gl-filters__body">
        <PairsNote />
        <Field
          label={`Within ${wholeMi} mi (${params.max_km.toFixed(1)} km)`}
          hint="Sperry's cutoff is 25 mi (40.2336 km)."
          type="range"
          min={1}
          max={31}
          step={1}
          value={wholeMi}
          aria-valuetext={`${wholeMi} miles`}
          onChange={(e) => setParams({ max_km: Number(e.target.value) * KM_PER_MI })}
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
const VIEWS = [
  ['list', 'List'],
  ['calendar', 'Calendar'],
]

function RankedList() {
  const g = useGridlock()
  const { ov, overlaps, params } = g
  const [all, setAll] = useState(false)
  const [order, setOrder] = useState('score')
  // a Sperry pair's chip (after expanding from their example): show its row in the list, wherever it ranks
  const showRow = useCallback(
    (o) => {
      setAll(true)
      if (g.tierFilter && o.tier !== g.tierFilter) g.setTierFilter(null)
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          const row = document.querySelector(`.gl-rows [data-pair="${CSS.escape(o.id)}"]`)
          row?.scrollIntoView({ block: 'center', behavior: 'smooth' })
          row?.focus({ preventScroll: true })
        }),
      )
    },
    [g],
  )
  const tier = g.tierFilter && ov.by_tier?.[g.tierFilter] ? g.tierFilter : null
  // the engine's order (Best match) is already grouped: building in the same months, at different times, timing
  // unknown, time passed (GROUPS in backend/gridlock.py); each group gets a heading. "Closest" and the tier filter only
  // re-order / narrow what it sent, each pair keeping its rank.
  const listed = useMemo(() => {
    const rows = tier ? overlaps.filter((o) => o.tier === tier) : overlaps
    if (order !== 'distance') return rows
    const d = (o) => pairDistance(o, params.method).km ?? Infinity
    return [...rows].sort((a, b) => d(a) - d(b) || (a.rank ?? 0) - (b.rank ?? 0))
  }, [overlaps, tier, order, params.method])
  const shown = all ? listed : listed.slice(0, 12)
  const groupCount = useMemo(() => {
    const c = {}
    for (const o of listed) c[o.group] = (c[o.group] || 0) + 1
    return c
  }, [listed])
  const total = ov.total_pairs || 0
  const flagged = ov.flagged ?? overlaps.length
  const cal = g.listView === 'calendar'
  const shared = useMemo(() => overlaps.filter((o) => o.same_window).length, [overlaps])

  return (
    <section className={`gl-ranked${cal ? ' gl-ranked--cal' : ''}`} aria-labelledby="gl-ranked-h">
      <div className="gl-listhead">
        <div className="gl-listhead__txt">
          <h2 id="gl-ranked-h" className="gl-listhead__h" tabIndex={-1}>
            {ov.status === 'loading' ? (
              'Comparing the plans…'
            ) : cal ? (
              <>
                <strong>
                  {fmtInt(shared)} shared build window{shared === 1 ? '' : 's'}
                </strong>{' '}
                among {fmtInt(flagged)} pair{flagged === 1 ? '' : 's'} within {limitMi(params.max_km)}
              </>
            ) : (
              <>
                <strong>
                  {fmtInt(tier ? listed.length : flagged)} pair{(tier ? listed.length : flagged) === 1 ? '' : 's'}
                </strong>{' '}
                within {limitMi(params.max_km)}
              </>
            )}
          </h2>
          {total > 0 && ov.status !== 'loading' && (
            <p className="gl-listhead__of">
              {cal ? (
                'the months both filed build windows cover: as filed, or from a derived start where a filing gives none'
              ) : (
                <>
                  of {fmtInt(total)} cross-state pairs compared ·{' '}
                  <Gloss tip={order === 'distance' ? 'Sorted by distance at the closest points; each pair keeps its best-match rank.' : GLOSS.rank}>
                    {order === 'distance' ? 'by distance' : 'how they are ordered'}
                  </Gloss>
                </>
              )}
            </p>
          )}
        </div>
        <div className="gl-listhead__tools">
          <div className="gl-sort gl-viewswitch" role="group" aria-label="Show the pairs as">
            {VIEWS.map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={g.listView === id ? 'is-on' : ''}
                aria-pressed={g.listView === id}
                onClick={() => g.setListView(id)}
              >
                {label}
              </button>
            ))}
          </div>
          {!cal && flagged > 1 && (
            <div className="gl-sort" role="group" aria-label="Sort the pairs">
              {ORDERS.map(([id, label]) => (
                <button key={id} type="button" className={order === id ? 'is-on' : ''} aria-pressed={order === id} onClick={() => setOrder(id)}>
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      {cal && <CalendarView />}
      {!cal && ov.status === 'loading' && <Loading label="Ranking the pairs…" />}
      {!cal && ov.status === 'error' && <ErrorBanner error={ov.error} onRetry={g.loadOverlaps} />}
      {!cal && (ov.status === 'ready' || ov.status === 'refreshing') && (
        <>
          {!g.pairs.length && <EmptyState title="Nothing to compare">Switch on DESC and at least one Georgia utility under Filters.</EmptyState>}
          {g.pairs.length > 0 && !flagged && (
            <EmptyState
              title={`No pairs within ${ov.limit_text || limitText(params.max_km)}`}
              action={
                params.max_km < g.sperryKm - 1e-6 ? (
                  <Button variant="secondary" onClick={() => g.setParams({ max_km: g.sperryKm })}>
                    Widen to 25 mi
                  </Button>
                ) : null
              }
            >
              Widen the distance under Filters.
            </EmptyState>
          )}
          <SperryMarks onShow={showRow} />
          {ov.truncated && <p className="gl-fine">The engine sent the top {fmtInt(overlaps.length)}; narrow the distance to see the rest ranked.</p>}
          {flagged > 0 && !g.draft && (
            <p className="gl-cta">
              Pick a pair below to see what building together saves.
            </p>
          )}
          <ol className={`gl-rows${ov.status === 'refreshing' ? ' gl-rows--stale' : ''}`} aria-busy={ov.status === 'refreshing' || undefined}>
            {shown.map((o, i) => (
              <Fragment key={o.id}>
                {order !== 'distance' && o.group && o.group !== shown[i - 1]?.group && (
                  <li className={`gl-rows__sep gl-rows__sep--${o.group}`}>
                    <Gloss tip={GLOSS.groups[o.group] || ''}>{o.group_label || o.group}</Gloss>
                    <span className="gl-rows__sepn">{fmtInt(groupCount[o.group] || 0)}</span>
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
  const marked = g.sperryMarks && !!o.sperry
  const when = whenOf(o)
  const rank = o.displayRank ?? o.rank
  // a DESC project the 2026-2030 list no longer carries (kept from the 2024-2028 list, marked)
  const earlier = [a, b].find((p) => p?.edition === '2024-2028') || null
  const near = nearEndsOnly(o, g.params.max_km)
  return (
    <li>
      <button
        type="button"
        className={`gl-row gl-row--${when.tone}${on ? ' is-on' : ''}${marked ? ' gl-row--sperry' : ''}`}
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
          <span className={`gl-row__meta bt-reason bt-reason--${when.tone}`}>{reasonLine(o)}</span>
          {/* a shared substation first (the engine lists these pairs above every tier); else the tier only when it isn't
              the usual one (crews and equipment: most pairs); the map key and Filters list all */}
          {(o.shared_station || o.tier !== 'crews' || o.sperry || earlier || near) && (
            <span className="gl-row__foot">
              {o.shared_station ? (
                <span className="gl-stationtag" title={`${GLOSS.station} ${o.shared_station.reason}.`}>
                  {SAME_STATION.en.tag(o.shared_station.name)}
                </span>
              ) : (
                o.tier !== 'crews' && <span className={`gl-tier gl-tier--${o.tier}`}>{o.tier_label}</span>
              )}
              {near && (
                <span className="bt-neartag" title="Within the distance only where their nearest ends meet: their centers are farther apart than 25 mi (Sperry's method).">
                  Close only at the nearest ends
                </span>
              )}
              {o.sperry && (
                <span
                  className={`gl-sperrytag${marked ? ' is-marked' : ''}`}
                  title={`${o.sperry}: this pair's number in Sperry's own worked example (their overlap list), which the pipeline reproduces`}
                >
                  In Sperry&apos;s example ({o.sperry})
                </span>
              )}
              {earlier && (
                <span className="gl-edtag" title={GLOSS.edition(earlier.edition_note)}>
                  DESC 2024–2028 list
                </span>
              )}
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
    <nav className="gl-railfoot bt-railfoot" aria-label="More views">
      <button type="button" className="gl-railfoot__side" onClick={() => g.setTab('projects')}>
        {n ? `All ${fmtInt(n)} projects` : 'All projects'}
      </button>
      <button type="button" className="gl-railfoot__side" onClick={() => g.enterSperry()}>
        Sperry&apos;s worked example
      </button>
      <button type="button" className="gl-railfoot__side" onClick={() => g.setTab('pipeline')}>
        How we built the data
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
        <h1 className="gl-title">{SUB_VIEWS[view]}</h1>
      </header>
      {g.conn.status === 'loading' && <Loading label="Reading the construction plans…" />}
      {g.conn.status === 'error' && <ErrorBanner error={g.conn.error} onRetry={g.reload} />}
      {g.conn.status === 'ready' &&
        (view === 'pipeline' ? (
          <PipelinePanel />
        ) : view === 'changes' ? (
          <ChangesView />
        ) : view === 'setaside' ? (
          <SetAsideView />
        ) : view === 'sperry' ? (
          <SperryView />
        ) : (
          <ProjectFinder />
        ))}
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
        {setAside ? `; ${fmtInt(setAside)} more set aside (see How we built the data)` : ''}. Only switched-on utilities are listed.
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
                  <span className="gl-item__who">{utilityShort(p.utility)}:</span>{' '}
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
