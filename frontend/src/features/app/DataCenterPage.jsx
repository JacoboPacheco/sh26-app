import { useEffect, useState } from 'react'
import { STATES, fmt } from '../../geo'
import { useOverload } from '../../store'
import { Loading } from '../../ui'
import { MODEL_MW_MAX, VERDICT_TEXT, fetchEntry, normalize, placeText, statusText, testQuery, useCatalog, verdictOf } from './catalogData'
import { Optional, hasModule } from './optional'
import { go, href } from './router'
import { isKnownState } from './Workspace'

// One announced data center: only its sourced facts (name, reported developer, place, reported MW,
// status — each with a link), then "Test it", which opens its state's workspace with a campus of
// that reported size at that location. The test is on a synthetic model; the page says so.

function useEntry(id) {
  const cat = useCatalog()
  const listed = cat.entries?.find((e) => e.id === id) || null
  // a duplicate row folded into another campus isn't in the list but still has a detail page
  const inList = !!listed || !!cat.entries?.some((e) => (e.raw?.also_listed_as || []).some((x) => x.id === id))
  const [detail, setDetail] = useState(null)
  const [detailError, setDetailError] = useState(null)
  useEffect(() => {
    let live = true
    setDetail(null)
    setDetailError(null)
    if (cat.status !== 'ready') return undefined // the endpoint isn't there yet; the listed entry stands in
    if (!inList) return undefined // not in the catalog: nothing to fetch (the page says so), no 404s
    fetchEntry(id)
      .then((d) => live && setDetail(d))
      .catch((err) => live && setDetailError(err))
    return () => {
      live = false
    }
  }, [id, cat.status, inList])
  const entry = detail?.entry?.name ? { ...listed, ...detail.entry } : listed
  const test = detail?.test ?? listed?.test ?? null
  return { entry, test, cat, detailError }
}

// The catalog track's card (facts, sources, its test flexible and firm, "Test it in the workspace")
// when it's there; our facts page otherwise.
export function DataCenterLeft({ id }) {
  useFocusEntry(id)
  return (
    <div className="nx-pad nx-dccard">
      <Optional
        from="catalog/DataCenterCard"
        id={id}
        onPlaced={(e) => go(`/state/${e.state}`, testQuery(normalize(e)))}
        fallback={<Facts id={id} />}
        loading={<Facts id={id} />}
      />
    </div>
  )
}

// bring the campus into view on the national map
function useFocusEntry(id) {
  const { entry: e } = useEntry(id)
  const { mapRef, grid } = useOverload()
  const onUS = grid?.meta?.region === 'US'
  const lat = e?.lat
  const lon = e?.lon
  useEffect(() => {
    if (!Number.isFinite(lat) || !onUS) return undefined
    const t = setTimeout(() => mapRef.current?.focus([[lon, lat]], [lon, lat]), 60)
    return () => clearTimeout(t)
  }, [lat, lon, onUS, mapRef])
}

