import BuildPlansPanel from './BuildPlansPanel'
import DetailCard from './DetailCard'
import { GridlockProvider } from './GridlockProvider'
import PlansMap from './PlansMap'
import './gridlock.css'

// The whole Build plans module: the sidebar on the left (~380 px), the map filling the rest, the
// detail card over the map's right side; stacked on a phone. It fills its parent (give the parent
// a height); `extra` goes in the sidebar's top row (e.g. a close link).
export default function BuildPlansPage({ extra }) {
  return (
    <GridlockProvider>
      <div className="gl gl-page">
        <BuildPlansPanel extra={extra} />
        <div className="gl-canvas">
          <PlansMap />
          <DetailCard />
        </div>
      </div>
    </GridlockProvider>
  )
}
