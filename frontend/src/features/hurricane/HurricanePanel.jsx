// FEATURE: hurricane mode (owned by the hurricane track).
// Contract: default export HurricanePanel() — the left panel while mode === 'hurricane'.
import { EmptyState } from '../../ui'

export default function HurricanePanel() {
  return <EmptyState title="Hurricane mode">Draw a storm track across Florida — coming online.</EmptyState>
}
