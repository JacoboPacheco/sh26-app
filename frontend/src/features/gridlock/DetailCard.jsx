import { useEffect, useRef } from 'react'
import { EmptyState } from '../../ui'
import { useGridlock } from './context'
import { KIND_LABEL, displayName, fmtBuiltAt, fmtDate, fmtKv, fmtMoney, osmUrl, sourceLink, toneOf, utilityName } from './format'
import './gridlock.css'

// A project's "Where this came from" (PDF page, raw text, OpenStreetMap matches, checks): a card over the map's
// right side when no pair is open, or inside the pair's sheet (PairSheet.jsx renders ProjectCard with Back).
export default function DetailCard() {
  const g = useGridlock()
  const { sel, close } = g
  useEffect(() => {
    if (sel?.kind !== 'project') return
    const onKey = (e) => e.key === 'Escape' && close()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [sel, close])
  if (sel?.kind !== 'project') return null
  return (
    <aside className="gl gl-card" aria-label="Where this came from">
      <ProjectCard sel={sel} onBack={sel.back ? g.back : null} onClose={close} />
    </aside>
  )
}

function CardHead({ eyebrow, title, sub, onBack, backLabel, onClose, autoFocus }) {
  // inside a pair's sheet the card opens over the steps (which go inert): its heading takes focus
  const h = useRef(null)
  useEffect(() => {
    if (autoFocus) h.current?.focus({ preventScroll: true })
  }, [autoFocus, title])
  return (
    <header className="gl-card__head">
      <div className="gl-card__nav">
        {onBack ? (
          <button type="button" className="gl-back" onClick={onBack}>
            <span aria-hidden="true">‹</span> Back to {backLabel}
          </button>
        ) : (
          <span className="gl-card__eyebrow">{eyebrow}</span>
        )}
        {onClose && (
          <button type="button" className="gl-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        )}
      </div>
      {onBack && <span className="gl-card__eyebrow">{eyebrow}</span>}
      <h2 className="gl-card__title" ref={h} tabIndex={autoFocus ? -1 : undefined}>
        {title}
      </h2>
      {sub && <p className="gl-card__sub">{sub}</p>}
    </header>
  )
}

// ------------------------------------------------------------------ project provenance
export function ProjectCard({ sel, onBack, onClose, backLabel, autoFocus = false }) {
  const g = useGridlock()
  const p = g.byId[sel.id]
  const backRank = sel.back ? (sel.back.overlap?.displayRank ?? sel.back.overlap?.rank) : null
  if (!p) {
    return (
      <>
        <CardHead eyebrow="Project" title={sel.id} onBack={onBack} backLabel={backLabel || 'the pair'} onClose={onClose} autoFocus={autoFocus} />
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
        autoFocus={autoFocus}
        onBack={onBack}
        onClose={onClose}
        backLabel={backLabel || `pair${backRank ? ` ${backRank}` : ''}`}
        eyebrow={
          <>
            <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
            {p.utility_name || utilityName(p.utility)}
            {p.state ? `, ${p.state}` : ''}
          </>
        }
        title={displayName(p.name)}
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
