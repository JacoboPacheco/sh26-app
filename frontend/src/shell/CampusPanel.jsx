import { useEffect, useId, useMemo, useState } from 'react'
import { api } from '../api'
import floridaFive from '../data/florida_five.json'
import { fmt } from '../geo'
import { MAX_POINTS, SEED_MARK, useOverload } from '../store'
import { Badge, Button, EmptyState, ErrorBanner, Field, Loading } from '../ui'
import DangerPanel from '../features/danger/DangerPanel'
import './campus.css'

// The slider covers the usual campus sizes; the custom box goes past it (the backend takes 1–50,000 MW).
const SLIDER_MIN = 100
const SLIDER_MAX = 2000
const SLIDER_STEP = 50
const CUSTOM_MAX = 50000
const QUICK_SIZES = [5000, 20000, 50000]
const PEOPLE_PER_MW_FALLBACK = 500 // used only until /api/grid's meta.people_per_mw arrives

// 500 -> "500 MW", 1,500 -> "1,500 MW", 2,000 -> "2 GW", 2,500 -> "2.5 GW", 50,000 -> "50 GW"
function fmtSize(mw) {
  const n = Math.round(mw)
  if (n >= 1000 && (n % 1000 === 0 || n > SLIDER_MAX)) {
    return `${(n / 1000).toLocaleString('en-US', { maximumFractionDigits: n >= 10000 ? 1 : 2 })} GW`
  }
  return `${fmt(n)} MW`
}

// 26,000,000 -> "26 million"; smaller counts in full
function fmtPeople(n) {
  if (n >= 1e6) return `${(n / 1e6).toLocaleString('en-US', { maximumFractionDigits: 1 })} million`
  return fmt(n)
}

// Campus mode: the data center you drop — its size, the saved scenarios, and what it does to the
// lines around it. The cascade itself runs from the timeline at the bottom.
export default function CampusPanel() {
  const o = useOverload()
  const { mw, setMw, site, result, solving, whatifError, subName } = o
  const peoplePerMw = o.grid?.meta?.people_per_mw || PEOPLE_PER_MW_FALLBACK
  const custom = mw < SLIDER_MIN || mw > SLIDER_MAX || mw % SLIDER_STEP !== 0
  const people = `as much power as ${fmtPeople(mw * peoplePerMw)} people use (estimate)`
  return (
    <div className="stack panel-body campus-panel">
      <div
        className="dc-chip"
        draggable
        onDragStart={(e) => {
          e.dataTransfer.setData('text/plain', 'data-center')
          e.dataTransfer.effectAllowed = 'copy'
        }}
      >
        <span className="dc-chip__mw">{fmtSize(mw)}</span>
        <span className="dc-chip__hint">AI campus — drag it onto the map, or click it. Hold Ctrl and click to add more points.</span>
      </div>
      <Field
        label="Size (MW)"
        type="range"
        min={SLIDER_MIN}
        max={SLIDER_MAX}
        step={SLIDER_STEP}
        value={Math.min(SLIDER_MAX, Math.max(SLIDER_MIN, mw))}
        onChange={(e) => setMw(Number(e.target.value))}
        hint={custom ? `Custom size ${fmt(mw)} MW: about ${people}` : `About ${people}`}
      />
      <CustomSize />
      <ExtraPoints />
      <DangerPanel />

      <RealProposals />
      <Scenarios />

      <ErrorBanner error={whatifError} />
      {!site ? null : !result ? (
        !whatifError && <Loading label="Solving the grid…" />
      ) : (
        <div className="stack site-result">
          <p>
            Connected at <strong>{result.sub_name}</strong> ({fmt(result.kv)} kV){solving && <span className="muted"> · updating…</span>}
          </p>
          {result.overloaded.length > 0 ? (
            <>
              <p className="verdict verdict--bad">{overLimitText(result.overloaded)}</p>
              <ul className="over-list">
                {result.overloaded.slice(0, 5).map((o2) => (
                  <li key={o2.id}>
                    <span>
                      {o2.from === o2.to ? `${subName(o2.from)} transformer` : `${subName(o2.from)} → ${subName(o2.to)}`}
                    </span>
                    <Badge tone="warn">{o2.pct.toFixed(0)} %</Badge>
                  </li>
                ))}
              </ul>
              {result.overloaded.length > 5 && <p className="muted">…and {result.overloaded.length - 5} more</p>}
            </>
          ) : (
            <p className="verdict verdict--ok">No line over limit.</p>
          )}
          <p>
            This site can take <strong>{fmt(result.headroom_mw)} MW</strong> before the first line overloads.
          </p>
        </div>
      )}

      <HeadroomToggle />
    </div>
  )
}

