import { api } from '../../api'

// Every Build plans call goes to the engine (/api/gridlock/*, backend/gridlock.py). When the engine is
// down the module shows an error with Retry; it never substitutes sample data. (When the pipeline hasn't
// run, the ENGINE serves Sperry's worked example itself and says so: summary.fallback.)

const q = (params) =>
  new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString()

const live = {
  summary: () => api('/api/gridlock/summary'),
  projects: () => api('/api/gridlock/projects'),
  basemap: () => api('/api/gridlock/basemap'),
  overlaps: (params) => api(`/api/gridlock/overlaps?${q(params)}`),
  opportunities: (limit = 10) => api(`/api/gridlock/opportunities?limit=${limit}`),
  estimate: (id, window_months) =>
    api(`/api/gridlock/estimate/${encodeURIComponent(id)}${window_months != null ? `?window_months=${window_months}` : ''}`),
  sperryCheck: () => api('/api/gridlock/sperry-check'),
  // Build agreement (backend/agreement.py): a draft coordination proposal for one overlap. ai=false is the
  // plain template (instant); ai=true asks Gemini to word it and checks every number (a few seconds).
  agreement: (id, { window_months, lang = 'en', ai = true } = {}) =>
    api(`/api/agreement/${encodeURIComponent(id)}?${q({ window_months, lang, ai: ai ? 'true' : 'false' })}`),
}

// Resolves to {client, summary} once the engine answers /summary; rejects with the engine's error.
export async function connect() {
  return { client: live, summary: await live.summary() }
}
