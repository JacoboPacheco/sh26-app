// The Strengthen page's plan, campus by campus (the capacity view's right column): today's campuses in one row,
// then one row per campus that needs upgrades (where it goes, what it takes in plain words, what it adds to the
// bill), the budget's line between what it buys and what it doesn't, and why the search ends. The number badge is
// the same campus as the meter's cell and the map's marker. A row click flies the map there and opens its card.
import { useEffect, useRef } from 'react'
import { fmt } from '../../geo'
import { moneyRange } from '../cost/money'
import { shortMoney } from './budget'
import { aOrdinal, blockOf, ordinal, raisedAgain, shortName, stopText, upgradeWords } from './capacity'

const TOWNS = 4

export default function CapacityPlan({ m, target, shown, playing, budget, selectedN, onPick, plantsN }) {
  const list = useRef(null)
  const steps = m.steps
  const today = m.today
  const free = steps.slice(0, today)
  const rest = steps.slice(today)

  // keep the campus being built (or the one picked) in view, scrolling only the list
  const focusN = playing ? shown : selectedN
  useEffect(() => {
    const box = list.current
    const row = focusN && box?.querySelector(`[data-n="${focusN > today ? focusN : 'today'}"]`)
    if (!box || !row || box.scrollHeight <= box.clientHeight + 2) return
    const top = row.offsetTop - box.offsetTop
    if (top < box.scrollTop || top + row.offsetHeight > box.scrollTop + box.clientHeight) {
      box.scrollTo({ top: Math.max(0, top - 40), behavior: playing ? 'auto' : 'smooth' })
    }
  }, [focusN, playing, today])

  const badge = (n) => {
    const s = n <= today ? 'today' : n <= target ? (n <= shown ? 'bought' : 'unlit') : 'more'
    return (
      <span key={n} className={`cp-n cp-n--${s}${plantsN != null && n > plantsN ? ' cp-n--gen' : ''}`} aria-hidden="true">
        {n}
      </span>
    )
  }

  return (
    <ol className="cp-list" ref={list} aria-label="Campuses in the order they connect">
      {today > 0 && (
        <li data-n="today">
          <button type="button" className={`cp-row cp-row--today${selectedN && selectedN <= today ? ' is-sel' : ''}`} onClick={() => onPick(today)}>
            <span className="cp-badges">{free.map((st) => badge(st.n))}</span>
            <span className="cp-main">
              <span className="cp-town">
                {today === 1 ? '1 fits today' : `1–${today} fit today`}
                <span className="cp-town__sub">
                  {': '}
                  {free
                    .slice(0, TOWNS)
                    .map((st) => st.site.area)
                    .join(', ')}
                  {free.length > TOWNS ? ` and ${fmt(free.length - TOWNS)} more` : ''}
                </span>
              </span>
              <span className="cp-what">No upgrade needed: no line or transformer goes over its rating with all of them connected.</span>
            </span>
            <span className="cp-cost cp-cost--none">$0</span>
          </button>
        </li>
      )}
      {target <= today && today < steps.length && (
        <li className="cp-budget">
          <span>Your budget: {budget > 0 ? shortMoney(budget) : 'none'}</span>
          <span className="cp-budget__rest">raise it to add the {ordinal(today + 1)}</span>
        </li>
      )}
      {rest.map((st) => {
        const n = st.n
        const lead = st.projects[0]
        // what stopped it, when one limit did at most sites and it isn't the upgrade the row already names
        const b = blockOf(st)
        const most = b && b.branch_id !== lead?.branch_id
        const cls = ['cp-row', n > target && 'is-later', playing && n === shown && 'is-now', selectedN === n && 'is-sel'].filter(Boolean).join(' ')
        return [
          <li key={n} data-n={n}>
            <button type="button" className={cls} onClick={() => onPick(n)} aria-current={selectedN === n ? 'true' : undefined}>
              <span className="cp-badges">{badge(n)}</span>
              <span className="cp-main">
                <span className="cp-town">{st.site.area}</span>
                {st.free ? (
                  <span className="cp-what">Fits with the upgrades before it.</span>
                ) : (
                  <span className="cp-what">
                    {upgradeWords(lead, raisedAgain(m, lead, n))}
                    {st.projects.length > 1 && (
                      <span className="cp-more">
                        {' '}
                        and {fmt(st.projects.length - 1)} more {st.projects.length === 2 ? 'upgrade' : 'upgrades'}
                      </span>
                    )}
                  </span>
                )}
                {most && (
                  <span className="cp-block">Stopped first by the {shortName(b)}</span>
                )}
              </span>
              <span className={`cp-cost${st.free ? ' cp-cost--none' : ''}`} title={st.free ? 'No added cost' : `${moneyRange(st.cost.low, st.cost.high)} (low to high end)`}>
                {st.free ? '$0' : `+${shortMoney(st.cost.high)}`}
                {!st.free && <span className="cp-cum">{shortMoney(st.cum_cost.high)} in all</span>}
              </span>
            </button>
          </li>,
          n === target && target < steps.length ? (
            <li key="budget" className="cp-budget">
              <span>Your budget: {shortMoney(budget)}</span>
              <span className="cp-budget__rest">
                {fmt(steps.length - target)} more for another {shortMoney(steps.at(-1).cum_cost.high - st.cum_cost.high)}
              </span>
            </li>
          ) : null,
        ]
      })}
      <li className="cp-end">
        Then {stopText(m.stop)}
        {m.stop === 'plants' ? `: the model’s power plants can’t supply ${aOrdinal(steps.length + 1)} campus at this load level.` : '.'}
      </li>
    </ol>
  )
}
