import { api as rawApi, assetUrl } from '../../api'

// A dropped connection reads "Failed to fetch" in the browser: say it in a plain sentence instead.
const api = async (...args) => {
  try {
    return await rawApi(...args)
  } catch (e) {
    if (e instanceof TypeError || /failed to fetch|networkerror|load failed/i.test(e?.message || '')) {
      throw new Error("Can't reach the server right now. Check the connection and try again.")
    }
    throw e
  }
}

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
  // one pair taken apart (backend/gridlock.py trace): the score's terms, the distance, each endpoint's match, provenance
  trace: (id, params) => api(`/api/gridlock/trace/${encodeURIComponent(id)}?${q(params)}`),
  // Build agreement (backend/agreement.py): a draft coordination proposal for one overlap. ai=false is the
  // plain template (instant); ai=true asks Gemini to word it and checks every number (a few seconds).
  // negotiated: 'en' | 'es' | 'plain' uses that negotiation's agreed, verified terms in the draft
  // plan: the id of a collaboration plan the viewer chose (POST /api/gridlock/plans): the draft takes that plan's terms
  agreement: (id, { window_months, lang = 'en', ai = true, negotiated, plan } = {}) =>
    api(`/api/agreement/${encodeURIComponent(id)}?${q({ window_months, lang, ai: ai ? 'true' : 'false', negotiated, plan })}`),
  // Collaboration plans (backend): one Gemini agent per company, each given a goal from its own filing, plus a neutral
  // coordinator, propose 2-3 distinct ways to build together; the pipeline checks every figure (a labeled template
  // without the key). {pair, companies, plans, recommended, trace, fallback, model, cached}
  plans: (pair, { lang = 'en', window_months } = {}) => api('/api/gridlock/plans', { method: 'POST', body: { pair, lang, window_months } }),
  // Negotiate (backend/negotiate.py): two Gemini agents, each reading one utility's filing, trade proposals and the
  // pipeline verifies every turn (up to 8 calls, ~20-40 s live; instant when cached). ai=false: the plain version.
  negotiate: (id, { window_months, lang = 'en', ai = true } = {}) =>
    api(`/api/negotiate/${encodeURIComponent(id)}?${q({ window_months, lang, ai: ai ? 'true' : 'false' })}`, { method: 'POST' }),
  // the turns of a negotiation still running, polled while its POST waits ({running: false} when none is)
  negotiateLive: (id, { window_months, lang = 'en' } = {}) => api(`/api/negotiate/${encodeURIComponent(id)}/live?${q({ window_months, lang })}`),
  // the coordination calendar: every compared project's build window + each flagged pair's shared window
  calendar: (params) => api(`/api/gridlock/calendar?${q(params)}`),
  // what changed since DESC's last filing: its 2026-2030 list against the 2024-2028 one, row by row with both pages
  changes: () => api('/api/gridlock/changes'),
}

// A download the browser saves itself (a plain link): /api/gridlock/<path> at the settings on screen, e.g.
// downloadUrl('calendar.ics', params, { pair: id }) or downloadUrl('export.xlsx', params).
export function downloadUrl(path, params, extra) {
  const s = q({ ...(params || {}), ...(extra || {}) })
  return assetUrl(`/api/gridlock/${path}${s ? `?${s}` : ''}`)
}

// Resolves to {client, summary} once the engine answers /summary; rejects with the engine's error.
export async function connect() {
  return { client: live, summary: await live.summary() }
}
