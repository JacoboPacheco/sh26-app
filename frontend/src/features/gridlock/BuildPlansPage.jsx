import { useEffect, useRef } from 'react'
import BuildPlansPanel from './BuildPlansPanel'
import DetailCard from './DetailCard'
import { GridlockProvider } from './GridlockProvider'
import { useGridlock } from './context'
import PairSheet from './PairSheet'
import PlansMap from './PlansMap'
import './gridlock.css'
import './agreement.css'

// The Build together page: the rail on the left (the ranked pairs first), the map filling the rest. Picking a
// pair opens its sheet over the right ~60 % of the map (full screen on a phone) and the map frames the pair in
// the part left visible; a project's "Where this came from" floats over the map when no pair is open. It fills
// its parent (give the parent a height); `extra` goes beside the rail's title.
export default function BuildPlansPage({ extra }) {
  return (
    <GridlockProvider>
      <PageBody extra={extra} />
    </GridlockProvider>
  )
}

function PageBody({ extra }) {
  const g = useGridlock()
  const { draft, flyToOverlap, cover } = g
  const covered = cover > 0
  // once the sheet has measured how much of the map it covers, frame the pair in the rest; when it closes, bring
  // the pair back to the middle of the whole map
  const last = useRef(null)
  useEffect(() => {
    if (!draft) return
    last.current = draft.overlap
    const t = setTimeout(() => flyToOverlap(draft.overlap), 40)
    return () => clearTimeout(t)
  }, [draft, covered, flyToOverlap])
  useEffect(() => {
    // closed, and the sheet no longer covers the map
    if (draft || covered || !last.current) return
    const o = last.current
    last.current = null
    const t = setTimeout(() => flyToOverlap(o), 40)
    return () => clearTimeout(t)
  }, [draft, covered, flyToOverlap])
  return (
    <div className={`gl gl-page${draft ? ' gl-page--sheet' : ''}`}>
      <BuildPlansPanel extra={extra} />
      <div className="gl-canvas">
        <PlansMap />
        {!draft && <DetailCard />}
        {draft && <PairSheet />}
      </div>
    </div>
  )
}