function Scenarios() {
  const { user, scenarios, scenarioError, loadScenarios, pickScenario, removeScenario, canSave, saveSite, saving } = useOverload()
  if (user === null) return null
  return (
    <div className="stack scenarios">
      <h3 className="panel-h">Saved sites</h3>
      <ErrorBanner error={scenarioError} onRetry={loadScenarios} />
      {scenarios === undefined ? (
        !scenarioError && <Loading />
      ) : scenarios.length === 0 ? (
        <EmptyState title="No saved sites">Drop the data center, then save the site.</EmptyState>
      ) : (
        <ul className="chips">
          {scenarios.map((sc) => (
            <li
              key={sc.id}
              title={sc.summary ? `${sc.summary.overloaded} over limit · ${fmt(sc.summary.headroom_mw)} MW headroom` : undefined}
            >
              <Button variant="secondary" onClick={() => pickScenario(sc)}>
                {sc.name}
              </Button>
              {/* everyone shares the demo account: the seeded demo scenarios can't be deleted from the page */}
              {!sc.note.includes(SEED_MARK) && (
                <Button variant="danger" onClick={() => removeScenario(sc.id)} aria-label={`Delete ${sc.name}`}>
                  ×
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canSave && (
        <div className="row">
          <Button variant="secondary" busy={saving} onClick={saveSite}>
            {saving ? 'Saving…' : 'Save this site'}
          </Button>
        </div>
      )}
    </div>
  )
}

export function HeadroomToggle() {
  const { headroomOn, toggleHeadroom, mw, headroom, headroomError, fetchHeadroom } = useOverload()
  const counts = headroom ? countBuckets(headroom, mw) : null
  const n = (k) => (counts ? ` · ${fmt(counts[k])} ${counts[k] === 1 ? 'substation' : 'substations'}` : '')
  return (
    <div className="stack headroom">
      <div className="row">
        <Button variant={headroomOn ? 'primary' : 'secondary'} aria-pressed={headroomOn} onClick={toggleHeadroom}>
          Where can {fmtSize(mw)} go?
        </Button>
      </div>
      <ErrorBanner error={headroomError} onRetry={fetchHeadroom} />
      {headroomOn && !headroom && !headroomError && <Loading />}
      {headroomOn && headroom && (
        <div className="legend">
          <strong>Headroom before the first overload</strong>
          <ul>
            <li>
              <span className="swatch swatch--ok" aria-hidden="true" /> Takes {fmtSize(mw)} or more{n('ok')}
            </li>
            <li>
              <span className="swatch swatch--mid" aria-hidden="true" /> {fmtSize(mw / 2)} to {fmtSize(mw)}{n('mid')}
            </li>
            <li>
              <span className="swatch swatch--low" aria-hidden="true" /> Under {fmtSize(mw / 2)}{n('low')}
            </li>
          </ul>
        </div>
      )}
    </div>
  )
}

function countBuckets(headroom, mw) {
  const c = { ok: 0, mid: 0, low: 0 }
  Object.values(headroom).forEach((v) => {
    if (v >= mw) c.ok++
    else if (v >= mw / 2) c.mid++
    else c.low++
  })
  return c
}

// A branch with both ends in one substation is a transformer; the map can't draw it, so count it apart.
export function overLimitText(overloaded) {
  const lines = overloaded.filter((o) => o.from !== o.to).length
  const xfmrs = overloaded.length - lines
  const parts = []
  if (lines) parts.push(`${lines} ${lines === 1 ? 'line' : 'lines'}`)
  if (xfmrs) parts.push(`${xfmrs} ${xfmrs === 1 ? 'transformer' : 'transformers'}`)
  return `${parts.join(' and ')} over limit`
}

// Optional size past the slider (up to 50 GW): type a number and Set (or Enter), or a quick pick.
// The points added with Ctrl+click (the main data center is the first click): each at the size it was
// added with, removable one by one. They are the same extra campuses AI-boom mode lists.
function ExtraPoints() {
  const { site, mw, extraSites, setExtraSites } = useOverload()
  if (!site && !extraSites.length) return null
  return (
    <div className="stack extra-points">
      <p className="muted extra-points__hint">
        Hold Ctrl (or Cmd) and click the map to add another point at {fmtSize(mw)}. {extraSites.length + (site ? 1 : 0)} of {MAX_POINTS}.
      </p>
      {extraSites.length > 0 && (
        <>
          <ul className="extra-points__list">
            {extraSites.map((c) => (
              <li key={c.id}>
                <span>
                  {c.metro || 'Point'} · {fmtSize(c.mw)}
                </span>
                <button type="button" className="extra-points__x" aria-label={`Remove the ${c.metro || 'extra'} point`} onClick={() => setExtraSites(extraSites.filter((x) => x.id !== c.id))}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
          <Button variant="secondary" onClick={() => setExtraSites([])}>
            Remove all extra points
          </Button>
        </>
      )}
    </div>
  )
}

function CustomSize() {
  const { mw, setMw } = useOverload()
  const id = useId()
  const errId = `${id}-err`
  const [draft, setDraft] = useState('')
  const [error, setError] = useState(null)
  const apply = (v) => {
    setDraft(String(v))
    setError(null)
    setMw(v)
  }
  const submit = (ev) => {
    ev.preventDefault()
    const v = Number(draft)
    if (draft.trim() === '' || !Number.isFinite(v) || v < 1 || v > CUSTOM_MAX) {
      setError(`Enter a size from 1 to ${fmt(CUSTOM_MAX)} MW.`)
      return
    }
    apply(Math.round(v))
  }
  return (
    <form className="campus-custom" onSubmit={submit} noValidate>
      <div className="campus-custom__row">
        <Field
          id={id}
          label="Custom size (MW)"
          type="number"
          inputMode="numeric"
          min={1}
          max={CUSTOM_MAX}
          step={1}
          placeholder={`Optional, up to ${fmt(CUSTOM_MAX)}`}
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            if (error) setError(null)
          }}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errId : undefined}
        />
        <Button type="submit" variant="secondary">
          Set
        </Button>
      </div>
      {error && (
        <p className="campus-custom__err" id={errId} role="alert">
          {error}
        </p>
      )}
      <div className="campus-custom__quick" role="group" aria-label="Quick sizes">
        {QUICK_SIZES.map((v) => (
          <Button key={v} variant="secondary" aria-pressed={mw === v} onClick={() => apply(v)}>
            {fmtSize(v)}
          </Button>
        ))}
      </div>
    </form>
  )
}

// The real, sourced data centers of the state on the map: pick one to test a campus of its reported size
// at its reported place. Florida leads with its five largest proposals (data/florida_five.json); every
// other state lists the national catalog's entries for it (features/catalog). Each test runs on a
// synthetic grid model: not a prediction about the real project or utility. Sources are shown as reported.
function RealProposals() {
  const { setMw, place, setMode, region, grid } = useOverload()
  const [pickedId, setPickedId] = useState('')
  const [places, setPlaces] = useState({}) // state code -> its data centers (GET /api/catalog/places: nothing is computed)
  const stateName = grid?.meta?.region_name || 'this state'
  // Fetched only when a state other than Florida is opened, and only that state's list: choosing a state
  // must not start the batch of engine tests behind /api/catalog (that ran a cascade per campus).
  useEffect(() => {
    if (region === 'US' || region === 'FL' || places[region]) return undefined
    let live = true
    api(`/api/catalog/places?state=${encodeURIComponent(region)}`)
      .then((d) => live && setPlaces((p) => ({ ...p, [region]: d.entries || [] })))
      .catch(() => live && setPlaces((p) => ({ ...p, [region]: [] })))
    return () => {
      live = false
    }
  }, [region, places])
  const loaded = region === 'FL' || !!places[region]
  const entries = useMemo(() => {
    if (region === 'US') return []
    if (region === 'FL') return floridaFive.entries
    return (places[region] || [])
      .filter((e) => Number.isFinite(e.lat) && Number.isFinite(e.lon) && e.mw > 0)
      .map((e) => ({ id: e.id, name: e.name, place: e.city || e.county || stateName, mw: e.mw, status: e.status, lat: e.lat, lon: e.lon, sources: e.sources }))
  }, [region, places, stateName])
  const picked = entries.find((e) => e.id === pickedId)
  const src = picked?.sources?.[0]
  if (region === 'US') return <p className="real__note">Click a state on the map, or pick one above, to see its real data centers.</p>
  return (
    <div className="stack real">
      <Field
        as="select"
        label={`Real data centers in ${stateName}`}
        value={picked ? pickedId : ''}
        onChange={(ev) => {
          const e = entries.find((x) => x.id === ev.target.value)
          setPickedId(ev.target.value)
          if (!e) return
          setMode('campus')
          setMw(Math.min(Math.round(e.mw), CUSTOM_MAX))
          place(e.lat, e.lon)
        }}
      >
        <option value="">{loaded ? 'Pick a real data center…' : 'Loading…'}</option>
        {entries.map((e) => (
          <option key={e.id} value={e.id}>
            {e.name} — {e.place}, {fmt(e.mw)} MW reported ({e.status})
          </option>
        ))}
      </Field>
      {loaded && !entries.length && <p className="real__note">No sourced data centers for {stateName} in the catalog yet.</p>}
      {picked && (
        <p className="real__status">
          <span className="real__meta">
            {picked.place} · {fmt(picked.mw)} MW reported · {picked.status}
          </span>
          {src?.url && (
            <a className="real__src" href={src.url} target="_blank" rel="noreferrer" title={src.title}>
              Source
            </a>
          )}
        </p>
      )}
      <p className="real__note">Reported sizes from news and company sources. Each test runs on a synthetic grid model: not a prediction about the real project or utility.</p>
    </div>
  )
}
