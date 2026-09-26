import { useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Badge, Button, EmptyState, ErrorBanner, Loading } from '../../ui'
import { selectEntry, useCatalog, useCatalogEntry } from './catalogApi'
import { statusLabel, verdictOf } from './format'
import './catalog.css'

// One real campus: the sourced facts (each source one click away), then a campus of its reported size
// tested on its state's synthetic model — flexible and firm — and "Test it in the workspace".
// `id` defaults to the catalog's selection; `onPlaced(entry)` lets a host switch to its workspace view.
export default function DataCenterCard({ id: idProp, onClose, onPlaced }) {
  const { data: list, selected } = useCatalog()
  const id = idProp ?? selected
  const detail = useCatalogEntry(id)
  const fromList = list?.entries.find((e) => e.id === id) || null
  const e = detail.data || fromList

  if (!id) return <EmptyState title="Pick a campus">Choose one from the list to see its sources and what it does to its state&apos;s grid model.</EmptyState>
  if (!e) return detail.error ? <ErrorBanner error={detail.error} onRetry={detail.retry} /> : <Loading label="Loading the campus…" />
  const close = () => {
    selectEntry(null)
    onClose?.()
  }
  const where = [e.city, e.county, e.state_name].filter(Boolean).join(', ')

  return (
    <article className="cat-card stack" aria-labelledby={`cat-card-${e.id}`}>
      <header className="cat-card__head">
        <div>
          <h3 id={`cat-card-${e.id}`} className="cat-card__name" tabIndex={-1}>
            {e.name}
          </h3>
          <p className="muted">
            {e.company} · {where}
          </p>
        </div>
        {(onClose || !idProp) && (
          <Button variant="secondary" onClick={close} aria-label={`Close ${e.name}`}>
            Close
          </Button>
        )}
      </header>

      <dl className="cat-facts">
        <div>
          <dt>Reported size</dt>
          <dd>
            <strong>{fmt(e.mw || 0)} MW</strong>
            {e.mw_basis && <span className="cat-facts__basis">{e.mw_basis}</span>}
          </dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>
            {statusLabel(e.status)}
            {e.year && <span className="muted"> · {e.year}</span>}
          </dd>
        </div>
        <div>
          <dt>Location</dt>
          <dd>
            Approximate{e.location_basis && <span className="cat-facts__basis">{e.location_basis}</span>}
          </dd>
        </div>
        <div>
          <dt>Sourcing</dt>
          <dd>
            {e.confidence[0].toUpperCase() + e.confidence.slice(1)} confidence{e.ai ? ' · AI campus' : ''}
            {e.verification && (
              <details className="cat-details">
                <summary>How this entry was checked</summary>
                <p>{e.verification}</p>
              </details>
            )}
          </dd>
        </div>
      </dl>

      <section className="stack cat-sources" aria-label="Sources">
        <h4 className="cat-h">As reported by</h4>
        {e.sources.length ? (
          <ul className="cat-sources__list">
            {e.sources.map((s) => (
              <li key={s.url}>
                <a href={s.url} target="_blank" rel="noreferrer noopener">
                  {s.title}
                </a>
                {s.supports && <span className="cat-facts__basis">Supports: {s.supports}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No web source recorded for this entry.</p>
        )}
        {e.also_listed_as?.length > 0 && (
          <p className="cat-facts__basis">Also listed as {e.also_listed_as.map((a) => a.name).join('; ')} (merged: same campus).</p>
        )}
      </section>

      <TestResult entry={e} detail={detail} onPlaced={onPlaced} />
    </article>
  )
}

function TestResult({ entry: e, detail, onPlaced }) {
  const t = detail.data?.test || e.test
  const full = !!detail.data
  return (
    <section className="stack cat-test" aria-label="Test on the synthetic model" aria-busy={detail.loading || undefined}>
      <h4 className="cat-h">On {e.state_name}&apos;s synthetic grid model</h4>
      {!t ? (
        detail.error ? (
          <ErrorBanner error={detail.error} onRetry={detail.retry} />
        ) : (
          <Loading label={`Testing ${fmt(e.mw || 0)} MW on the ${e.state_name} model…`} />
        )
      ) : !t.tested ? (
        <p className="cat-untested">{t.reason}</p>
      ) : (
        <>
          <p className="cat-sentence">
            <VerdictChip test={t} /> {t.sentence}
          </p>
          <dl className="cat-nums">
            <div>
              <dt>Room at the site</dt>
              <dd>{fmt(t.headroom_mw)} MW</dd>
            </div>
            <div>
              <dt>Over their limits</dt>
              <dd>{t.overloaded ? t.over_text : 'None'}</dd>
            </div>
            <div>
              <dt>Connects at</dt>
              <dd>
                {t.sub_name} · {fmt(t.kv)} kV · {fmt(t.km)} km away
              </dd>
            </div>
          </dl>
          {t.overloaded > 0 && (
            <div className="cat-runs">
              <Run title="Flexible" hint="the plain cascade: the campus's own lines may trip" run={t.flexible} full={full} />
              <Run title="Firm" hint="kept on: the grid operator cuts other customers instead" run={t.firm} full={full} firm />
            </div>
          )}
          {t.why && <p className="cat-why">{t.why}</p>}
          {detail.error && <ErrorBanner error={detail.error} onRetry={detail.retry} />}
        </>
      )}
      <p className="cat-note">{detail.data?.frame || 'A campus of this reported size at this location, tested on a synthetic grid model: not a prediction about the real project or the real utility.'}</p>
      {t?.tested && <Workspace key={e.id} entry={e} test={t} max={detail.data?.workspace_max_mw || 5000} onPlaced={onPlaced} />}
    </section>
  )
}

function VerdictChip({ test }) {
  const v = verdictOf(test)
  return (
    <span className={`cat-chip cat-chip--${v.key}`} title={v.title}>
      {v.key === 'outage' ? 'Outage in the model' : v.label}
    </span>
  )
}

function Run({ title, hint, run, full, firm = false }) {
  return (
    <div className="cat-run">
      <p className="cat-run__title">
        <strong>{title}</strong> <span className="muted">· {hint}</span>
      </p>
      <p className="cat-run__people">
        {fmt(run.people)} <span className="cat-run__unit">people without power (estimate)</span>
      </p>
      <p className="muted cat-run__meta">
        {run.steps} {run.steps === 1 ? 'step' : 'steps'}
        {run.capped ? ' (stopped at the cap)' : ''}
        {run.site_cut_off ? ' · the campus is cut off' : firm && run.firm_held ? ' · the campus stays on' : ''}
        {run.shed_mw > 0 && ` · ${fmt(run.shed_mw)} MW of customers cut on purpose`}
      </p>
      {full && run.areas?.length > 0 && (
        <ul className="cat-areas" aria-label={`${title}: areas hit most`}>
          {run.areas.map((a) => (
            <li key={a.area}>
              <span>{a.area}</span>
              <span className="muted">{fmt(a.people)} (est.)</span>
            </li>
          ))}
          {run.area_count > run.areas.length && <li className="muted">and {run.area_count - run.areas.length} more areas</li>}
        </ul>
      )}
    </div>
  )
}

// Put this campus on the live map: its state, its location, its reported size (up to the workspace's limit).
function Workspace({ entry: e, test, max, onPlaced }) {
  const o = useOverload()
  const [placed, setPlaced] = useState(null) // null | {ok, text}
  const mw = Math.min(e.mw || 0, max)
  const go = () => {
    if (!o) return
    if (o.region === e.state) {
      o.setMode?.('campus')
      o.setMw(mw)
      o.place(e.lat, e.lon)
    } else if (!o.setRegion(e.state, { place: [e.lat, e.lon], mw })) {
      setPlaced({ ok: false, text: `The ${e.state_name} model isn't available.` })
      return
    }
    setPlaced({ ok: true, text: `Placed ${fmt(mw)} MW at ${test.sub_name} on the ${e.state_name} model.` })
    onPlaced?.(e)
  }
  return (
    <div className="cat-go stack">
      <div className="row">
        <Button onClick={go} disabled={!o}>
          Test it in the workspace
        </Button>
      </div>
      {e.mw > max && (
        <p className="cat-facts__basis">
          The workspace goes up to {fmt(max)} MW, so it opens at {fmt(max)} MW; the result above is for the full {fmt(e.mw)} MW.
        </p>
      )}
      {placed && (
        <p className={placed.ok ? 'cat-go__done' : 'cat-untested'} role="status">
          {placed.text}
        </p>
      )}
      {!o && <Badge>Open the app to test it on the map</Badge>}
    </div>
  )
}
