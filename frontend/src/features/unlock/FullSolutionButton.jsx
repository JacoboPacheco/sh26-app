// The way in to the incident solution stage (IncidentSolution.jsx), for Watch it fail's results column ("Fix it in
// Strengthen": right after a drop that overloads, before any cascade, and again once the cascade has played; Fix it
// itself moved there, user Sat 22:30) and the presentation's solutions beat ("The full solution"). One click: the stage
// reads the briefing report for this case, fetched then if no one fetched it yet. `body` is the briefing's case
// (features/briefing/stage.js bodyFor); without it, the map's own (the cascade on screen, else the case). `onOpen` runs
// first (the deck closes itself). Hidden on the national map and for a plant outage (the briefing body can't carry them).
import { useOverload } from '../../store'
import { bodyFor } from '../briefing/stage'
import { plantsOut } from '../fix/flipCase'
import { openIncidentSolution } from './incident'
import './incident.css'

export default function FullSolutionButton({ body: bodyIn = null, onOpen, className = '', label = 'The full solution', hint = 'Every verified fix for this incident, in depth, on Strengthen' }) {
  const O = useOverload()
  const body = bodyIn || (O.cascade ? bodyFor(O.cascade, O.caseBody, O.cascadeBody) : O.caseBody)
  if (!body || (body.region || O.region) === 'US' || (!bodyIn && plantsOut(O.cascade))) return null
  const open = () => {
    onOpen?.()
    openIncidentSolution({ region: O.region, ...body }, O)
  }
  return (
    <button type="button" className={`to-solution${className ? ` ${className}` : ''}`} onClick={open}>
      <span className="to-solution__a">
        {label} <span aria-hidden="true">→</span>
      </span>
      {hint && <span className="to-solution__q">{hint}</span>}
    </button>
  )
}
