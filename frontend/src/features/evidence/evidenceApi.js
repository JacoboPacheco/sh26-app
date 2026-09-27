// FEATURE: "How we know" (backend/evidence.py). Three engine checks behind the headline numbers, asked for only when
// the fold is opened and kept per case for the session (the server caches them too), so opening it twice asks once.
//   getValidation(region)  GET  /api/evidence/validation  the engine against the dataset's own solved flows
//   getN1(body, applied)   POST /api/evidence/n1          an N-1 screen of the case's fix (the case body: grid.CaseIn)
//   getHours(body, applied) POST /api/evidence/hours      how often the case would overload (a coarse step curve)
// `applied`: the case on screen already has its fix in it (the results panel's flip), so the backend screens it as it
// is (auto_fix=false) instead of judging Fix it's re-rating.
import { api } from '../../api'

const memo = new Map()

// one request per key; a failed one is forgotten so Retry asks again
function once(key, ask) {
  let p = memo.get(key)
  if (!p) {
    p = ask().catch((e) => {
      memo.delete(key)
      throw e
    })
    memo.set(key, p)
    if (memo.size > 40) memo.delete(memo.keys().next().value)
  }
  return p
}

// only what the engine reads: the store's case body may carry UI-only fields
export function caseOf(body) {
  if (!body) return null
  const out = {
    region: body.region || 'FL',
    load_factor: body.load_factor ?? 1,
    trip: body.trip || [],
    upgrades: body.upgrades || {},
    firm: !!body.firm,
    sites: (body.sites || []).map(({ lat, lon, mw }) => ({ lat, lon, mw })),
  }
  if (body.lat != null && body.lon != null && body.mw != null) Object.assign(out, { lat: body.lat, lon: body.lon, mw: body.mw })
  // a catastrophe preset (the incident solution stage's body): the backend expands it as the briefing does
  if (body.preset) out.preset = String(body.preset)
  return out
}

// something to check: a campus, a storm (drawn or a preset) or another hour (the backend refuses an empty case)
export const checkable = (c) => !!c && (c.lat != null || c.sites.length > 0 || c.trip.length > 0 || !!c.preset || Math.abs((c.load_factor ?? 1) - 1) > 1e-9)

const q = (applied) => (applied ? '?auto_fix=false' : '')
export const getValidation = (region = 'FL') => once(`v|${region}`, () => api(`/api/evidence/validation?region=${encodeURIComponent(region)}`))
export const getN1 = (c, applied = false) => once(`n1|${!!applied}|${JSON.stringify(c)}`, () => api(`/api/evidence/n1${q(applied)}`, { method: 'POST', body: c }))
export const getHours = (c, applied = false) => once(`h|${!!applied}|${JSON.stringify(c)}`, () => api(`/api/evidence/hours${q(applied)}`, { method: 'POST', body: c }))
