import { api } from '../../api'

// What a case costs (backend/costs.py). `body` is the store's caseBody (backend/grid.py → CaseIn);
// `hoursOut` is how long the lost load stays dark (0.5–72 hours).
//   getCost   → the formula estimate: {lines: [{key, label, per, low, high, formula, assumption, sources, …}],
//               total_one_time, insights, notes, lost_mw, people, region_name, …}
//   getCostAi → the same plus ai: {blackout|upgrades|power_bill|who_pays: {low, high, reasoning, fallback}}
//               and fallback: true when Gemini wasn't used (the AI column then repeats the formula)
export const getCost = (body, hoursOut) => api('/api/cost', { method: 'POST', body: { ...body, hours_out: hoursOut } })
export const getCostAi = (body, hoursOut) => api('/api/cost/ai', { method: 'POST', body: { ...body, hours_out: hoursOut } })
