// Strengthen the grid: "Gemini tries to beat the plan" (backend/capacity_ai.py). A compact block under the plan's
// title: what Gemini proposed against the engine's plan at the default budget and what the engine found, in one
// sentence written by the backend from the engine's verdicts (a win is only ever claimed when the engine re-solved,
// priced and cascaded Gemini's plan). "Show it" puts Gemini's verified plan on the meter and the map (its new sites
// outlined in the Gemini blue); "Watch the AI work" plays the rounds (proposal -> engine verdict -> findings ->
// revision). Honest when Gemini loses or isn't available: the engine's plan stands. The study never waits for the
// challenge: it arrives "pending" (a working line) and the verdict replaces it when the engine has judged it.
import { useState } from 'react'
import { fmt } from '../../geo'
import AgentTrace from '../ai/AgentTrace'
import AiBadge from '../ai/AiBadge'
import { shortMoney } from './budget'
import { ordinal, shortName } from './capacity'
import { setFlex } from './unlockStore'

const FELL = { not_configured: 'Gemini not set up', offline: 'Gemini unavailable', error: 'Gemini unavailable' }
const OUTCOME = new Set(['beat', 'matched', 'lost']) // the challenge really ran and the engine judged it
const RAISES = 6

export default function GeminiChallenge({ r, flex, showing, onShow }) {
  const ai = r?.capacity?.ai
  const [watch, setWatch] = useState(false)
  if (!ai || ai.status === 'skipped' || !ai.status) return null
  const win = ai.verified && (ai.status === 'beat' || ai.status === 'matched')
  const fell = FELL[ai.status]
  const pending = ai.status === 'pending'
  if (flex) {
    // the challenge covers the always-on plan: point there only when it ran (or is running), never for a fallback
    if (!pending && !OUTCOME.has(ai.status)) return null
    return (
      <p className="gc gc--quiet">
        <AiBadge by="gemini" compact />
        <span>
          {pending ? 'Gemini is challenging the always-on plan.' : 'Gemini’s challenge ran on the always-on plan.'}{' '}
          <button type="button" className="st-link" onClick={() => setFlex(false)}>
            See it
          </button>
        </span>
      </p>
    )
  }
  if (pending) {
    return (
      <div className="gc gc--pending" role="status">
        <p className="gc__line">
          <AiBadge by="gemini" />
          <span className="gc__text">{ai.sentence || 'Gemini is trying to beat this plan; the engine will check whatever it proposes.'}</span>
        </p>
        <span className="gc__working" aria-hidden="true" />
      </div>
    )
  }
  const badge = fell ? <AiBadge by="fallback" why={fell} compact /> : <AiBadge by="gemini" verified={win} />
  const trace = ai.trace || []
  return (
    <div className={`gc gc--${ai.status}${showing ? ' is-showing' : ''}`}>
      <p className="gc__line">
        {badge}
        <span className="gc__text">{ai.sentence || 'Gemini’s challenge didn’t run for this study; the engine’s plan stands.'}</span>
      </p>
      {(win || trace.length > 0) && (
        <div className="gc__actions">
          {win && (
            <button type="button" className={`gc__btn${showing ? ' is-on' : ''}`} aria-pressed={showing} onClick={() => onShow(!showing)}>
              {showing ? 'Back to the engine’s plan' : 'Show it'}
            </button>
          )}
          {trace.length > 0 && (
            <button type="button" className="gc__btn gc__btn--quiet" aria-expanded={watch} onClick={() => setWatch((w) => !w)}>
              {watch ? 'Hide the AI’s work' : 'Watch the AI work'}
            </button>
          )}
        </div>
      )}
      {showing && win && <Version ai={ai} />}
      {watch && (
        <AgentTrace
          className="gc__trace"
          trace={trace}
          heading="Gemini against the engine’s plan"
          badge={badge}
          stepMs={700}
          totals={`${ai.rules}${ai.model ? ` Model: ${ai.model}.` : ''}`}
        />
      )}
    </div>
  )
}

// Gemini's plan in words, while the map and the meter show it
function Version({ ai }) {
  const fresh = ai.placements.filter((p) => p.new)
  const ups = ai.projects || []
  return (
    <div className="gc__plan">
      <p className="gc__plan-h">
        On the map: Gemini’s {fmt(ai.campuses)} campuses at once and {fmt(ups.length)} {ups.length === 1 ? 'upgrade' : 'upgrades'}, {shortMoney(ai.cost.high)} at the high
        end, priced by the engine.
      </p>
      {(fresh.length > 0 || ai.dropped?.length > 0) && (
        <p className="gc__moves">
          {fresh.length > 0 && (
            <span>
              <span className="gc__new" aria-hidden="true" />
              New: {fresh.map((p) => p.area).join(', ')}
            </span>
          )}
          {ai.dropped?.length > 0 && <span>In place of: {ai.dropped.map((d) => `${d.area} (the engine’s ${ordinal(d.engine_n)})`).join(', ')}</span>}
        </p>
      )}
      {ups.length > 0 && (
        <ul className="gc__ups" aria-label="Gemini’s upgrades">
          {ups.slice(0, RAISES).map((p) => (
            <li key={p.branch_id}>
              <span className="gc__up-name">{shortName(p)}</span>
              <span className="gc__up-mva">
                {fmt(p.rating_original_mva)} → {fmt(p.rating_after_mva)} MVA
                {p.engine_mva != null && Math.abs(p.engine_mva - p.rating_after_mva) > 0.5 ? ` (engine: ${fmt(p.engine_mva)})` : ''}
                {p.engine_mva == null ? ' (new)' : ''}
              </span>
              <span className="gc__up-cost">{shortMoney(p.cost.high)}</span>
            </li>
          ))}
          {ups.length > RAISES && <li className="gc__more">and {fmt(ups.length - RAISES)} more</li>}
        </ul>
      )}
    </div>
  )
}
