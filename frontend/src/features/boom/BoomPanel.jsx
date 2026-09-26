// FEATURE: AI-boom mode — several campuses at once (owned by the boom track).
// Contract: default export BoomPanel() — the left panel while mode === 'boom'. Uses
// useOverload().extraSites / setExtraSites([{id, lat, lon, mw}]). The store re-solves the whole
// case on every change; the cascade runs from the timeline at the bottom.
import { useEffect, useRef, useState } from 'react'
import { fmt } from '../../geo'
import { overLimitText } from '../../shell/CampusPanel'
import { townOf, useOverload } from '../../store'
import { Button, EmptyState, ErrorBanner, Field } from '../../ui'
import { usePlanner } from '../planner/plannerStore'
import BoomAgent from './BoomAgent'
import { CAMPUS_MW, MAX_EXTRA, PRESET, SIZE_MAX, SIZE_MIN, frame, homesLabel, newId, niceName, snapToSub } from './boomData'
import './boom.css'

const DRAG_DEG = 0.05 // a press that moves farther than this before release is a drag, not a click

export default function BoomPanel() {
  const o = useOverload()
  const { grid, extraSites, setExtraSites, setMapTool, mode, focus, mapRef, cascade, site, branchById, subPos, region } = o
  const agent = usePlanner()
  const [armed, setArmed] = useState(false)
  const [notice, setNotice] = useState(null)
  const live = armed && mode === 'boom' && !!grid
  const total = extraSites.reduce((sum, c) => sum + c.mw, 0)
  const full = extraSites.length >= MAX_EXTRA
  const stateName = grid?.meta?.region_name || 'this state'
  const agentBusy = agent.origin === 'boom' && agent.status === 'running'

  // When the panel opens with campuses already set, bring them into view.
  const opening = useRef(extraSites)
  useEffect(() => {
    frame(
      opening.current.map((c) => [c.lon, c.lat]),
      focus,
      mapRef,
    )
  }, [focus, mapRef])

  // The store centers the cascade camera on the first data center in the case, and never zooms
  // out past all of Florida, so with campuses from Jacksonville to Miami most of the blackout ended
  // up off screen. With no main data center, frame everything the cascade touches instead.
  const seenCascade = useRef(cascade)
  useEffect(() => {
    if (!cascade || cascade === seenCascade.current) return
    seenCascade.current = cascade
    if (site) return
    const pts = cascade.sites.map((s) => [s.sub_lon, s.sub_lat])
    cascade.steps.forEach((st) => {
      st.tripped.forEach((bid) => {
        const b = branchById.get(bid)
        if (b) pts.push(subPos(b.from_sub), subPos(b.to_sub))
      })
      st.dark_subs.forEach((sid) => pts.push(subPos(sid)))
      st.newly_affected.forEach(([sid]) => pts.push(subPos(sid)))
    })
    frame(pts.filter(Boolean), focus, mapRef)
  }, [cascade, site, branchById, subPos, focus, mapRef])

  // Add a 1 GW campus where the map was clicked, snapped to the substation it would connect to.
  function addAt({ lat, lon }) {
    if (extraSites.length >= MAX_EXTRA) {
      setNotice(`The model runs up to ${MAX_EXTRA} campuses at once. Remove one to add another.`)
      setArmed(false)
      return
    }
    const sub = snapToSub(grid.subs, lat, lon, grid.meta?.bbox)
    if (!sub) {
      setNotice(`No grid there: click inside ${stateName}.`)
      return
    }
    if (extraSites.some((c) => c.sub === sub.id)) {
      setNotice(`${niceName(sub.name)} already has a campus. Change its size in the list instead.`)
      return
    }
    setNotice(null)
    setExtraSites((list) => [...list, { id: newId(), metro: niceName(sub.name), sub: sub.id, lat: sub.lat, lon: sub.lon, mw: CAMPUS_MW }])
    if (extraSites.length + 1 >= MAX_EXTRA) setArmed(false)
  }
  const addRef = useRef(addAt)
  useEffect(() => {
    addRef.current = addAt
  })

  // While armed, a click on the map adds a campus (the tool replaces placing the main data center).
  useEffect(() => {
    if (!live) return undefined
    let downAt = null
    const tool = {
      cursor: 'copy',
      down: (p) => (downAt = p),
      up: (p) => {
        const d = downAt
        downAt = null
        if (d && Math.hypot(d.lat - p.lat, d.lon - p.lon) > DRAG_DEG) return
        addRef.current(p)
      },
    }
    setMapTool(tool)
    const onKey = (e) => e.key === 'Escape' && setArmed(false)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      setMapTool((t) => (t === tool ? null : t)) // only take back our own tool
    }
  }, [live, setMapTool])

  function loadPreset() {
    setNotice(null)
    setExtraSites(PRESET.map((p) => ({ ...p })))
    frame(
      PRESET.map((p) => [p.lon, p.lat]),
      focus,
      mapRef,
    )
  }
  function remove(id) {
    setNotice(null)
    setExtraSites((list) => list.filter((c) => c.id !== id))
  }
  function clearAll() {
    setNotice(null)
    setArmed(false)
    setExtraSites([]) // with nothing else in the case, the store drops the old answer: the map goes calm
  }
  const setSize = (id, mw) => setExtraSites((list) => list.map((c) => (c.id === id ? { ...c, mw } : c)))

  return (
    <div className="stack panel-body boom">
      <p className="boom__lede">An AI buildout: several gigawatt campuses on one grid.</p>
      <BoomAgent />
      {region !== 'US' && (
        <div className="boom__actions">
          <h3 className="panel-h">Or place them yourself</h3>
          {region === 'FL' && (
            <Button variant="secondary" onClick={loadPreset} disabled={!grid || agentBusy}>
              Five 1 GW campuses near the biggest metros
            </Button>
          )}
          <Button variant="secondary" aria-pressed={armed} onClick={() => setArmed((a) => !a)} disabled={!grid || agentBusy || (full && !armed)}>
            {armed ? 'Stop adding campuses' : 'Add campuses by clicking the map'}
          </Button>
          {armed && (
            <p className="boom__armed" role="status">
              Click anywhere on {stateName} to add a {fmt(CAMPUS_MW)} MW campus. Press Escape to stop.
            </p>
          )}
          {notice && (
            <p className="boom__notice" role="status">
              {notice}
            </p>
          )}
        </div>
      )}

      {extraSites.length === 0 ? (
        !agentBusy &&
        region !== 'US' && (
          <EmptyState title="No campuses yet">
            {region === 'FL' ? 'Let the AI place them, load the five-metro buildout, or add campuses one click at a time.' : 'Let the AI place them, or add campuses one click at a time.'}
          </EmptyState>
        )
      ) : (
        <>
          <div className="boom__total">
            <span className="boom__total-n">{fmt(total)} MW</span>
            <span className="boom__total-label">
              {extraSites.length} {extraSites.length === 1 ? 'campus' : 'campuses'} · about as much as {homesLabel(total)} homes use, estimate
            </span>
          </div>
          <Together />
          <h3 className="panel-h">Campuses</h3>
          <CampusList campuses={extraSites} onSize={setSize} onRemove={remove} />
          <div className="row">
            <Button variant="secondary" onClick={clearAll}>
              Clear all
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

// The backend reports 1,000,000 MW for a spot where no size overloads a line, and 0 where a line
// nearby is already over its limit (a hot hour or a hurricane's damage).
function aloneHint(mw) {
  if (mw >= 100000) return 'Alone, this spot takes any size without overloading a line.'
  if (mw < 1) return 'Alone, this spot is already at a line’s limit.'
  return `Alone, this spot takes ${fmt(mw)} MW before a line overloads.`
}

function CampusList({ campuses, onSize, onRemove }) {
  const { result } = useOverload()
  // what each site could take alone (from the last solve), by substation
  const alone = new Map((result?.sites || []).map((s) => [s.sub, s]))
  return (
    <ul className="boom__list">
      {campuses.map((c) => {
        const info = alone.get(c.sub)
        return (
          <li key={c.id} className="boom__item">
            <Field
              label={`${c.metro} · ${fmt(c.mw)} MW`}
              type="range"
              min={SIZE_MIN}
              max={Math.max(SIZE_MAX, Math.ceil(c.mw / 50) * 50)}
              step={c.mw % 50 ? 10 : 50}
              value={c.mw}
              aria-valuetext={`${fmt(c.mw)} MW`}
              onChange={(e) => onSize(c.id, Number(e.target.value))}
              hint={info ? aloneHint(info.headroom_mw) : undefined}
            />
            <span className="boom__remove">
              <Button variant="danger" onClick={() => onRemove(c.id)} aria-label={`Remove the ${c.metro} campus`}>
                ×
              </Button>
            </span>
          </li>
        )
      })}
    </ul>
  )
}

// The combined answer: every campus (and the main data center, if one is placed) solved together.
function Together() {
  const { result, solving, whatifError, subName, site, mw, clearSite, cascade, extraSites, grid } = useOverload()
  const campusSubs = new Set(extraSites.map((c) => c.sub))
  const current = result && (result.sites || []).some((s) => campusSubs.has(s.sub))
  const over = current ? result.overloaded : []
  const first = over[0]
  const lineName = (b) => (b.from === b.to ? `the ${niceName(subName(b.from))} transformer` : `${niceName(subName(b.from))} to ${niceName(subName(b.to))}`)
  return (
    <div className="stack boom__result" aria-live="polite">
      <h3 className="panel-h">
        All together{solving && <span className="muted"> · updating…</span>}
      </h3>
      <ErrorBanner error={whatifError} />
      {!current ? (
        !whatifError && <p className="muted">Solving the grid…</p>
      ) : over.length === 0 ? (
        <p className="verdict verdict--ok">No line over limit. The grid holds this buildout.</p>
      ) : (
        <>
          <p className="verdict verdict--bad">{overLimitText(over)}</p>
          <p>
            {grid?.meta?.region_name || 'The grid'} breaks first near <strong>{townOf(subName(first.from))}</strong>: {lineName(first)} runs at{' '}
            <strong className="boom__pct">{fmt(first.pct)} %</strong> of its limit.
          </p>
        </>
      )}
      {site && (
        <div className="boom__main">
          <p className="muted">Includes the {fmt(mw)} MW data center from the Data center tab.</p>
          <Button variant="secondary" onClick={clearSite}>
            Leave it out
          </Button>
        </div>
      )}
      {current && over.length > 0 && (
        <p className="boom__next">
          {cascade ? (
            'Scrub the timeline at the bottom to step through what failed.'
          ) : (
            <>
              Press <strong>Run the cascade</strong> at the bottom to trip that line and follow the failures.
            </>
          )}
        </p>
      )}
    </div>
  )
}
