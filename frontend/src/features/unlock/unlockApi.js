import { api } from '../../api'

// Strengthen the grid (backend/unlock.py): a background study per (region, size, load level).
// start → {id, status, cached}; job → {status, progress {phase, done, total, message}, partial {sites, points} | result | error}
export const SIZES = [500, 1000, 2000, 5000]
export const DEFAULT_SIZE = 1000

export const startUnlock = ({ region, mw, loadFactor = 1 }) =>
  api('/api/unlock/start', {
    method: 'POST',
    body: { region, mw, load_factor: loadFactor },
  })

export const getUnlockJob = (id) => api(`/api/unlock/jobs/${encodeURIComponent(id)}`)
