import { useEffect, useState } from 'react'
import { Badge, EmptyState, ErrorBanner, Loading } from '../../ui'
import { useGridlock } from './context'
import {
  KIND_LABEL,
  TIER_SHARE,
  fmtBuiltAt,
  fmtDate,
  fmtKv,
  fmtMoney,
  fmtRange,
  fmtPairDistance,
  fmtTimeline,
  osmUrl,
  sourceLink,
  toneOf,
  utilityName,
} from './format'
import './gridlock.css'

// The card over the map's right side: an opportunity (both projects, why it ranks, a rough
// estimate) or one project's "Where this came from" (PDF page, raw text, OSM matches, checks).
export default function DetailCard() {
  const g = useGridlock()
  const { sel, close } = g
  useEffect(() => {
    if (!sel) return
    const onKey = (e) => e.key === 'Escape' && close()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [sel, close])
  if (!sel) return null
  return (
    <aside className="gl gl-card" aria-label={sel.kind === 'overlap' ? 'Coordination opportunity' : 'Where this came from'}>
      {sel.kind === 'overlap' ? <OpportunityCard sel={sel} /> : <ProjectCard sel={sel} />}
    </aside>
  )
}

function CardHead({ eyebrow, title, sub, onBack, backLabel }) {
  const { close } = useGridlock()
  return (
    <header className="gl-card__head">
      <div className="gl-card__nav">
        {onBack ? (
          <button type="button" className="gl-link" onClick={onBack}>
            Back to {backLabel}
          </button>
        ) : (
          <span className="gl-card__eyebrow">{eyebrow}</span>
        )}
        <button type="button" className="gl-close" onClick={close} aria-label="Close">
          ×
        </button>
      </div>
      {onBack && <span className="gl-card__eyebrow">{eyebrow}</span>}
      <h2 className="gl-card__title">{title}</h2>
      {sub && <p className="gl-card__sub">{sub}</p>}
    </header>
  )
}

// ------------------------------------------------------------------ opportunity
function OpportunityCard({ sel }) {
  const g = useGridlock()
  const live = g.overlaps.find((o) => o.id === sel.id)
  const o = live || sel.overlap
  const a = g.byId[o.a]
  const b = g.byId[o.b]
  const share = o.share || TIER_SHARE[o.tier]
  const rank = o.displayRank ?? o.rank
  const method = g.params.method
  const dist = fmtPairDistance(o, method)
  const where =
    method === 'center'
      ? `Centers ${dist.replace(/ between centers$/, '')} apart`
      : o.crosses
        ? 'The two projects cross'
        : o.distance_km < 0.05
          ? 'The two projects meet'
          : `${dist} apart at the closest points`
  return (
    <>
      <CardHead eyebrow={`Opportunity ${rank ? `#${rank}` : ''}`.trim()} title={o.tier_label} sub={`${where}; ${fmtTimeline(o)}.`} />
      {!live && <p className="gl-note">Outside the current settings. Widen the distance to see it in the list again.</p>}

      <div className="gl-pair">
        <ProjectMini p={a} id={o.a} />
        <div className="gl-pair__link" aria-hidden="true">
          <span>{method === 'center' ? dist.replace(/ between centers$/, '') : o.crosses ? 'cross' : dist}</span>
        </div>
        <ProjectMini p={b} id={o.b} />
      </div>

      <section className="gl-sec">
        <h3>What they could share</h3>
        <p className="gl-share-line">{share}</p>
      </section>

      <section className="gl-sec">
        <h3>Why it ranks {rank ? `#${rank}` : ''}</h3>
        {o.reasons?.length ? (
          <ul className="gl-reasons">
            {o.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">Score {o.score ?? '–'}.</p>
        )}
        {o.reasons?.length > 0 && !o.reasons.some((r) => /^Score/.test(r)) && <p className="gl-fine">Score {o.score ?? '–'}.</p>}
      </section>

      <Estimate id={o.id} />

      <p className="gl-fine">
        These projects could coordinate. This compares public plans only; it doesn&apos;t say whether the utilities already work together.
      </p>
    </>
  )
}

function ProjectMini({ p, id }) {
  const g = useGridlock()
  if (!p) return <p className="muted">Project {id} is not in the current data.</p>
  const meta = [fmtKv(p.kv), KIND_LABEL[p.kind], p.in_service ? `in service ${fmtDate(p.in_service)}` : 'no in-service date'].filter(Boolean)
  return (
    <div className={`gl-mini gl-mini--${toneOf(p.utility)}`}>
      <span className="gl-mini__who">
        <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
        {utilityName(p.utility)}
      </span>
      <strong className="gl-mini__name">{p.name}</strong>
      <span className="gl-mini__meta">{meta.join(' · ')}</span>
      <span className="gl-mini__meta">
        Location: {p.confidence || 'unknown'} confidence
        {p.status ? ` · ${p.status}` : ''}
      </span>
      <button type="button" className="gl-link" onClick={() => g.openProject(p.id)}>
        Where this came from
      </button>
    </div>
  )
}

// GET /api/gridlock/estimate/<id>?window_months=: rough low-high items with their basis and source
function Estimate({ id }) {
  const g = useGridlock()
  const months = g.params.window_months
  const key = `${id}@${months}`
  const [st, setSt] = useState({ key: null })
  const [tries, setTries] = useState(0)
  useEffect(() => {
    if (!g.client) return
    let live = true
    g.client.estimate(id, months).then(
      (data) => live && setSt({ key, status: 'ready', data }),
      (error) => live && setSt({ key, status: 'error', error }),
    )
    return () => {
      live = false
    }
  }, [g.client, id, months, key, tries])
  const cur = st.key === key ? st : { status: 'loading' }
  return (
    <section className="gl-sec gl-est" aria-live="polite">
      <div className="gl-sec__row">
        <h3>What sharing could save</h3>
        <Badge>Rough estimate</Badge>
      </div>
      {cur.status === 'loading' && <Loading label="Estimating…" />}
      {cur.status === 'error' &&
        (/no overlap|not found|unknown|aren't a/i.test(cur.error.message) ? (
          <EmptyState title="No estimate for this pair">{cur.error.message}</EmptyState>
        ) : (
          <ErrorBanner error={cur.error} onRetry={() => setTries((n) => n + 1)} />
        ))}
      {cur.status === 'ready' && <EstimateBody e={cur.data} />}
    </section>
  )
}

function EstimateBody({ e }) {
  const items = e?.items || []
  const sources = e?.sources || []
  const urlFor = (title) => sources.find((s) => s.title === title)?.url
  if (!items.length) return <EmptyState title="Nothing to estimate for this pair" />
  return (
    <>
      {(e.total_low != null || e.total_high != null) && (
        <p className="gl-est__total">
          <strong>{fmtRange(e.total_low, e.total_high, e.unit)}</strong>{' '}
          <span className="muted">if the two projects share what their distance and timing allow</span>
        </p>
      )}
      <ul className="gl-est__items">
        {items.map((it, i) => (
          <li key={`${it.id || it.label}-${i}`}>
            <div className="gl-est__line">
              <span>{it.label}</span>
              <strong>{fmtRange(it.low, it.high, it.unit)}</strong>
            </div>
            {it.basis && <span className="gl-est__basis">{it.basis}</span>}
            <span className="gl-est__meta">
              {it.needs && <span className="gl-est__needs">Needs {it.needs}</span>}
              {it.source && <SourceRef s={typeof it.source === 'string' && urlFor(it.source) ? { title: it.source, url: urlFor(it.source) } : it.source} />}
            </span>
          </li>
        ))}
      </ul>
      {e.context?.length > 0 && (
        <details className="gl-details">
          <summary>Rough size of each project</summary>
          <ul>
            {e.context.map((c) => (
              <li key={c.project}>
                <strong>{c.project}</strong> {fmtRange(c.cost_low, c.cost_high)}
                {c.basis && <span className="gl-est__basis"> {c.basis}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {e.assumptions?.length > 0 && (
        <details className="gl-details">
          <summary>Assumptions ({e.assumptions.length})</summary>
          <ul>
            {e.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </details>
      )}
      {sources.length > 0 && (
        <details className="gl-details">
          <summary>Sources ({sources.length})</summary>
          <ul>
            {sources.map((s, i) => (
              <li key={i}>
                <SourceRef s={s} />
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  )
}

function SourceRef({ s }) {
  if (typeof s === 'string') {
    return /^https?:\/\//.test(s) ? (
      <a className="gl-est__src" href={s} target="_blank" rel="noreferrer">
        {s.replace(/^https?:\/\//, '').slice(0, 60)}
      </a>
    ) : (
      <span className="gl-est__src">{s}</span>
    )
  }
  return s?.url ? (
    <a className="gl-est__src" href={s.url} target="_blank" rel="noreferrer">
      {s.title || s.url}
    </a>
  ) : (
    <span className="gl-est__src">{s?.title || ''}</span>
  )
}

// ------------------------------------------------------------------ project provenance
function ProjectCard({ sel }) {
  const g = useGridlock()
  const p = g.byId[sel.id]
  const backRank = sel.back ? (sel.back.overlap?.displayRank ?? sel.back.overlap?.rank) : null
  if (!p) {
    return (
      <>
        <CardHead eyebrow="Project" title={sel.id} />
        <EmptyState title="This project is not in the current data" />
      </>
    )
  }
  const sources = g.summary?.sources || []
  const src = sources.find((s) => s.id === p.provenance?.source || s.file === p.provenance?.source || s.title === p.provenance?.source)
  const page = p.provenance?.page
  const link = sourceLink(src, page)
  const checkLabel = Object.fromEntries((g.summary?.report?.checks || []).map((c) => [c.id, c.label]))
  const rawDate = p.in_service_raw && p.in_service && p.in_service_raw !== p.in_service ? p.in_service_raw : null
  return (
    <>
      <CardHead
        onBack={sel.back ? g.back : null}
        backLabel={`opportunity${backRank ? ` #${backRank}` : ''}`}
        eyebrow={
          <>
            <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
            {p.utility_name || utilityName(p.utility)}
            {p.state ? `, ${p.state}` : ''}
          </>
        }
        title={p.name}
        sub={[fmtKv(p.kv), KIND_LABEL[p.kind], p.id].filter(Boolean).join(' · ')}
      />

      <dl className="gl-facts">
        <dt>In service</dt>
        <dd>
          {p.in_service ? fmtDate(p.in_service) : 'Not given'}
          {rawDate && <span className="gl-fine"> (filed as &ldquo;{rawDate}&rdquo;)</span>}
        </dd>
        {p.build_window && (
          <>
            <dt>Build window</dt>
            <dd>
              {fmtDate(p.build_window.start)} to {fmtDate(p.build_window.end)}
              {p.build_window.basis && <span className="gl-fine"> ({p.build_window.basis})</span>}
            </dd>
          </>
        )}
        {p.status && (
          <>
            <dt>Status</dt>
            <dd>{p.status}</dd>
          </>
        )}
        <dt>Cost</dt>
        <dd>{p.cost_usd != null ? fmtMoney(p.cost_usd) : <span className="muted">{p.utility === 'DESC' ? 'Not given' : 'Redacted in the public filing'}</span>}</dd>
        {p.zone && (
          <>
            <dt>Zone</dt>
            <dd>{p.zone}</dd>
          </>
        )}
        {p.teams_no && (
          <>
            <dt>TEAMS project</dt>
            <dd>{p.teams_no}</dd>
          </>
        )}
        {p.miles != null && (
          <>
            <dt>Length</dt>
            <dd>{p.miles} mi</dd>
          </>
        )}
      </dl>
      {p.cost_by_year && typeof p.cost_by_year === 'object' && <CostByYear c={p.cost_by_year} />}

      <section className="gl-sec">
        <h3>Where this came from</h3>
        <p className="gl-src">
          {src ? src.title : p.provenance?.source || 'Unknown source'}
          {page ? `, page ${page}` : ''}
          {p.provenance?.detail_page ? ` (detail on page ${p.provenance.detail_page})` : ''}
          {link && (
            <>
              {' · '}
              <a href={link.url} target="_blank" rel="noreferrer">
                {link.label}
              </a>
            </>
          )}
        </p>
        {src?.file && <p className="gl-fine">{src.file}</p>}
        {p.provenance?.text ? (
          <pre className="gl-raw" aria-label="Raw text as extracted">
            {p.provenance.text}
          </pre>
        ) : (
          <p className="muted">No raw text kept for this record.</p>
        )}
      </section>

      <section className="gl-sec">
        <div className="gl-sec__row">
          <h3>Located</h3>
          <ConfBadge c={p.confidence} />
        </div>
        <ul className="gl-endpoints">
          {(p.endpoints || []).map((e, i) => (
            <Endpoint key={`${e.name}-${i}`} e={e} />
          ))}
        </ul>
        {!p.endpoints?.length && <p className="muted">No endpoints were read from this record.</p>}
        {p.geometry?.basis && <p className="gl-fine">Drawn as: {p.geometry.basis}.</p>}
      </section>

      <section className="gl-sec">
        <h3>Checks</h3>
        {p.checks?.length ? (
          <ul className="gl-checklist">
            {p.checks.map((c, i) => (
              <li key={`${c.id}-${i}`} className={`gl-check gl-check--${c.status}`}>
                <CheckIcon status={c.status} />
                <span>
                  <strong>{c.label || checkLabel[c.id] || c.id}</strong> <span className="gl-check__detail">{c.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No checks recorded.</p>
        )}
      </section>

      {(p.description || p.need) && (
        <details className="gl-details">
          <summary>Description and need, as filed</summary>
          {p.description && <p>{p.description}</p>}
          {p.need && (
            <p>
              <strong>Need:</strong> {p.need}
            </p>
          )}
        </details>
      )}
      {g.summary?.built_at && <p className="gl-fine">Data built {fmtBuiltAt(g.summary.built_at)}.</p>}
    </>
  )
}

function CostByYear({ c }) {
  const rows = Object.entries(c).filter(([, v]) => v != null && v !== 0)
  if (!rows.length) return null
  return (
    <details className="gl-details">
      <summary>Estimated cost by year, as filed</summary>
      <table className="gl-table">
        <tbody>
          {rows.map(([y, v]) => (
            <tr key={y}>
              <th scope="row">{y}</th>
              <td>{typeof v === 'number' ? fmtMoney(v) : v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}

function Endpoint({ e }) {
  const url = e.osm?.url || osmUrl(e.osm)
  return (
    <li className="gl-ep">
      <div className="gl-ep__row">
        <strong>{e.name}</strong>
        <ConfBadge c={e.confidence} />
      </div>
      {e.raw && e.raw !== e.name && <span className="gl-fine">Filed as &ldquo;{e.raw}&rdquo;</span>}
      {e.osm ? (
        <span className="gl-ep__osm">
          OpenStreetMap {e.osm.type} {e.osm.id}
          {e.osm.name ? `, “${e.osm.name}”` : ''}
          {e.osm.operator ? ` (${e.osm.operator})` : ''}
          {url && (
            <>
              {' · '}
              <a href={url} target="_blank" rel="noreferrer">
                View
              </a>
            </>
          )}
        </span>
      ) : (
        <span className="gl-fine">No OpenStreetMap feature</span>
      )}
      {e.lat != null && e.lon != null && (
        <span className="gl-fine">
          {e.lat.toFixed(4)}, {e.lon.toFixed(4)}
        </span>
      )}
      {e.match && <span className="gl-ep__why">{e.match}</span>}
    </li>
  )
}

export function ConfBadge({ c }) {
  if (!c) return <span className="gl-conf gl-conf-b--none">not located</span>
  return <span className={`gl-conf gl-conf-b--${c}`}>{c} confidence</span>
}

export function CheckIcon({ status }) {
  const label = { pass: 'Passed', warn: 'Warning', fail: 'Failed' }[status] || status
  return (
    <span className={`gl-ci gl-ci--${status}`} role="img" aria-label={label}>
      {status === 'pass' ? '✓' : status === 'warn' ? '!' : '×'}
    </span>
  )
}
