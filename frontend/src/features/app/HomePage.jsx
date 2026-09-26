import { useCallback, useId, useMemo, useRef, useState } from 'react'
import { useMapView } from '../../GridMap'
import { STATES, fmt } from '../../geo'
import { useOverload } from '../../store'
import { Button, ErrorBanner, Field } from '../../ui'
import { VERDICT_TEXT, placeText, statusText, useCatalog, verdictOf } from './catalogData'
import { Optional, hasModule } from './optional'
import { go, href } from './router'

// Home: the U.S. map of announced AI data centers. Left: the question, the ways in, and the
// catalog as a list; the map: every campus as a dot sized by its reported MW; right: the totals.

export const HERO = { code: 'FL', lat: 26.64, lon: -81.87, mw: 1500 } // Fort Myers: the demo's hero case

export function useHomeState() {
  const [hoverId, setHoverId] = useState(null)
  const [dropMw, setDropMw] = useState(500)
  const [notice, setNotice] = useState(null)
  const [filters, setFilters] = useState({ q: '', state: '', status: '', sort: 'mw' })
  const searchRef = useRef(null)
  return { hoverId, setHoverId, dropMw, setDropMw, notice, setNotice, filters, setFilters, searchRef }
}

const r0 = (mw) => 1.1 + Math.sqrt(Math.max(mw || 0, 0)) / 11 // map units at zoom 1: 100 MW ≈ 2, 1 GW ≈ 4, 11 GW ≈ 10.6

export function useFiltered(entries, filters) {
  return useMemo(() => {
    const q = filters.q.trim().toLowerCase()
    let list = (entries || []).filter((e) => {
      if (filters.state && e.state !== filters.state) return false
      if (filters.status && e.status !== filters.status) return false
      if (!q) return true
      return [e.name, e.company, e.city, e.state, STATES[e.state]?.name].some((s) => s && String(s).toLowerCase().includes(q))
    })
    const by = {
      mw: (a, b) => (b.mw || 0) - (a.mw || 0),
      name: (a, b) => a.name.localeCompare(b.name),
      state: (a, b) => (a.state || '').localeCompare(b.state || '') || (b.mw || 0) - (a.mw || 0),
      risk: (a, b) => riskRank(b) - riskRank(a) || (b.mw || 0) - (a.mw || 0),
    }[filters.sort || 'mw']
    list = [...list].sort(by)
    return list
  }, [entries, filters])
}
const riskRank = (e) => ({ cascades: 3, over: 2, holds: 1 })[verdictOf(e.test)] || 0

// ------------------------------------------------------------------ left
export function HomeLeft({ home }) {
  const cat = useCatalog()
  const [door, setDoor] = useState(null)
  const doorId = useId()
  const n = cat.entries?.length || 0
  // the same count the national totals use: campuses in play (paused or canceled ones are listed, not counted)
  const active = Number.isFinite(cat.totals?.campuses) ? cat.totals.campuses : (cat.entries || []).filter((e) => e.raw?.counted !== false).length
  const other = Math.max(0, n - active)
  return (
    <div className="nx-home">
      <div className="nx-pad nx-home__intro">
        <h1 className="nx-hook">When the next AI data center plugs in, whose lights go out?</h1>
        <p className="nx-lede">
          {cat.status === 'ready'
            ? `${fmt(active)} data center campuses announced, being built or running across the U.S.${other ? ` (plus ${fmt(other)} paused or canceled)` : ''}, each on the map at its reported size. Test any of them on a synthetic model of its state's grid.`
            : "Every dot is a reported AI data center. Test any of them on a synthetic model of its state's grid."}
        </p>
        <div className="nx-hero">
          <a className="btn nx-hero__btn" href={href(`/state/${HERO.code}`, { lat: HERO.lat, lon: HERO.lon, mw: HERO.mw })}>
            Start in Florida
          </a>
          <span className="nx-hero__cap">A 1,500 MW campus at Fort Myers: calm at 500 MW, a blackout at 1,500.</span>
        </div>
        <ul className="nx-doors" aria-label="Ways in">
          <li>
            <button
              type="button"
              className="nx-door"
              onClick={() => {
                setDoor(null)
                home.searchRef.current?.focus()
                home.searchRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
              }}
            >
              <span className="nx-door__t">Test a real data center</span>
              <span className="nx-door__s">Pick one from the list below or a dot on the map</span>
            </button>
          </li>
          <li>
            <button type="button" className="nx-door" aria-expanded={door === 'drop'} aria-controls={`${doorId}-drop`} onClick={() => setDoor(door === 'drop' ? null : 'drop')}>
              <span className="nx-door__t">Drop one anywhere</span>
              <span className="nx-door__s">Choose a size, then click any state</span>
            </button>
            {door === 'drop' && (
              <div className="nx-door__body" id={`${doorId}-drop`}>
                <Field
                  label={`Size: ${fmt(home.dropMw)} MW`}
                  type="range"
                  min={100}
                  max={2000}
                  step={50}
                  value={home.dropMw}
                  onChange={(e) => home.setDropMw(Number(e.target.value))}
                  hint="Now click anywhere in the lower 48: that state's grid opens with the campus plugged in there."
                />
              </div>
            )}
          </li>
          <li>
            <button type="button" className="nx-door" aria-expanded={door === 'area'} aria-controls={`${doorId}-area`} onClick={() => setDoor(door === 'area' ? null : 'area')}>
              <span className="nx-door__t">Is my area at risk?</span>
              <span className="nx-door__s">Find your town on your state&apos;s grid</span>
            </button>
            {door === 'area' && (
              <div className="nx-door__body" id={`${doorId}-area`}>
                <AreaDoor />
              </div>
            )}
          </li>
        </ul>
        {home.notice && (
          <p className="nx-notice" role="status">
            {home.notice}{' '}
            <button type="button" className="nx-linkbtn" onClick={() => home.setNotice(null)}>
              Dismiss
            </button>
          </p>
        )}
      </div>
      <CatalogList home={home} cat={cat} />
    </div>
  )
}

