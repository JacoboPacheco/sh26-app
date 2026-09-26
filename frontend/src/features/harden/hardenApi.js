import { api } from '../../api'

// "Harden before the storm" (backend/harden.py).
// startHarden(body) → {job, status: 'running'|'done', progress: {phase, done, total}, trace, result?}
//   body: {region, preset?, points, radius_km, budget_usd, lat?, lon?, mw?, sites, load_factor, upgrades, firm}
// pollHarden(job) → the same view, until status is 'done' (result) or 'error' (error)
// getHardenInfo() → {configured (Gemini plans on this server), budgets, cost_method, sources, note}
export const startHarden = (body) => api('/api/harden/run', { method: 'POST', body })
export const pollHarden = (job) => api(`/api/harden/jobs/${encodeURIComponent(job)}`)
export const getHardenInfo = () => api('/api/harden/info')
