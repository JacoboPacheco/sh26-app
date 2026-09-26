// FEATURE: AI-boom mode — several campuses at once (owned by the boom track).
// Contract: default export BoomPanel() — the left panel while mode === 'boom'. Uses
// useOverload().extraSites / setExtraSites([{id, lat, lon, mw}]).
import { EmptyState } from '../../ui'

export default function BoomPanel() {
  return <EmptyState title="AI boom">Several gigawatt campuses at once — coming online.</EmptyState>
}
