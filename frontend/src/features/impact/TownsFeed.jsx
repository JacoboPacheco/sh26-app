import { useId, useMemo, useRef } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import './impact.css'
import { homesText, nameList, roundHomes, useFlip, useFreshStep, useReducedMotion, useRestored, useTowns } from './towns'

const MAX_ROWS = 8
const SETTLE_MS = 1200 // how long a step's new towns stay pinned at the top before taking their place

// Who loses power, by name: under the giant counter, the towns going dark as the cascade plays,
// biggest first. A step's new towns slide in at the top with a short red flash, then settle into
// their place by homes. Click a town to fly the map there. Renders nothing when no one is dark.
export default function TownsFeed() {
  const { cascade, step, focus, subPos } = useOverload()
  const towns = useTowns()
  const reduced = useReducedMotion()
  const fresh = useFreshStep(cascade, step, SETTLE_MS) && !reduced && step > 0
  const listRef = useRef(null)
  const headId = useId()

  const ordered = useMemo(() => {
    if (!fresh) return towns
    const isNew = (t) => t.arrival === step
    return [...towns.filter(isNew), ...towns.filter((t) => !isNew(t))]
  }, [towns, fresh, step])

  const restored = useRestored(towns)
  useFlip(listRef, reduced)

  if (!towns.length && !restored.length) return null
  const shown = ordered.slice(0, MAX_ROWS)
  const more = towns.length - shown.length
  const top = towns[0]?.homes || 1

  return (
    <section className="towns" aria-labelledby={headId}>
      <div className="towns__head">
        <h2 id={headId} className="panel-h">
          {/* the counter shows the worst moment; once some towns got power back, say this list is the end state */}
          {restored.length ? 'Towns dark at the end (estimates)' : 'Towns losing power (estimates)'}
        </h2>
        <span className="towns__count">{towns.length ? `${fmt(towns.length)} ${towns.length === 1 ? 'town' : 'towns'}` : 'None now'}</span>
      </div>
      {towns.length > 0 && (
        <ol className="towns__list" ref={listRef}>
          {shown.map((t, i) => {
            const isNew = fresh && t.arrival === step
            const grew = fresh && !isNew && t.latest === step
            const cls = `towns__item${isNew ? ' towns__item--new' : ''}${grew ? ' towns__item--grew' : ''}`
            return (
              <li key={t.name} data-key={t.name} className={cls} style={isNew ? { '--i': i } : undefined}>
                <button
                  type="button"
                  className="towns__row"
                  onClick={() => focus(t.subs.map(subPos))}
                  aria-label={`${t.name}: about ${homesText(t.homes)} without power (estimate). Show it on the map.`}
                >
                  <span className="towns__name">{t.name}</span>
                  <span className="towns__homes">
                    {fmt(roundHomes(t.homes))} <span className="towns__unit">homes</span>
                  </span>
                  <span className="towns__bar" style={{ transform: `scaleX(${Math.max(t.homes / top, 0.02)})` }} aria-hidden="true" />
                </button>
              </li>
            )
          })}
        </ol>
      )}
      {more > 0 && (
        <p className="towns__more">
          and {fmt(more)} more {more === 1 ? 'town' : 'towns'}
        </p>
      )}
      {restored.length > 0 && <p className="towns__back">Power came back to {nameList(restored)} as the grid split.</p>}
    </section>
  )
}
