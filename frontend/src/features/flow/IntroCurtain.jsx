import { useIntroPhase, useIntroSoft } from './introPhase'
import './flow.css'

// The dark sheet over the map that rises south to north while the grid powers on (see Intro.jsx).
// Rendered by FlowCanvas, so it sits inside the map: the header's question and the panels stay lit.
// While the start gate waits ('gate') the sheet holds still; after it the sweep starts from the same dim veil.
export default function IntroCurtain() {
  const phase = useIntroPhase()
  const soft = useIntroSoft()
  if (phase === 'off') return null
  return (
    <div className={`intro-curtain${soft ? ' intro-curtain--soft' : ''}${phase === 'gate' ? ' intro-curtain--held' : ''}${phase === 'out' ? ' intro--out' : ''}`} aria-hidden="true">
      <div className="intro-curtain__sheet" />
    </div>
  )
}
