import { useEffect, useId, useMemo, useState } from 'react'
import { fmt } from '../../geo'
import { townOf, useOverload } from '../../store'
import { Button, ErrorBanner, Loading } from '../../ui'

// "Is my area at risk?" for the state on screen, from what the app already knows: every light is
// named by the town its substation serves; an area's people are its load's share of the state's
// residents (estimate); its room is the most a data center could add at its best substation before
// the first line overloads; and the current scenario tells whether it stays lit.
// (The areas track's deeper profile replaces this when it lands.)
export default function AreaFinder({ code }) {
  const o = useOverload()
  const { grid, peoplePerMw, getHeadroom, loadFactor, view, cascade, step, focus, subPos, mw, setMw, place, setMode, result } = o
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState(null)
  const [room, setRoom] = useState(null)
  const [roomError, setRoomError] = useState(null)
  const listId = useId()

  const areas = useMemo(() => {
    if (!grid?.subs?.length || grid.meta?.region !== code) return []
    const by = new Map()
    grid.subs.forEach((s) => {
      const name = s.area || townOf(s.name)
      const a = by.get(name) || { name, subs: [], load: 0 }
      a.subs.push(s)
      a.load += Math.max(0, s.load_mw || 0)
      by.set(name, a)
    })
    return [...by.values()].sort((a, b) => b.load - a.load)
  }, [grid, code])

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = q ? areas.filter((a) => a.name.toLowerCase().includes(q)) : areas
    return list.slice(0, 8)
  }, [areas, query])

  const area = picked ? areas.find((a) => a.name === picked) : null

  // the room at the area's best substation, at this time of day
  useEffect(() => {
    if (!area) return undefined
    let live = true
    setRoom(null)
    setRoomError(null)
    getHeadroom(loadFactor, code)
      .then((by) => {
        if (!live) return
        let best = null
        area.subs.forEach((s) => {
          const v = by[s.id]
          if (v !== undefined && (!best || v > best.mw)) best = { mw: v, sub: s }
        })
        setRoom(best)
      })
      .catch((err) => live && setRoomError(err))
    return () => {
      live = false
    }
  }, [area, loadFactor, code, getHeadroom])

  function pick(a) {
    setPicked(a.name)
    setQuery(a.name)
    focus(a.subs.map((s) => subPos(s.id)))
  }

  // the scenario on screen: how much of this area's load is lost at the step shown
  let status = null
  if (area && (result || cascade)) {
    let lost = 0
    area.subs.forEach((s) => (lost += view?.affected?.get(s.id) || 0))
    const share = area.load ? lost / (area.load * loadFactor) : 0
    const when = cascade ? (step >= cascade.steps.length ? 'at the end of the cascade' : `at step ${step}`) : 'before the cascade'
    status =
      share >= 0.6
        ? { tone: 'bad', text: `Dark ${when}: about ${fmt(lost * peoplePerMw)} people without power (estimate).` }
        : lost > 0.5
          ? { tone: 'warn', text: `Partly dark ${when}: about ${fmt(lost * peoplePerMw)} people without power (estimate).` }
          : { tone: 'ok', text: `Keeps its power ${when}${cascade ? '' : '. Run the cascade to test it'}.` }
  }

  if (!areas.length) return <Loading label="Loading the areas…" />

  return (
    <div className="stack nx-pad nx-area">
      <p className="muted nx-small">Every light on the map is a substation, named after the town it serves. Find yours.</p>
      <div className="field">
        <label htmlFor={`${listId}-q`}>Your town or area</label>
        <input
          id={`${listId}-q`}
          type="search"
          value={query}
          placeholder={areas[0] ? `e.g. ${areas[0].name}` : ''}
          onChange={(e) => {
            setQuery(e.target.value)
            setPicked(null)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && matches[0]) pick(matches[0])
          }}
          aria-controls={`${listId}-list`}
          autoComplete="off"
        />
      </div>
      {!area && (
        <ul className="nx-area__list" id={`${listId}-list`} aria-label="Matching areas">
          {matches.map((a) => (
            <li key={a.name}>
              <button type="button" onClick={() => pick(a)}>
                <span>{a.name}</span>
                <span className="muted">{fmt(a.load * peoplePerMw)} people</span>
              </button>
            </li>
          ))}
          {!matches.length && <li className="muted nx-small">No area by that name in this state&apos;s model.</li>}
        </ul>
      )}
      {area && (
        <section className="nx-area__card stack" aria-live="polite">
          <h3 className="nx-h3">{area.name}</h3>
          <dl className="nx-dl">
            <div>
              <dt>People served</dt>
              <dd>{fmt(area.load * peoplePerMw)} (estimate)</dd>
            </div>
            <div>
              <dt>Substations</dt>
              <dd>{fmt(area.subs.length)}</dd>
            </div>
            <div>
              <dt>Room for a data center</dt>
              <dd>{room ? `${fmt(room.mw)} MW` : roomError ? 'unavailable' : '…'}</dd>
            </div>
          </dl>
          <ErrorBanner error={roomError} />
          {room && <p className="muted nx-small">The most a data center could add at {room.sub.name} before the first line overloads, on the synthetic model.</p>}
          {status && <p className={`nx-area__status nx-area__status--${status.tone}`}>{status.text}</p>}
          <div className="row">
            {room && (
              <Button
                variant="secondary"
                onClick={() => {
                  setMode('campus')
                  if (mw < 100) setMw(500)
                  place(room.sub.lat, room.sub.lon)
                }}
              >
                Plug {fmt(mw)} MW in here
              </Button>
            )}
            <Button
              variant="secondary"
              onClick={() => {
                setPicked(null)
                setQuery('')
              }}
            >
              Another area
            </Button>
          </div>
        </section>
      )}
    </div>
  )
}
