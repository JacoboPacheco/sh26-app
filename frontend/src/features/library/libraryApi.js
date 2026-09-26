import { api } from '../../api'

// The Library's endpoints (backend/scenarios.py). A scenario is {id, name, note, region, case, result,
// parent_id, version, example, shared, share_slug, updated_at, lat, lon, mw, summary}; `case` is what
// the grid endpoints solve (grid.CaseIn) and `result` what it does, computed by the backend.
export const getScenario = (id) => api(`/api/scenarios/${encodeURIComponent(id)}`)
export const createScenario = (body) => api('/api/scenarios', { method: 'POST', body }) // {name, note?, case}
export const updateScenario = (id, body) => api(`/api/scenarios/${id}`, { method: 'PUT', body }) // {name?, note?, case?}
export const deleteScenario = (id) => api(`/api/scenarios/${id}`, { method: 'DELETE' })
export const makeVersion = (id, body = {}) => api(`/api/scenarios/${id}/version`, { method: 'POST', body }) // {name?, note?, case?}
export const shareScenario = (id) => api(`/api/scenarios/${id}/share`, { method: 'POST' }) // -> {slug, path}
export const unshareScenario = (id) => api(`/api/scenarios/${id}/share`, { method: 'DELETE' })
export const getShared = (slug) => api(`/api/share/${encodeURIComponent(slug)}`) // public, read-only
export const syncExamples = () => api('/api/scenarios/examples', { method: 'POST' }) // the built-in examples, idempotent
