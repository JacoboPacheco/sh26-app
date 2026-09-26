import { api } from '../../api'

// Moving a saved case (backend/scenarios.py → case: the full grid.CaseIn) in and out of the store.

// The case's main campus, or null (a storm or a heat wave alone).
export function mainSite(c) {
  if (!c) return null
  if (c.lat != null && c.lon != null && c.mw != null) return { lat: Number(c.lat), lon: Number(c.lon), mw: Number(c.mw) }
  return null
}

// A saved scenario's case, whatever its age: the new `case` column, or the old {lat, lon, mw} row.
export function caseOf(sc) {
  if (sc?.case) return sc.case
  if (sc?.lat != null) return { region: sc.region || 'FL', lat: sc.lat, lon: sc.lon, mw: sc.mw, sites: [], load_factor: 1, trip: [], upgrades: {}, firm: false }
  return null
}

// Load a case into the store (the workspace then shows it). Returns the region code.
export function openCase(o, c) {
  const code = String(c.region || 'FL').toUpperCase()
  const main = mainSite(c)
  if (o.region !== code) {
    o.setRegion(code, main ? { place: [main.lat, main.lon], mw: main.mw } : {})
  } else if (main) {
    o.setMw(main.mw)
    o.setMode('campus')
    o.place(main.lat, main.lon)
  } else {
    o.clearSite()
  }
  o.setExtraSites((c.sites || []).map((s, i) => ({ id: `saved-${i}-${Math.round(s.lat * 1e4)}-${Math.round(s.lon * 1e4)}`, lat: s.lat, lon: s.lon, mw: s.mw })))
  o.setLoadFactor(Number(c.load_factor ?? 1))
  o.setTrip(Array.isArray(c.trip) ? c.trip : [])
  o.setUpgrades(c.upgrades || {})
  o.setFirm(!!c.firm)
  return code
}

// The grid endpoints' body for a case (drops display-only fields like the storm's name).
export function caseBody(c) {
  const body = {
    region: c.region || 'FL',
    firm: !!c.firm,
    load_factor: Number(c.load_factor ?? 1),
    trip: c.trip || [],
    upgrades: c.upgrades || {},
    sites: (c.sites || []).map(({ lat, lon, mw }) => ({ lat, lon, mw })),
  }
  const m = mainSite(c)
  if (m) Object.assign(body, m)
  return body
}

// A scenario by id (GET /api/scenarios/{id}; older backends: found in the list) or a share slug.
export async function fetchScenario(idOrSlug) {
  const key = String(idOrSlug)
  if (!/^\d+$/.test(key)) return { ...(await api(`/api/share/${encodeURIComponent(key)}`)), shared: true }
  try {
    return await api(`/api/scenarios/${key}`)
  } catch (err) {
    if (!/not found|405|404/i.test(err.message)) throw err
    const list = await api('/api/scenarios')
    const sc = list.find((s) => String(s.id) === key)
    if (!sc) throw new Error('No scenario with that id in this library')
    return sc
  }
}

// What a case does, on the synthetic model: the saved result when it has one, else solved now.
export async function resultOf(sc) {
  if (sc?.result && sc.result.people !== undefined) return { ...sc.result, live: false }
  const c = caseOf(sc)
  if (!c) throw new Error('This scenario has no case to solve')
  const cas = await api('/api/grid/cascade', { method: 'POST', body: caseBody(c) })
  const peak = Math.max(0, ...cas.steps.map((s) => s.people || 0), cas.people || 0)
  return {
    verdict: cas.people > 0 || peak > 0 ? 'blackout' : cas.total_steps ? 'trips' : 'holds',
    people: cas.people || 0,
    people_peak: peak,
    steps: cas.total_steps,
    overloaded: null,
    lost_mw: cas.lost_mw,
    headroom_mw: cas.headroom_mw,
    site_cut_off: !!cas.site_cut_off,
    firm: !!cas.firm,
    firm_held: cas.firm_held,
    shed_mw: cas.shed_mw || 0,
    areas_top: [],
    region: cas.region,
    sub_area: cas.sub_area,
    mw_total: cas.mw,
    load_factor: cas.load_factor,
    storm_lines: (c.trip || []).length,
    live: true,
  }
}
