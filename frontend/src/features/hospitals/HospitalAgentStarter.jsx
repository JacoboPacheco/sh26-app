// Renders nothing: starts the hospital beds agent the moment a cascade lands (mounted once, inside the
// OverloadProvider), so its answer is ready by the time the presentation reaches the hospitals beat.
import { useHospitalAgentStarter } from './hospitalAgentApi'

export default function HospitalAgentStarter() {
  useHospitalAgentStarter()
  return null
}
