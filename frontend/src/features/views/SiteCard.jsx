import { useEffect, useState } from 'react'
import { api } from '../../api'
import MapLink from './MapLink'
import { STATUS_LABEL, fmt, fmt1, mwText, testHref } from './viewsKit'

// One data center's card: only sourced facts, each as reported (name, reported operator, place, reported MW,
// status, source links). The test is a campus of that reported size at that point on a SYNTHETIC grid model,
// not a prediction about the real project or the real utility. Undisclosed tenants are never named.

const cache = new Map()

const PRECISION = {
  approximate: 'The point is approximate: usually the city or site centre, not a surveyed location.',
  exact: 'The point is the reported site address.',
  'point of the matched entry': "The point is the same campus's point in another listing.",
}

export default function SiteCard({ id, onClose }) {
  const [got, setGot] = useState({ id: null, site: null, error: null }) // the last answer, and which site it was for
  const [nonce, setNonce] = useState(0) // Retry after an error

  useEffect(() => {
    if (cache.has(id)) return undefined
    let live = true
    api(`/api/views/datacenters/${encodeURIComponent(id)}`)
      .then((d) => {
        cache.set(id, d)
        if (live) setGot({ id, site: d, error: null })
      })
      .catch((e) => live && setGot({ id, site: null, error: e }))
    return () => {
      live = false
    }
  }, [id, nonce])

  const site = cache.get(id) || (got.id === id ? got.site : null)
  const error = got.id === id ? got.error : null

  if (error) {
    return (
      <section className="vw-card" aria-label="Data center">
        <p className="vw-error" role="alert">
          Couldn&apos;t load this site: {error.message}
        </p>
        <div className="row">
          <button
            type="button"
            className="vw-linkbtn"
            onClick={() => {
              setGot({ id: null, site: null, error: null })
              setNonce((n) => n + 1)
            }}
          >
            Retry
          </button>
          <button type="button" className="vw-linkbtn" onClick={onClose}>
            Close
          </button>
        </div>
      </section>
    )
  }
  if (!site) {
    return (
      <section className="vw-card" aria-label="Data center" aria-busy="true">
        <p className="muted">Loading the site…</p>
      </section>
    )
  }

  const href = site.can_test ? testHref(site) : null
  const place = [site.city, site.county && !/county/i.test(site.county) ? `${site.county} County` : site.county, site.state_name].filter(Boolean).join(', ')
  const longBasis = (site.mw_basis || '').length > 150
  const epoch = (site.also || []).filter((a) => a.origin === 'epoch-ai')
  const atlas = (site.also || []).filter((a) => a.origin === 'compute-atlas')

  return (
    <section className="vw-card" aria-label={`Data center: ${site.name}`}>
      <div className="vw-card__head">
        <p className="vw-kicker">
          {site.status_short || STATUS_LABEL[site.status] || site.status}
          {' · '}
          {site.kind === 'crypto_mining' ? 'crypto-mining site' : 'data center'}
        </p>
        <button type="button" className="vw-iconbtn" aria-label="Close the site card" onClick={onClose}>
          ×
        </button>
      </div>
      <h2 className="vw-card__name">{site.name}</h2>
      <dl className="vw-facts">
        <div>
          <dt>Operator, as reported</dt>
          <dd>{site.operator || 'Not reported'}</dd>
        </div>
        <div>
          <dt>Place</dt>
          <dd>{place || 'Not reported'}</dd>
        </div>
        {site.status_text && (
          <div>
            <dt>Status, as reported</dt>
            <dd>{site.status_text}</dd>
          </div>
        )}
        {(site.year || site.announced) && (
          <div>
            <dt>Timeline, as reported</dt>
            <dd>{site.year || `Announced ${site.announced}`}</dd>
          </div>
        )}
        <div>
          <dt>Reported size</dt>
          <dd>
            {site.mw ? (
              <>
                <strong className="vw-fig">{fmt(site.mw)} MW</strong>
                {site.size_note ? <span className="muted"> ({site.size_note})</span> : null}
                {site.mw_from ? <span className="muted"> · taken from {site.mw_from === 'epoch-ai' ? 'Epoch AI' : 'another listing'}</span> : null}
                {site.mw_planned && site.mw_operational && site.mw_planned > site.mw_operational ? (
                  <span className="muted">
                    {' '}
                    · {fmt(site.mw_operational)} MW operating, {fmt(site.mw_planned)} MW planned
                  </span>
                ) : null}
              </>
            ) : (
              'Not reported'
            )}
            {site.mw_basis && !longBasis && <span className="vw-basis">{site.mw_basis}</span>}
          </dd>
        </div>
        {site.share_of_model_load_pct != null && (
          <div>
            <dt>Scale</dt>
            <dd>
              {fmt1(site.share_of_model_load_pct)}% of {site.state_name}&apos;s model base load ({mwText(site.model_load_mw)}). A scale comparison only: the model holds no real campus.
            </dd>
          </div>
        )}
      </dl>
      {longBasis && (
        <details className="vw-details">
          <summary>How the size was reported</summary>
          <p>{site.mw_basis}</p>
        </details>
      )}
      {site.location_basis && (
        <details className="vw-details">
          <summary>How the place was reported</summary>
          <p>{site.location_basis}</p>
        </details>
      )}
      {!site.location_basis && site.precision && PRECISION[site.precision] && <p className="vw-fine">{PRECISION[site.precision]}</p>}

      <h3 className="vw-h4">Sources, as reported by</h3>
      {site.sources?.length ? (
        <ul className="vw-sources">
          {site.sources.map((s) => (
            <li key={s.url}>
              <a href={s.url} target="_blank" rel="noreferrer">
                {s.title || s.publisher || s.url}
              </a>
              {s.supports ? <span className="vw-fine"> supports: {s.supports}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="vw-fine">No source link on file.</p>
      )}
      <p className="vw-fine">
        Listing: {site.origin_label}
        {site.confidence ? ` · ${site.origin === 'curated' ? 'research confidence' : 'source confidence'} ${site.confidence}` : ''}
        {site.updated ? ` · updated ${site.updated}` : ''}
      </p>
      {(epoch.length > 0 || atlas.length > 0) && (
        <div className="vw-also">
          <h3 className="vw-h4">Also listed by another source</h3>
          <ul>
            {epoch.map((a) => (
              <li key={a.name + a.mw}>
                Epoch AI lists it as &ldquo;{a.name}&rdquo;{a.mw ? `: ${fmt(a.mw)} MW current power (an estimate)` : ' (no power figure)'}.
              </li>
            ))}
            {atlas.map((a) => (
              <li key={a.name}>Compute Atlas lists it as &ldquo;{a.name}&rdquo;.</li>
            ))}
          </ul>
        </div>
      )}

      <div className="vw-card__actions">
        {href ? (
          <MapLink className="btn" site={site}>
            {site.hypothetical ? 'Test a hypothetical campus here' : 'Test it on the grid'}
          </MapLink>
        ) : (
          <p className="vw-fine">
            {site.has_model ? 'This site has no reported size, so there is nothing to test yet.' : `${site.state_name || 'This place'} has no grid model in Overload (the synthetic models cover the lower 48).`}
          </p>
        )}
        {site.origin === 'curated' && (
          <a className="vw-link" href={`#/vote/${encodeURIComponent(site.id)}`}>
            Its page in Proposed data centers
          </a>
        )}
        {site.has_model && (
          <MapLink className="vw-link" state={site.state}>
            Open {site.state_name}&apos;s grid model
          </MapLink>
        )}
      </div>
      <p className="vw-fine">
        {site.hypothetical
          ? `A test is a hypothetical ${fmt(site.mw)} MW campus at this location on a synthetic grid model (Breakthrough Energy / Texas A&M): not a prediction about the real project or the real utility.`
          : 'A test is a campus of this reported size at this location on a synthetic grid model (Breakthrough Energy / Texas A&M): not a prediction about the real project or the real utility.'}
      </p>
    </section>
  )
}
