import { useEffect, useRef } from 'react'
import BuildPlansPanel from './BuildPlansPanel'
import DetailCard from './DetailCard'
import { GridlockProvider } from './GridlockProvider'
import { useGridlock } from './context'
import PairSheet from './PairSheet'
import PlansMap from './PlansMap'
import ProofStrip from './ProofStrip'
import usePageTitle from '../../usePageTitle'
import './gridlock.css'
import './agreement.css'
import './plans.css'

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
  usePageTitle('Build together | Overload')
  // once the sheet has measured how much of the map it covers, frame the pair in the rest; when it closes, bring
  // the pair back to the middle of the whole map
  const last = useRef(null)
  const calendarOn = g.calendarOn
  useEffect(() => {
    if (!draft) return
    last.current = draft.overlap
    const t = setTimeout(() => flyToOverlap(draft.overlap), 40)
    return () => clearTimeout(t)
  }, [draft, covered, flyToOverlap])
  // In Calendar view the rail eases back to its wide timeline when a sheet closes (calendar.css), so the map is
  // still resizing when the camera first moves: frame the pair again once the rail has settled.
  const pageRef = useRef(null)
  const settle = useRef(null) // {o, until}
  useEffect(() => {
    // closed, and the sheet no longer covers the map
    if (draft || covered || !last.current) return
    const o = last.current
    last.current = null
    settle.current = calendarOn ? { o, until: performance.now() + 1000 } : null
    const t = setTimeout(() => flyToOverlap(o), 40)
    return () => clearTimeout(t)
  }, [draft, covered, flyToOverlap, calendarOn])
  useEffect(() => {
    const el = pageRef.current
    if (!el) return undefined
    const onEnd = (e) => {
      const s = settle.current
      if (e.target !== el || e.propertyName !== 'grid-template-columns' || !s) return
      settle.current = null
      if (performance.now() <= s.until) flyToOverlap(s.o)
    }
    el.addEventListener('transitionend', onEnd)
    return () => el.removeEventListener('transitionend', onEnd)
  }, [flyToOverlap])
  return (
    <div ref={pageRef} className={`gl gl-page${draft ? ' gl-page--sheet' : ''}${draft && g.pairStep === 'plans' ? ' gl-page--wide' : ''}${g.calendarOn ? ' gl-page--cal' : ''}`}>
      <BuildPlansPanel extra={extra} />
      <div className="gl-canvas">
        <PlansMap />
        {!['pipeline', 'sperry'].includes(g.tab) && <ProofStrip />}
        {!draft && <DetailCard />}
        {draft && <PairSheet />}
      </div>
    </div>
  )
}