function Facts({ id }) {
  const { entry: e, cat } = useEntry(id)
  const { regions } = useOverload()

  if (!e) {
    if (cat.status === 'loading' || cat.status === 'idle' || cat.status === 'fallback') {
      return (
        <div className="stack">
          <Loading label="Loading this data center from the catalog…" />
          {cat.status === 'fallback' && (
            <p className="nx-note">
              The national catalog isn&apos;t available yet.{' '}
              <button type="button" className="nx-linkbtn" onClick={cat.retry}>
                Try again
              </button>
            </p>
          )}
          <a className="nx-link" href={href()}>
            Back to the map of America
          </a>
        </div>
      )
    }
    return (
      <div className="stack">
        <h1 className="nx-h2">No data center by that name</h1>
        <p className="muted">It may have been merged with a duplicate in the catalog.</p>
        <a className="nx-link" href={href()}>
          Back to the map of America
        </a>
      </div>
    )
  }

  const known = isKnownState(e.state, regions)
  const capped = (e.mw || 0) > MODEL_MW_MAX
  return (
    <article className="stack nx-dcpage">
      <p className="nx-eyebrow">{statusText(e.status)} · reported</p>
      <h1 className="nx-h1">{e.name}</h1>
      <p className="nx-dcpage__where">{[e.company, placeText(e)].filter(Boolean).join(' · ')}</p>
      <dl className="nx-dl nx-dl--facts">
        <div>
          <dt>Reported size</dt>
          <dd>{e.mw ? `${fmt(e.mw)} MW` : 'Not reported'}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{statusText(e.status)}</dd>
        </div>
        {e.year && (
          <div>
            <dt>Timing</dt>
            <dd>{e.year}</dd>
          </div>
        )}
        {e.county && (
          <div>
            <dt>County</dt>
            <dd>{e.county}</dd>
          </div>
        )}
      </dl>
      {e.mwBasis && <p className="nx-small nx-basis">{e.mwBasis}</p>}
      {e.locationBasis && <p className="nx-small muted">Location: {e.locationBasis}</p>}

      <div className="nx-cta">
        {known ? (
          <a className="btn nx-cta__btn" href={href(`/state/${e.state}`, testQuery(e))}>
            Test it on {STATES[e.state]?.name}&apos;s grid model
          </a>
        ) : (
          <p className="nx-note">{STATES[e.state]?.name || e.state || 'This state'} has no grid model in this app (the synthetic grid covers the lower 48).</p>
        )}
        <p className="nx-fine">
          {capped
            ? `Tested at the model's limit of ${fmt(MODEL_MW_MAX)} MW, not the reported ${fmt(e.mw)} MW. `
            : ''}
          A campus of this reported size at this location, tested on a synthetic grid model: not a prediction about the real project or the real utility.
        </p>
      </div>

      <section className="stack">
        <h2 className="panel-h">Sources</h2>
        {e.sources.length ? (
          <ul className="nx-sources">
            {e.sources.map((s) => (
              <li key={s.url}>
                <a href={s.url} target="_blank" rel="noreferrer">
                  {s.title || s.url}
                </a>
                {s.supports && <span className="muted nx-small"> · supports: {s.supports}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No source listed.</p>
        )}
        {e.confidence && <p className="muted nx-small">Research confidence: {e.confidence}.</p>}
      </section>
    </article>
  )
}

// With the catalog's card on the left (it carries the test): the other campuses in the same state.
// Without it: the catalog's test of this campus, flexible and firm.
export function DataCenterRight({ id }) {
  const { entry: e, test, detailError, cat } = useEntry(id)
  if (!e) return null
  if (hasModule('catalog/DataCenterCard')) return <SameState e={e} entries={cat.entries || []} />
  const t = readTest(test)
  const known = isKnownState(e.state, null)
  return (
    <div className="stack nx-pad nx-dctest">
      <h2 className="nx-h2">On the synthetic model</h2>
      {!t ? (
        <>
          <p className="muted">
            {detailError ? 'The catalog’s test isn’t available right now.' : 'Not tested yet.'} {known ? 'Test it to run the model now: it takes about a second.' : ''}
          </p>
          {known && (
            <a className="btn btn--secondary" href={href(`/state/${e.state}`, testQuery(e))}>
              Test it now
            </a>
          )}
        </>
      ) : (
        <>
          {t.verdict && <p className={`nx-verdict__label nx-tone--${t.verdict}`}>{VERDICT_TEXT[t.verdict]}</p>}
          <dl className="nx-dl">
            {t.sub && (
              <div>
                <dt>Connects at</dt>
                <dd>{t.sub}</dd>
              </div>
            )}
            {Number.isFinite(t.room) && (
              <div>
                <dt>Room at that substation</dt>
                <dd>{fmt(t.room)} MW</dd>
              </div>
            )}
            {Number.isFinite(t.over) && (
              <div>
                <dt>Lines over their limit</dt>
                <dd>{fmt(t.over)}</dd>
              </div>
            )}
          </dl>
          <div className="nx-svc">
            {[
              ['Flexible service', t.flex],
              ['Firm service', t.firm],
            ].map(([label, c]) =>
              c ? (
                <div key={label} className="nx-svc__col">
                  <p className="panel-h">{label}</p>
                  <p className="nx-svc__n">{fmt(c.people || 0)}</p>
                  <p className="muted nx-small">people without power (estimate){Number.isFinite(c.steps) ? ` · ${fmt(c.steps)} steps` : ''}</p>
                </div>
              ) : null,
            )}
          </div>
          {t.areas?.length > 0 && <p className="nx-small">Areas hit hardest: {t.areas.slice(0, 5).join(', ')}.</p>}
          <p className="muted nx-small">Open the workspace to replay it, change the time of day, or try firm service.</p>
        </>
      )}
    </div>
  )
}

function SameState({ e, entries }) {
  const others = entries.filter((x) => x.state === e.state && x.id !== e.id).sort((a, b) => (b.mw || 0) - (a.mw || 0))
  const name = STATES[e.state]?.name || e.stateName || e.state
  return (
    <div className="stack nx-pad">
      <h2 className="nx-h2">More in {name}</h2>
      {others.length ? (
        <ul className="nx-rows nx-rows--flush">
          {others.slice(0, 12).map((x) => {
            const v = verdictOf(x.test)
            return (
              <li key={x.id}>
                <a className="nx-row" href={href(`/dc/${encodeURIComponent(x.id)}`)}>
                  <span className="nx-row__name">{x.name}</span>
                  <span className="nx-row__mw">{x.mw ? `${fmt(x.mw)} MW` : 'n/a'}</span>
                  <span className="nx-row__meta">{[x.company, x.city].filter(Boolean).join(' · ')}</span>
                  <span className="nx-row__tags">
                    <span className="nx-tag">{statusText(x.status)}</span>
                    {v && <span className={`nx-tag nx-tag--${v}`}>{VERDICT_TEXT[v]}</span>}
                  </span>
                </a>
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="muted">No other announced campus in {name} in the catalog.</p>
      )}
      {isKnownState(e.state, null) && (
        <a className="btn btn--secondary nx-block" href={href(`/state/${e.state}`)}>
          Open {name}&apos;s grid model
        </a>
      )}
      <p className="nx-fine">Every test places one campus alone on its state&apos;s synthetic model. Sizes and places as reported by the linked sources.</p>
    </div>
  )
}

// The catalog test, whatever its exact shape: {verdict, sub, room, over, flex, firm, areas}
function readTest(test) {
  if (!test || typeof test !== 'object') return null
  const w = test.whatif || test
  const c = test.cascade || test
  const flex = c.flexible || c.flex || (c.people !== undefined ? c : null)
  const firm = c.firm && typeof c.firm === 'object' ? c.firm : null
  const pick = (x) =>
    x && {
      people: Number(x.people ?? x.people_without_power ?? 0),
      steps: Number(x.steps ?? x.total_steps ?? NaN),
      outcome: x.outcome,
    }
  const areas = (flex?.areas_top || test.areas_top || flex?.areas || []).map((a) => (typeof a === 'string' ? a : a?.name)).filter(Boolean)
  return {
    verdict: verdictOf(test),
    sub: w.sub_area || w.area || w.sub_name || null,
    room: Number(w.headroom_mw ?? w.room_mw ?? NaN),
    over: Number(Array.isArray(w.overloaded) ? w.overloaded.length : (w.overloaded ?? w.over ?? NaN)),
    flex: pick(flex),
    firm: pick(firm),
    areas,
  }
}
