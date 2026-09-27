// The way in to the incident solution stage (IncidentSolution.jsx), for Watch it fail's results column and the
// presentation's solutions beat: "The full solution" for the incident on the map, one click, no waiting (the stage
// reads the briefing report already fetched for this case). `body` is the briefing's case (features/briefing/stage.js
// bodyFor); without it, the map's own (the cascade on screen, else the case). `onOpen` runs first (the deck closes
// itself). Hidden on the national map and for a plant outage (the briefing body can't carry the plants).
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
