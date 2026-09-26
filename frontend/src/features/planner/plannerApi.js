// The siting planner's calls (backend/planner.py) and the small helpers the panel needs.
import { api } from '../../api'

// {region, goal, total_mw, max_sites, load_factor, firm} → {id, status}
export const startPlan = (body) => api('/api/planner/start', { method: 'POST', body })
// → {id, status: 'running' | 'done' | 'error', steps: [...], result?, error?}
export const getPlanJob = (id) => api(`/api/planner/jobs/${encodeURIComponent(id)}`)

export const TOTAL_MIN = 10
export const TOTAL_MAX = 30000
export const SITES_MAX = 6
export const GOAL_MAX = 300

// One click fills the whole form. The Florida one needs an upgrade (a transformer every site
// loads), so it shows the planner's second half; Atlanta shows a goal that names a town.
export const EXAMPLES = [
  {
    label: '2,000 MW in Texas',
    goal: 'Place 2,000 MW of AI campuses in Texas without blacking anyone out',
    region: 'TX',
    total: 2000,
    sites: 3,
  },
  {
    label: '3,000 MW in Florida',
    goal: "Place 3,000 MW of AI campuses in Florida and keep everyone's lights on",
    region: 'FL',
    total: 3000,
    sites: 3,
  },
  {
    label: '1,500 MW near Atlanta',
    goal: 'Place 1,500 MW of AI campuses near Atlanta without blacking anyone out',
    region: 'GA',
    total: 1500,
    sites: 3,
  },
]

const WORDS = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6 }

// What a typed goal says about the form's fields: {mw, sites, region} (each only when found).
// "2 GW in Texas across three campuses" → {mw: 2000, sites: 3, region: 'TX'}. State names only
// (two-letter codes collide with words like "in" and "or").
export function readGoal(text, regions) {
  const out = {}
  const s = String(text || '')
  const m = s.match(/(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(gw|gigawatts?|mw|megawatts?)\b/i)
  if (m) {
    const n = Number(m[1].replace(/,/g, ''))
    const mw = /^g/i.test(m[2]) ? n * 1000 : n
    if (Number.isFinite(mw) && mw >= TOTAL_MIN && mw <= TOTAL_MAX) out.mw = Math.round(mw)
  }
  const k = s.match(/\b(\d|one|two|three|four|five|six)\s+(?:ai\s+)?(?:sites?|campus(?:es)?|data\s+cent(?:er|re)s?|locations?|places)\b/i)
  if (k) {
    const n = WORDS[k[1].toLowerCase()] ?? Number(k[1])
    if (n >= 1 && n <= SITES_MAX) out.sites = n
  }
  const byLength = [...(regions || [])].sort((a, b) => b.name.length - a.name.length)
  const hit = byLength.find((r) => new RegExp(`\\b${r.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i').test(s))
  if (hit) out.region = hit.code
  return out
}

// The workspace case for a plan (the result's `case`, or its version without upgrades), loaded
// through the app store the same way a user would build it: the biggest campus is the main site,
// the rest are extra sites (AI-boom style), plus the plan's upgrades, load level and firm switch.
export function loadIntoWorkspace(o, sites, caseBody) {
  const main = { lat: caseBody.lat, lon: caseBody.lon, mw: caseBody.mw }
  // name each extra campus after the plan site at its coordinates (the case carries only lat/lon/mw)
  const infoAt = (s) => sites.find((x) => Math.abs(x.lat - s.lat) < 1e-6 && Math.abs(x.lon - s.lon) < 1e-6) || {}
  const extras = (caseBody.sites || []).map((s, i) => {
    const info = infoAt(s)
    return { id: `plan-${info.sub ?? i}-${i}`, metro: info.town, sub: info.sub, lat: s.lat, lon: s.lon, mw: s.mw }
  })
  const rest = () => {
    o.setExtraSites(extras)
    o.setUpgrades(caseBody.upgrades || {})
    o.setLoadFactor(caseBody.load_factor ?? 1)
    o.setFirm(!!caseBody.firm)
  }
  if (o.region !== caseBody.region) {
    // clears the case, loads the state's grid, flies the map there and drops the main campus once it's in
    o.setRegion(caseBody.region, { place: [main.lat, main.lon], mw: main.mw })
    rest()
  } else {
    o.resetAll()
    o.setMw(main.mw)
    o.place(main.lat, main.lon)
    rest()
  }
}

// "2,000 MW" (whole MW, rounded half up like the backend's step texts)
export const fmtMw = (n) => `${Math.round(n).toLocaleString('en-US')} MW`