function AreaDoor() {
  const { regions } = useOverload()
  const [code, setCode] = useState('FL')
  const list = useMemo(
    () =>
      (regions?.length ? regions.map((r) => [r.code, r.name]) : Object.entries(STATES).filter(([c]) => c !== 'DC').map(([c, s]) => [c, s.name])).sort((a, b) =>
        a[1].localeCompare(b[1]),
      ),
    [regions],
  )
  return (
    <form
      className="nx-door__form"
      onSubmit={(e) => {
        e.preventDefault()
        go(`/state/${code}`, { panel: 'area' })
      }}
    >
      <Field as="select" label="Your state" value={code} onChange={(e) => setCode(e.target.value)}>
        {list.map(([c, name]) => (
          <option key={c} value={c}>
            {name}
          </option>
        ))}
      </Field>
      <Button type="submit">Find my area</Button>
    </form>
  )
}

// The catalog as a list: search, filters, sort. A row opens the data center's page.
function CatalogList({ home, cat }) {
  const { filters, setFilters } = home
  const list = useFiltered(cat.entries, filters)
  const states = useMemo(() => [...new Set((cat.entries || []).map((e) => e.state).filter(Boolean))].sort(), [cat.entries])
  const statuses = useMemo(() => [...new Set((cat.entries || []).map((e) => e.status).filter(Boolean))].sort(), [cat.entries])
  const hasTests = (cat.entries || []).some((e) => verdictOf(e.test))
  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }))
  const listId = useId()
  return (
    <section className="nx-cat" aria-labelledby={`${listId}-h`}>
      <div className="nx-pad nx-cat__head">
        <h2 className="nx-h2" id={`${listId}-h`}>
          Announced AI data centers {cat.entries ? <span className="nx-count">{fmt(list.length)}</span> : null}
        </h2>
        {cat.status === 'fallback' && (
          <p className="nx-note" role="status">
            The national catalog is loading. Meanwhile, here are Florida&apos;s five sourced proposals.{' '}
            <button type="button" className="nx-linkbtn" onClick={cat.retry}>
              Try again
            </button>
          </p>
        )}
        {cat.progress && (
          <p className="nx-note" role="status">
            Testing each campus on its state&apos;s model: {fmt(cat.progress.done)} of {fmt(cat.progress.total)}
          </p>
        )}
        <div className="field">
          <label htmlFor={`${listId}-q`}>Search</label>
          <input
            id={`${listId}-q`}
            ref={home.searchRef}
            type="search"
            value={filters.q}
            onChange={set('q')}
            placeholder="Name, company, city or state"
            autoComplete="off"
          />
        </div>
        <div className="nx-cat__filters">
          <Field as="select" label="State" value={filters.state} onChange={set('state')}>
            <option value="">All states</option>
            {states.map((s) => (
              <option key={s} value={s}>
                {STATES[s]?.name || s}
              </option>
            ))}
          </Field>
          <Field as="select" label="Status" value={filters.status} onChange={set('status')}>
            <option value="">Any status</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {statusText(s)}
              </option>
            ))}
          </Field>
          <Field as="select" label="Sort" value={filters.sort} onChange={set('sort')}>
            <option value="mw">Largest first</option>
            {hasTests && <option value="risk">Most at risk first</option>}
            <option value="name">Name</option>
            <option value="state">State</option>
          </Field>
        </div>
      </div>
      {cat.status === 'loading' && !cat.entries ? (
        <p className="nx-pad muted" role="status">
          Loading the catalog…
        </p>
      ) : list.length === 0 ? (
        <div className="nx-pad">
          <p className="muted">No data center matches. </p>
          <button type="button" className="nx-linkbtn" onClick={() => setFilters({ q: '', state: '', status: '', sort: filters.sort })}>
            Clear the filters
          </button>
        </div>
      ) : (
        <ul className="nx-rows">
          {list.map((e) => {
            const v = verdictOf(e.test)
            return (
              <li key={e.id}>
                <a
                  className={`nx-row${home.hoverId === e.id ? ' nx-row--hi' : ''}`}
                  href={href(`/dc/${encodeURIComponent(e.id)}`)}
                  onMouseEnter={() => home.setHoverId(e.id)}
                  onMouseLeave={() => home.setHoverId(null)}
                  onFocus={() => home.setHoverId(e.id)}
                  onBlur={() => home.setHoverId(null)}
                >
                  <span className="nx-row__name">{e.name}</span>
                  <span className="nx-row__mw">{e.mw ? `${fmt(e.mw)} MW` : 'size n/a'}</span>
                  <span className="nx-row__meta">
                    {[e.company, placeText(e)].filter(Boolean).join(' · ')}
                  </span>
                  <span className="nx-row__tags">
                    <span className="nx-tag">{statusText(e.status)}</span>
                    {v && <span className={`nx-tag nx-tag--${v}`}>{VERDICT_TEXT[v]}</span>}
                  </span>
                </a>
              </li>
            )
          })}
        </ul>
      )}
      {cat.error && cat.status !== 'fallback' && <ErrorBanner error={cat.error} onRetry={cat.retry} />}
      <p className="nx-pad nx-fine">
        Reported sizes and locations from news and company sources, linked on each page; locations are approximate. Tests run on synthetic grid models:
        not predictions about real projects or utilities.
      </p>
    </section>
  )
}

