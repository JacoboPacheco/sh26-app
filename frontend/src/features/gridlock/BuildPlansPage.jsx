import { useEffect } from 'react'
import AgreementDoc from './AgreementDoc'
import BuildPlansPanel from './BuildPlansPanel'
import DetailCard from './DetailCard'
import { GridlockProvider } from './GridlockProvider'
import { useGridlock } from './context'
import PlansMap from './PlansMap'
import './gridlock.css'
import './agreement.css'

// The Build agreement page: the sidebar on the left (~380 px), the map filling the rest, the detail card
// over the map's right side; a drafted agreement opens as a document column beside the map (it replaces
// the opportunity card; a project's "Where this came from" card still opens over the map). Stacked on a
// phone. It fills its parent (give the parent a height); `extra` goes in the sidebar's top row.
export default function BuildPlansPage({ extra }) {
  return (
    <GridlockProvider>
      <PageBody extra={extra} />
    </GridlockProvider>
  )
}

function PageBody({ extra }) {
  const g = useGridlock()
  const { draft, flyToOverlap } = g
  // once the map has made room for the document, fly to the pair (and, on a phone, bring the document up)
  useEffect(() => {
    if (!draft) return
    const t = setTimeout(() => {
      flyToOverlap(draft.overlap)
      if (window.matchMedia?.('(max-width: 760px)').matches) document.querySelector('.gl-doc')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 260)
    return () => clearTimeout(t)
  }, [draft, flyToOverlap])
  return (
    <div className={`gl gl-page${draft ? ' gl-page--doc' : ''}`}>
      <BuildPlansPanel extra={extra} />
      <div className="gl-canvas">
        <PlansMap />
        {(!draft || g.sel?.kind === 'project') && <DetailCard />}
      </div>
      {draft && <AgreementDoc />}
    </div>
  )
}
