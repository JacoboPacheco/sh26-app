// The Strengthen page's ranked upgrade table: the engine's plan, cheapest per site first, one row per package (one
// upgrade, or the few one blocked site needs together). The rows the budget buys are bright, the ones built so far
// carry a green number, the row the build-up is on is highlighted, and past the budget the plan runs on dimmed
// under a rule. A second view lists Gemini's bundles; only the ones the engine re-ran and confirmed are there.
// Clicking a row selects it: the map flies to it and the card opens.
import { useEffect, useRef, useState } from 'react'
import { fmt } from '../../geo'
import AiBadge from '../ai/AiBadge'
import HowAiIsUsed from '../ai/HowAiIsUsed'
import { money, moneyRange } from '../cost/money'
import { leadOf, shortMoney, whereOf } from './budget'
import { compact } from './unlockStore'

const kindOf = (p) => `${fmt(p.kv)} kV ${p.kind}`
const plain = (p) => p.short || p.label?.replace(/^the /, '') || `#${p.branch_id}`

export default function UpgradeTable({ r, shown, target, playing, budget, selected, bundle, onPickStep, onPickBundle, startView = 'plan' }) {
  const ai = r.ai || {}
  const bundles = ai.bundles || []
  const [view, setView] = useState(startView)
  const scroller = useRef(null)
  const total = r.steps.at(-1)?.cum_cost.high || 0
  const atBudget = target ? r.steps[target - 1].cum_cost.high : 0
  const selStep = selected?.type === 'step' ? selected.id : null

  // keep the row the build-up is on (or the one picked) in view, scrolling only the table
  const focusRow = playing ? shown : selStep
  useEffect(() => {
    const box = scroller.current
    const row = focusRow && box?.querySelector(`[data-step="${focusRow}"]`)
    if (!box || !row || box.scrollHeight <= box.clientHeight + 2) return
    const top = row.offsetTop - box.querySelector('thead').offsetHeight
    if (top < box.scrollTop || row.offsetTop + row.offsetHeight > box.scrollTop + box.clientHeight) {
      box.scrollTo({ top: Math.max(0, top - 8), behavior: playing ? 'auto' : 'smooth' })
    }
  }, [focusRow, playing, view])
  // the build-up finished: back to the top of the ranking (the biggest wins first)
  const wasPlaying = useRef(false)
  useEffect(() => {
    if (wasPlaying.current && !playing && shown >= target) scroller.current?.scrollTo({ top: 0, behavior: 'smooth' })
    wasPlaying.current = playing
  }, [playing, shown, target])

  const aiLine = {
    used: `Gemini proposed ${fmt(ai.asked)} other ${ai.asked === 1 ? 'bundle' : 'bundles'} from the weak points; the engine re-ran each and kept ${fmt(bundles.length)}.`,
    none_verified: `Gemini proposed ${fmt(ai.asked)} ${ai.asked === 1 ? 'bundle' : 'bundles'}; the engine re-ran each and none let another site connect, so none is listed.`,
    not_configured: "Gemini isn't set up on this server: the engine's plan stands alone.",
    offline: "Gemini was unavailable for this study (busy, over its quota or too slow): the engine's plan stands alone.",
    error: "Gemini's step failed: the engine's plan stands alone.",
    skipped: 'There was nothing for Gemini to improve on.',
  }[ai.status]
  const aiOk = ai.status === 'used' || ai.status === 'none_verified'

  return (
    <section className="st-table" aria-labelledby="st-table-h">
      <div className="st-table__head">
        <h2 className="st-h" id="st-table-h">
          {view === 'plan' ? 'The upgrades, cheapest per site first' : "Gemini's bundles, re-run by the engine"}
        </h2>
        <div className="st-seg" role="tablist" aria-label="Which upgrades">
          <button
            type="button"
            role="tab"
            aria-selected={view === 'plan'}
            className={view === 'plan' ? 'is-on' : ''}
            onClick={() => {
              setView('plan')
              if (bundle != null) onPickBundle(bundle) // back to the plan on the map too
            }}
          >
            Engine plan <span className="st-seg__n">{fmt(r.steps.length)}</span>
          </button>
          <button type="button" role="tab" aria-selected={view === 'ai'} className={view === 'ai' ? 'is-on' : ''} onClick={() => setView('ai')}>
            Gemini <span className="st-seg__n">{fmt(bundles.length)}</span>
          </button>
        </div>
      </div>
      <div className="st-ailine">
        {aiOk ? (
          <AiBadge by="gemini" verified={bundles.length > 0} />
        ) : (
          <AiBadge by="fallback" why={ai.status === 'not_configured' ? 'Gemini not set up' : 'Gemini unavailable'} compact />
        )}
        <span>{aiLine}</span>
        <HowAiIsUsed surface="unlock" label="How" className="st-how" />
      </div>

      <div className="st-table__scroll" ref={scroller}>
        {view === 'plan' ? (
          <table className="st-t">
            <thead>
              <tr>
                <th scope="col" className="st-t__n">
                  #
                </th>
                <th scope="col">Upgrade · where</th>
                <th scope="col" className="st-t__num" title="High end of each estimate; hover a cost for its range">
                  Cost
                </th>
                <th scope="col" className="st-t__num" title="More sites that can host the campus once this package is built (each site tested alone)">
                  Unlocks
                </th>
                <th
                  scope="col"
                  className="st-t__num"
                  title="Line overloads across the tested sites (a line over its rating with the campus at one site), before and after this package"
                >
                  Strain removed
                </th>
                <th scope="col" className="st-t__num" title="The biggest blackout (people hit, estimate) that a campus at an unlocked site would have set off">
                  Blackout prevented
                </th>
                <th scope="col">By</th>
              </tr>
            </thead>
            <tbody>
              {r.steps.map((st) => {
                const lead = leadOf(st.projects)
                const built = st.n <= shown
                const cls = ['st-row', built && 'st-row--built', st.n > target && 'st-row--over', playing && st.n === shown && 'st-row--now', selStep === st.n && 'st-row--sel']
                  .filter(Boolean)
                  .join(' ')
                const towns = st.newly.map((s) => s.area)
                const drop = (st.overloads_before ?? 0) - st.overloads_left
                return [
                  <tr key={st.n} data-step={st.n} className={cls} aria-selected={selStep === st.n} onClick={() => onPickStep(st)}>
                    <td className="st-t__n">{st.n}</td>
                    <th scope="row" className="st-t__what">
                      <button type="button" className="st-t__btn" onClick={(e) => (e.stopPropagation(), onPickStep(st))}>
                        {plain(lead)}
                        {st.projects.length > 1 && <span className="st-t__more"> +{st.projects.length - 1} more</span>}
                      </button>
                      <span className="st-t__sub">
                        {whereOf(st.projects)} · {kindOf(lead)}
                        {lead.weak_point ? <span className="st-t__wp"> · weak point {lead.weak_point}</span> : null}
                      </span>
                    </th>
                    <td className="st-t__num" title={`${moneyRange(st.cost.low, st.cost.high)} (low to high end)`}>
                      {shortMoney(st.cost.high)}
                      <span className="st-t__sub st-t__opt">{shortMoney(st.cum_cost.high)} in all</span>
                    </td>
                    <td className="st-t__num">
                      +{fmt(st.newly_count)} {st.newly_count === 1 ? 'site' : 'sites'}
                      <span className="st-t__sub st-t__opt" title={towns.join(', ')}>
                        {towns.slice(0, 2).join(', ')}
                        {towns.length > 2 ? ` +${towns.length - 2}` : ''}
                      </span>
                    </td>
                    <td
                      className="st-t__num"
                      title={`Line overloads across the ${fmt(r.sites_total)} tested sites: ${fmt(st.overloads_before)} before, ${fmt(st.overloads_left)} after`}
                    >
                      {drop > 0 ? `−${fmt(drop)}` : '0'}
                      <span className="st-t__sub">{fmt(st.overloads_left)} left</span>
                    </td>
                    <td className="st-t__num">
                      {st.blackout_prevented_max ? compact(st.blackout_prevented_max) : '—'}
                      {st.blackout_prevented_max > 0 && <span className="st-t__sub">people</span>}
                    </td>
                    <td>
                      <AiBadge
                        by="engine"
                        title={
                          st.verified === st.newly_count
                            ? 'Found by the engine; every site it unlocks was re-run through the full cascade'
                            : `Found by the engine; ${st.verified} of ${st.newly_count} sites re-run so far`
                        }
                      />
                    </td>
                  </tr>,
                  st.n === target && target < r.steps.length ? (
                    <tr key={`b${st.n}`} className="st-row--rule" aria-hidden="true">
                      <td colSpan={7}>
                        <span>
                          Budget {shortMoney(budget)} ends here · the rest of the plan: {fmt(r.steps.length - target)} more packages, {money(total - atBudget)} more
                        </span>
                      </td>
                    </tr>
                  ) : null,
                ]
              })}
            </tbody>
          </table>
        ) : bundles.length ? (
          <table className="st-t">
            <thead>
              <tr>
                <th scope="col" className="st-t__n">
                  #
                </th>
                <th scope="col">Bundle · Gemini&apos;s reason</th>
                <th scope="col" className="st-t__num">
                  Cost
                </th>
                <th scope="col" className="st-t__num">
                  Unlocks
                </th>
                <th scope="col" className="st-t__num" title="The engine's own plan with the same money (high end)">
                  Engine, same money
                </th>
                <th scope="col" className="st-t__num">
                  Blackout prevented
                </th>
                <th scope="col">By</th>
              </tr>
            </thead>
            <tbody>
              {bundles.map((b, i) => (
                <tr key={b.name + i} className={`st-row st-row--built${bundle === i ? ' st-row--sel' : ''}`} aria-selected={bundle === i} onClick={() => onPickBundle(i)}>
                  <td className="st-t__n">G{i + 1}</td>
                  <th scope="row" className="st-t__what">
                    <button type="button" className="st-t__btn" onClick={(e) => (e.stopPropagation(), onPickBundle(i))}>
                      {b.name}
                    </button>
                    <span className="st-t__sub">
                      {fmt(b.projects.length)} upgrades{b.why ? ` · ${b.why}` : ''}
                    </span>
                  </th>
                  <td className="st-t__num" title={`${moneyRange(b.cost.low, b.cost.high)} (low to high end)`}>
                    {shortMoney(b.cost.high)}
                  </td>
                  <td className="st-t__num">
                    +{fmt(b.more_sites)} {b.more_sites === 1 ? 'site' : 'sites'}
                    <span className="st-t__sub">{b.verified === b.more_sites ? 'all re-run' : `${fmt(b.verified)} re-run`}</span>
                  </td>
                  <td className="st-t__num">
                    +{fmt(b.engine_same_cost?.more_sites ?? 0)}
                    <span className="st-t__sub">{b.beats_engine ? 'Gemini ahead' : 'plan ahead'}</span>
                  </td>
                  <td className="st-t__num">{b.blackout_prevented_max ? compact(b.blackout_prevented_max) : '—'}</td>
                  <td>
                    <AiBadge by="gemini" verified className="aib--wrap" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="st-empty">{aiLine || 'No Gemini bundle for this study.'}</p>
        )}
      </div>
    </section>
  )
}