// ------------------------------------------------------------------ the map layer
export function DataCentersLayer({ home, focusId }) {
  const { k, project, region } = useMapView()
  const cat = useCatalog()
  const list = useFiltered(cat.entries, home.filters)
  const dots = useMemo(() => {
    if (region !== 'US') return []
    return [...list]
      .sort((a, b) => (b.mw || 0) - (a.mw || 0)) // big first, so small dots sit on top
      .map((e) => {
        const [x, y] = project(e.lon, e.lat)
        return { e, x, y, r: r0(e.mw), v: verdictOf(e.test) }
      })
  }, [list, project, region])
  const open = useCallback((e) => go(`/dc/${encodeURIComponent(e.id)}`), [])
  if (!dots.length) return null
  const s = 1 / Math.pow(k, 0.7) // dots shrink as you zoom in, a little slower than the map grows
  const hi = home.hoverId || focusId
  const hot = hi ? dots.find((d) => d.e.id === hi) : null
  return (
    <g className="nx-dcs">
      {dots.map(({ e, x, y, r, v }) => (
        <circle
          key={e.id}
          className={`nx-dc${v ? ` nx-dc--${v}` : ''}${/paus|cancel/i.test(e.status || '') ? ' nx-dc--paused' : ''}${e.id === hi ? ' nx-dc--hi' : ''}`}
          cx={x}
          cy={y}
          r={r * s}
          onPointerDown={(ev) => ev.stopPropagation()}
          onClick={() => open(e)}
          onMouseEnter={() => home.setHoverId(e.id)}
          onMouseLeave={() => home.setHoverId(null)}
        >
          <title>{`${e.name}: ${e.mw ? `${fmt(e.mw)} MW reported` : 'size not reported'}, ${placeText(e)}`}</title>
        </circle>
      ))}
      {hot && (
        <g className="nx-dc-label" transform={`translate(${hot.x} ${hot.y})`} pointerEvents="none">
          <circle className="nx-dc-ring" r={hot.r * s + 3 / k} />
          <text x={hot.r * s + 6 / k} y={4 / k} fontSize={12 / k}>
            {hot.e.name} · {hot.e.mw ? `${fmt(hot.e.mw)} MW` : 'size n/a'}
          </text>
        </g>
      )}
    </g>
  )
}

