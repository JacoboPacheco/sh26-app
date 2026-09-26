import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import './impact.css'
import { groupTowns, hitTowns, peopleText, roundPeople, useFlip, useHitEvents, useReducedMotion } from './towns'

const MAX_ROWS = 8

// Who is hit, by name: under the counter, the towns in the order the blast reaches them — the
// latest at the top. While the replay plays, a town appears the moment the blast front lands on it
// (the same schedule as the counter's leap), with a red flash; a town hit again climbs back to the
// top and its number grows. Paused or scrubbed: the towns hit up to that step. Before the cascade
// (a storm that already cut lines): the towns without power. Click a town to fly the map there.
export default function TownsFeed() {
  const { cascade, step, fx, playing, focus, subPos, subById, view, peoplePerMw } = useOverload()
  const events = useHitEvents()
  const reduced = useReducedMotion()
  const live = !!(fx && playing)
  const listRef = useRef(null)
  const headId = useId()

  // while playing: how many of the schedule's town leaps have landed (a render per landing, not per frame)
  const [landed, setLanded] = useState({ fx: null, n: 0 })
  useEffect(() => {
    if (!live) return undefined
    const leaps = fx.schedule.leaps.filter((l) => l.town)
    const now = performance.now() - fx.startedAt
    const timers = leaps.map((l, i) => setTimeout(() => setLanded({ fx, n: i + 1 }), Math.max(0, l.t - now)))
    return () => timers.forEach(clearTimeout)
  }, [live, fx])

  const { towns, hit } = useMemo(() => {
    if (cascade && (live || step > 0)) {
      let list
      if (live) {
        const n = landed.fx === fx ? landed.n : 0
        const now = fx.schedule.leaps.filter((l) => l.town).slice(0, n).map((l) => l.town)
        list = [...events.slice(0, fx.from).flat(), ...now]
      } else list = events.slice(0, step).flat()
      // the latest hit first
      return { hit: true, towns: hitTowns(list, subById).sort((a, b) => b.last - a.last) }
    }
    return { hit: false, towns: groupTowns(view?.affected, subById, peoplePerMw) }
  }, [cascade, live, step, landed, fx, events, subById, view, peoplePerMw])

  useFlip(listRef, reduced)

  if (!towns.length) return null
  const shown = towns.slice(0, MAX_ROWS)
  const more = towns.length - shown.length
  const top = Math.max(...towns.map((t) => t.people), 1)
  const latest = live ? Math.max(...towns.map((t) => t.last)) : -1

  return (
    <section className="towns" aria-labelledby={headId}>
      <div className="towns__head">
        <h2 id={headId} className="panel-h">
          {hit ? 'Towns hit, latest first (estimates)' : 'Towns losing power (estimates)'}
        </h2>
        <span className="towns__count">{`${fmt(towns.length)} ${towns.length === 1 ? 'town' : 'towns'}`}</span>
      </div>
      <ol className="towns__list" ref={listRef}>
        {shown.map((t) => {
          const now = !reduced && t.last === latest
          const cls = `towns__item${now && t.first === t.last ? ' towns__item--new' : ''}${now && t.first !== t.last ? ' towns__item--grew' : ''}`
          return (
            // keyed by name and hit: a town hit again re-mounts its row, so its flash plays again
            <li key={now ? `${t.name}-${t.last}` : t.name} data-key={t.name} className={cls}>
              <button
                type="button"
                className="towns__row"
                onClick={() => focus(t.subs.map(subPos))}
                aria-label={`${t.name}: about ${peopleText(t.people)} ${hit ? 'hit' : 'without power'} (estimate). Show it on the map.`}
              >
                <span className="towns__name">{t.name}</span>
                <span className="towns__homes">
                  {fmt(roundPeople(t.people))} <span className="towns__unit">people</span>
                </span>
                <span className="towns__bar" style={{ transform: `scaleX(${Math.max(t.people / top, 0.02)})` }} aria-hidden="true" />
              </button>
            </li>
          )
        })}
      </ol>
      {more > 0 && (
        <p className="towns__more">
          and {fmt(more)} more {more === 1 ? 'town' : 'towns'}
        </p>
      )}
    </section>
  )
}
