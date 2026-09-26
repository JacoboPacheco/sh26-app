import { api } from '../../api'

// The areas endpoints (backend/towns.py). An area is the town a synthetic substation is named after.
//   GET /api/areas?region=FL                      -> [{slug, name, subs, sub_ids, people, load_mw, lat, lon}]
//   GET /api/areas/FL/exposure?firm=&load_factor= -> {tested, cases, heat_wave, areas: [{slug, name, hits, people_max}], …}
//   GET /api/areas/FL/naples?firm=&load_factor=   -> {name, people, substations, room, beyond, exposure: {tested, hits, cases, heat_wave}, …}
// People are estimates (lost load x the state's people per MW); the grid is a synthetic model.

const enc = encodeURIComponent

// The load levels a stress battery runs at: the heat clock's presets (features/heat/presets.js).
export const LEVELS = [0.62, 0.82, 1.0, 1.04]
export const levelFor = (lf) => LEVELS.find((l) => l.toFixed(2) === Number(lf).toFixed(2)) ?? 1.0

const cond = ({ firm, level }) => `firm=${firm ? 'true' : 'false'}&load_factor=${level}`

export const getAreas = (region) => api(`/api/areas?region=${enc(region)}`)
export const getExposure = (region, c) => api(`/api/areas/${enc(region)}/exposure?${cond(c)}`)
export const getArea = (region, slug, c) => api(`/api/areas/${enc(region)}/${enc(slug)}?${cond(c)}`)