// ------------------------------------------------------------------ right
export function HomeRight({ home }) {
  const cat = useCatalog()
  const entries = useMemo(() => cat.entries || [], [cat.entries])
  const stats = useMemo(() => {
    const byState = new Map()
    let total = 0
    entries.forEach((e) => {
      const mw = e.mw || 0
      total += mw
      byState.set(e.state, (byState.get(e.state) || 0) + mw)
    })
    const top = [...byState.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
    const tested = entries.filter((e) => verdictOf(e.test))
    const risky = tested.filter((e) => verdictOf(e.test) !== 'holds').length
    return { total, states: byState.size, top, tested: tested.length, risky }
  }, [entries])
  const max = stats.top[0]?.[1] || 1
  return (
    <div className="stack nx-pad nx-natl">
      <Optional
        from="catalog/NationalStats"
        onPickState={(code) => home.setFilters((f) => ({ ...f, state: code || '' }))}
        loading={null}
        fallback={
          <section className="stack">
            <h2 className="nx-h2">Across America</h2>
            <dl className="nx-kpis">
              <div>
                <dt>Campuses</dt>
                <dd>{fmt(entries.length)}</dd>
              </div>
              <div>
                <dt>Reported size</dt>
                <dd>{fmt(stats.total / 1000)} GW</dd>
              </div>
              <div>
                <dt>States</dt>
                <dd>{fmt(stats.states)}</dd>
              </div>
            </dl>
            {stats.tested > 0 && (
              <p className="nx-small">
                On their states&apos; synthetic models, <strong>{fmt(stats.risky)}</strong> of {fmt(stats.tested)} tested campuses push a line past its limit at
                their reported size.
              </p>
            )}
            {cat.status === 'fallback' && <p className="muted nx-small">National totals appear when the catalog loads.</p>}
          </section>
        }
      />
      {stats.top.length > 1 && !hasModule('catalog/NationalStats') && (
        <section className="stack">
          <h3 className="panel-h">Reported MW by state</h3>
          <ul className="nx-sbars">
            {stats.top.map(([code, mw]) => (
              <li key={code}>
                <button
                  type="button"
                  className={`nx-sbar${home.filters.state === code ? ' nx-sbar--on' : ''}`}
                  aria-pressed={home.filters.state === code}
                  onClick={() => home.setFilters((f) => ({ ...f, state: f.state === code ? '' : code }))}
                >
                  <span className="nx-sbar__name">{STATES[code]?.name || code}</span>
                  <span className="nx-sbar__track" aria-hidden="true">
                    <span className="nx-sbar__fill" style={{ width: `${(mw / max) * 100}%` }} />
                  </span>
                  <span className="nx-sbar__v">{fmt(mw)} MW</span>
                </button>
              </li>
            ))}
          </ul>
          <p className="muted nx-small">Click a state to filter the list and the map.</p>
        </section>
      )}
      <section className="stack nx-key">
        <h3 className="panel-h">On the map</h3>
        <ul>
          <li>
            <svg width="44" height="22" viewBox="0 0 44 22" aria-hidden="true">
              <circle className="nx-dc" cx="6" cy="11" r="2.4" />
              <circle className="nx-dc" cx="17" cy="11" r="4.3" />
              <circle className="nx-dc" cx="33" cy="11" r="9" />
            </svg>
            <span>An announced AI data center, sized by its reported MW (100 MW, 1 GW, 5 GW).</span>
          </li>
          <li>
            <svg width="44" height="22" viewBox="0 0 44 22" aria-hidden="true">
              <circle className="nx-dc nx-dc--paused" cx="11" cy="11" r="5" />
            </svg>
            <span>Paused or canceled, as reported.</span>
          </li>
          {stats.tested > 0 && (
            <li>
              <svg width="44" height="22" viewBox="0 0 44 22" aria-hidden="true">
                <circle className="nx-dc nx-dc--over" cx="11" cy="11" r="5" />
                <circle className="nx-dc nx-dc--cascades" cx="30" cy="11" r="5" />
              </svg>
              <span>Amber: a line over its limit. Red: a blackout on the synthetic model.</span>
            </li>
          )}
        </ul>
        <p className="muted nx-small">Click a dot for its sources and test. Click anywhere else to open that state&apos;s grid with a campus there.</p>
      </section>
    </div>
  )
}
