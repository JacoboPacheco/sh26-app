import { api } from '../../api'

// Plant Down's backend calls (backend/plants.py). A case body is the app store's caseBody plus
// `region`; a trip (and a trace on a case) adds `outages` (plant ids) and `retire_fuels` (fuels).
//
//   GET  /api/plants?region=ST                     → {plants, by_fuel, fuels, total_pmax, total_load, region_name, …}
//   POST /api/plants/trip                          → the /api/grid/cascade shape + {removed_mw, removed,
//                                                    removed_output_mw, initial, baseline, added_people, supply}
//   GET  /api/plants/{region}/{id}/trace           → {plant, output_mw, serves: [{area, share, mw, of_output, lat, lon}],
//                                                    rest, exported_mw, method} (base case)
//   POST /api/plants/{region}/{id}/trace (case)    → the same on the case, before any line trips (+ `out`)
//   GET  /api/plants/ranking?region=&load_factor=  → {status, done, total, baseline, site, ranking: [{plant, people,
//        [&lat=&lon=&mw=&firm=]                      added_people, lost_mw, steps, outcome}]} — polled until "ready"
//   POST /api/plants/restore (the trip's body)     → the same case with every plant running (the cascade shape, to
//                                                    replay) + {plants_back, restored, compare: {out, back, held_people,
//                                                    held_mw, held_subs, held_areas}}; cached by the trip before it
//   POST /api/briefing (case + outages)            → the incident report: root_cause.sentence names the plant
const q = encodeURIComponent

export const getPlants = (region) => api(`/api/plants?region=${q(region)}`)
export const tripPlants = (body) => api('/api/plants/trip', { method: 'POST', body })
export const restorePlants = (body) => api('/api/plants/restore', { method: 'POST', body })
export const getPlantBriefing = (body) => api('/api/briefing', { method: 'POST', body })
export function getTrace(region, id, body) {
  const path = `/api/plants/${q(region)}/${q(id)}/trace`
  return body ? api(path, { method: 'POST', body }) : api(path)
}
// `site` = {lat, lon, mw, firm} to rank with the campus placed, or null
export function getRanking(region, loadFactor, site) {
  let path = `/api/plants/ranking?region=${q(region)}&load_factor=${q(Number(loadFactor).toFixed(2))}`
  if (site) path += `&lat=${q(site.lat)}&lon=${q(site.lon)}&mw=${q(Math.round(site.mw))}${site.firm ? '&firm=true' : ''}`
  return api(path)
}
