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

// `propose: false` (a prefetch nobody may present): the server does not start its AI proposer for the case yet; the
// stage's own requests (Gemini's deck, the poll) start it. Not part of the cache key: the answer is the same.
const noPropose = (propose) => (propose === false ? { propose: false } : {})
export const getReport = (body, { propose } = {}) =>
  cached(keyOf(body, 'report'), () => api('/api/briefing', { method: 'POST', body: { ...cleanBody(body), ...noPropose(propose) } }))
export const getDeck = (body, { ai = false, length = 'full', propose } = {}) =>
  cached(keyOf(body, `deck:${ai}:${length}`), () =>
    api('/api/briefing/deck', { method: 'POST', body: { ...cleanBody(body), ai, length, ...noPropose(propose) } }),
  )
// Fetch again, past the cache, and keep the answer for next time: the AI proposer adds its verified plans to a
// report in the background (deck.agentic.status 'running' → 'done'), so the stage asks again while it runs.
function fresh(key, fn) {
  const p = fn()
  p.then(
    () => {
      cache.delete(key)
      cache.set(key, p)
    },
    () => {},
  )
  return p
}
export const refetchReport = (body) => fresh(keyOf(body, 'report'), () => api('/api/briefing', { method: 'POST', body: cleanBody(body) }))
export const refetchDeck = (body, { ai = false, length = 'full' } = {}) =>
  fresh(keyOf(body, `deck:${ai}:${length}`), () => api('/api/briefing/deck', { method: 'POST', body: { ...cleanBody(body), ai, length } }))
export const getPresets = (region = 'FL') => cached(`presets|${region}`, () => api(`/api/briefing/presets?region=${encodeURIComponent(region)}`))

// Start fetching a briefing (the review card and "Present the damage" call this as soon as a cascade lands): the
// template deck, the report and Gemini's deck at the same moment (BRIEFING SPEED: the server builds each case once, so
// the three share one build). `ai`: false leaves Gemini for the click: outside Florida a cascade nobody presents spends
// no Gemini call on its deck, and the AI proposer waits for the click too.
export function prefetchBriefing(body, { ai = true } = {}) {
  getDeck(body, { ai: false, propose: ai }).catch(() => {})
  getReport(body, { propose: ai }).catch(() => {})
  if (ai) getDeck(body, { ai: true }).catch(() => {})
}

// A catastrophe's cascade, loaded into the map from its report, remembers the case it came from (the
// preset), so the review card under it asks for that briefing (cached) instead of re-sending the
// storm's thousand downed lines, which the case API refuses.
const replayBodies = new WeakMap()
export const rememberReplay = (cascade, body) => cascade && typeof cascade === 'object' && replayBodies.set(cascade, cleanBody(body))
export const replayBodyOf = (cascade) => (cascade && typeof cascade === 'object' ? replayBodies.get(cascade) || null : null)

// The region a catastrophe preset belongs to: its id starts with the state code ('tx-gulf-landfall').
export const presetRegion = (id) => (/^[a-z]{2}-/.test(String(id)) ? String(id).slice(0, 2).toUpperCase() : 'FL')

// The route isn't there yet (another track is still building it): the preview falls back to its fixture.
export const notLive = (err) => /^(Not Found|Method Not Allowed|Request failed \((404|405)\))$/.test(err?.message || '')
