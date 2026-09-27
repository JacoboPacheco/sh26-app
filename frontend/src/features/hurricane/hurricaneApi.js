import { api } from '../../api'

// Hurricane mode's backend calls (backend/hurricane.py).
// trackHits -> {trip: [branch ids, first reached first], count, along_km: [...], total_km, radius_km,
//               capped, category, vmax_mph}
export const getPresets = () => api('/api/hurricane/presets')
export const trackHits = (points, radiusKm, category) =>
  api('/api/hurricane/track', { method: 'POST', body: { points, radius_km: radiusKm, category } })
