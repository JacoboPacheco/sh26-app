import { useIntroPhase } from './introPhase'
import './flow.css'

// The dark sheet over the map that rises south to north while the grid powers on (see Intro.jsx).
// Rendered by FlowCanvas, so it sits inside the map: the header's question and the panels stay lit.
export default function IntroCurtain() {
  const phase = useIntroPhase()
  if (phase === 'off') return null
  return (
    <div className={`intro-curtain${phase === 'out' ? ' intro--out' : ''}`} aria-hidden="true">
      <div className="intro-curtain__sheet" />
    </div>
  )
}
