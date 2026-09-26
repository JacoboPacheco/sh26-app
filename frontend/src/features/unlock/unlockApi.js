import { api } from '../../api'

// Strengthen the grid (backend/unlock.py): a background study per (region, size, load level).
// start → {id, status, cached}; job → {status, progress {phase, done, total, message}, partial {sites, points} | result | error}
// peek → {state: done | queued | running | none, id, estimate_s, sites, warm}: what can be shown without starting a study
export const SIZES = [500, 1000, 2000, 5000]
export const DEFAULT_SIZE = 1000

export const startUnlock = ({ region, mw, loadFactor = 1 }) =>
  api('/api/unlock/start', {
    method: 'POST',
    body: { region, mw, load_factor: loadFactor },
  })

export const getUnlockJob = (id) => api(`/api/unlock/jobs/${encodeURIComponent(id)}`)

// How sure is the number (capacity.sensitivity): the always-on search again under other assumptions, for a finished
// study. → {status: pending | done, sensitivity}; the cases land in the study's result (the page polls its job).
export const startSensitivity = ({ region, mw, loadFactor = 1 }) =>
  api('/api/unlock/sensitivity', {
    method: 'POST',
    body: { region, mw, load_factor: loadFactor },
  })

export const peekUnlock = ({ region, mw, loadFactor = 1 }) =>
  api(`/api/unlock/peek?region=${encodeURIComponent(region)}&mw=${encodeURIComponent(mw)}&load_factor=${encodeURIComponent(loadFactor)}`)
