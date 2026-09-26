import { api } from '../../api'

// The incident briefing's backend calls (backend/briefing.py, backend/bulletin.py). A `body` is a case
// (backend/grid.py → CaseIn) plus an optional `preset` (a catastrophes.json id).
//   POST /api/briefing       → the report: timeline, root cause, areas, verified fixes, no-fix proof, recovery, facts, replay
//   POST /api/briefing/deck  → the slides with EN/ES narration (templates; ai=true adds Gemini's presenter prose)
//   GET  /api/briefing/presets?region=FL → hypothetical catastrophes
//
// Answers are cached here by body, so the review card can prefetch while the cascade plays and the
// stage opens on data that is already in. Failed requests are dropped from the cache (a retry refetches).

const CACHE_MAX = 16
const cache = new Map()

function cached(key, fn) {
  if (cache.has(key)) {
    const p = cache.get(key)
    cache.delete(key) // most recently used last
    cache.set(key, p)
    return p
  }
  const p = fn().catch((err) => {
    if (cache.get(key) === p) cache.delete(key)
    throw err
  })
  cache.set(key, p)
  while (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value)
  return p
}

// only what the backend reads, in a stable order (the cache key)
export function cleanBody(body) {
  const out = {}
  for (const k of ['region', 'preset', 'lat', 'lon', 'mw', 'sites', 'load_factor', 'trip', 'upgrades', 'firm']) {
    if (body?.[k] !== undefined && body[k] !== null) out[k] = body[k]
  }
  return out
}
const keyOf = (body, extra = '') => `${extra}|${JSON.stringify(cleanBody(body))}`

export const getReport = (body) => cached(keyOf(body, 'report'), () => api('/api/briefing', { method: 'POST', body: cleanBody(body) }))
export const getDeck = (body, { ai = false, length = 'full' } = {}) =>
  cached(keyOf(body, `deck:${ai}:${length}`), () => api('/api/briefing/deck', { method: 'POST', body: { ...cleanBody(body), ai, length } }))
export const getPresets = (region = 'FL') => cached(`presets|${region}`, () => api(`/api/briefing/presets?region=${encodeURIComponent(region)}`))

// Start fetching a briefing (the review card calls this as soon as a cascade lands).
export function prefetchBriefing(body) {
  getReport(body).catch(() => {})
  getDeck(body, { ai: false })
    .then(() => getDeck(body, { ai: true }))
    .catch(() => {})
}

// The route isn't there yet (another track is still building it): the preview falls back to its fixture.
export const notLive = (err) => /^(Not Found|Method Not Allowed|Request failed \((404|405)\))$/.test(err?.message || '')
