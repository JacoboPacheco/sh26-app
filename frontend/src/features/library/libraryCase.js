import { useSyncExternalStore } from 'react'
import { getHurricane } from '../hurricane/hurricaneStore'
import { STATES, fmt } from '../../geo'

// Moving saved cases in and out of the store, and the little the Library remembers on its own:
// which saved scenario is open in the workspace (so a change to it saves as a new version of it).

export const VERDICT = { holds: 'Holds', trips: 'Lines trip, lights stay on', blackout: 'Blackout' } // the app shell's words
export const verdictTone = (v) => (v === 'blackout' ? 'bad' : v === 'trips' ? 'warn' : v === 'holds' ? 'ok' : 'idle')

// The app shell's routes (features/app/router.js): the Library links into them.
const BASE = '#/next'
export const appHref = (path = '') => `${BASE}${path}`
export const goTo = (path) => {
  window.location.hash = appHref(path)
}
// A share link opens the shell's brief page, which reads /api/share/<slug> when the id isn't a number.
export const shareUrl = (slug) => `${window.location.origin}${window.location.pathname}${appHref(`/brief/${encodeURIComponent(slug)}`)}`

// 869608 -> "870k", 1043234 -> "1.04M", 42552 -> "42.6k", 312 -> "312"
export function compact(n) {
  const v = Math.max(0, Number(n) || 0)
  if (v < 1000) return fmt(v)
  if (v < 1e6) return `${Number((v / 1e3).toPrecision(3))}k`
  return `${Number((v / 1e6).toPrecision(3))}M`
}

export const regionName = (code) => STATES[code || 'FL']?.name || code || 'Florida'

// A saved scenario's case, whatever its age: the `case` column, or an old {lat, lon, mw} row.
export function caseOf(sc) {
  if (sc?.case) return sc.case
  if (sc?.lat != null) return { region: sc.region || 'FL', lat: sc.lat, lon: sc.lon, mw: sc.mw, sites: [], load_factor: 1, trip: [], upgrades: {}, firm: false }
  return null
}

const mainOf = (c) => (c && c.lat != null && c.lon != null && c.mw != null ? { lat: Number(c.lat), lon: Number(c.lon), mw: Number(c.mw) } : null)

// Two cases are the same scenario when this string is the same (labels like the storm's name don't count).
export function caseKey(c) {
  if (!c) return ''
  const r5 = (x) => Math.round(Number(x) * 1e5) / 1e5
  const r1 = (x) => Math.round(Number(x) * 10) / 10
  const m = mainOf(c)
  return JSON.stringify({
    r: String(c.region || 'FL').toUpperCase(),
    m: m ? [r5(m.lat), r5(m.lon), r1(m.mw)] : null,
    s: (c.sites || []).map((s) => [r5(s.lat), r5(s.lon), r1(s.mw)]),
    l: Math.round(Number(c.load_factor ?? 1) * 100),
    t: [...new Set((c.trip || []).map(Number))].sort((a, b) => a - b),
    u: Object.entries(c.upgrades || {})
      .map(([k, v]) => [Number(k), r1(v)])
      .sort((a, b) => a[0] - b[0]),
    f: !!c.firm,
  })
}

// The case on screen, ready to save — null when there is nothing in it yet (or its state's grid is
// still loading). A storm picked from the hypothetical presets keeps its name.
export function caseFromStore(o) {
  if (!o || o.region === 'US' || o.grid?.meta?.region !== o.region) return null
  const c = { ...o.caseBody, region: o.region }
  if (!(mainOf(c) || c.sites?.length || c.trip?.length || Math.round((c.load_factor ?? 1) * 100) !== 100)) return null
  const h = getHurricane()
  if (c.trip?.length && h.presetId && h.phase === 'landed') {
    const p = (h.presets || []).find((x) => x.id === h.presetId)
    c.storm = { preset: h.presetId, ...(p?.name ? { name: p.name } : {}) }
  }
  return c
}

// A name for the case on screen: "Fort Myers · 1,500 MW", "Abilene, Texas · 1,200 MW in a heat wave".
export function suggestName(o) {
  const c = caseFromStore(o)
  if (!c) return ''
  const where = o.result?.sub_area || null
  const state = c.region === 'FL' ? '' : `, ${regionName(c.region)}`
  let name
  if (mainOf(c) && where) name = `${where}${state} · ${fmt(c.mw)} MW`
  else if (mainOf(c)) name = `${regionName(c.region)} · ${fmt(c.mw)} MW`
  else if (c.sites?.length) name = `${regionName(c.region)} · ${c.sites.length} ${c.sites.length === 1 ? 'campus' : 'campuses'}`
  else if (c.trip?.length) name = `${regionName(c.region)} · storm, ${fmt(c.trip.length)} lines out`
  else name = regionName(c.region)
  if (mainOf(c) && c.sites?.length) name += ` + ${c.sites.length} more`
  const pct = Math.round(Number(c.load_factor ?? 1) * 100)
  if (pct > 100) name += ' in a heat wave'
  else if (pct < 100) name += ` at ${pct} % load`
  if (c.firm && (mainOf(c) || c.sites?.length)) name += ', firm'
  return name.slice(0, 80)
}

// Load a case into the store: the workspace then shows it (and re-solves it). Returns the region.
// A case in the state on screen starts from a clean slate (Start over); another state loads first.
export function loadCase(o, c) {
  if (typeof o.loadCase === 'function') return o.loadCase(c) // the store's own, when it has one
  const code = String(c.region || 'FL').toUpperCase()
  const main = mainOf(c)
  if (o.region !== code) {
    if (o.setRegion(code, main ? { place: [main.lat, main.lon], mw: main.mw } : {}) === false) return null // not a state this app has
  } else {
    o.resetAll()
    if (main) {
      o.setMw(main.mw)
      o.place(main.lat, main.lon)
    }
  }
  const stamp = Date.now().toString(36)
  o.setExtraSites((c.sites || []).map((s, i) => ({ id: `lib${stamp}${i}`, lat: Number(s.lat), lon: Number(s.lon), mw: Number(s.mw) })))
  o.setLoadFactor(Number(c.load_factor ?? 1))
  o.setTrip(Array.isArray(c.trip) ? c.trip.map(Number) : [])
  o.setUpgrades({ ...(c.upgrades || {}) })
  o.setFirm(!!c.firm)
  o.setMode(!main && c.sites?.length ? 'boom' : 'campus')
  return code
}

// ------------------------------------------------------------------ the scenario open in the workspace
// {id, rootId, name, rootName, key, example, reset}: `reset` is the store's resetCount right after the
// open (loading bumps it once); a later Start over or state change bumps it again and forgets it.
let opened = null
const listeners = new Set()
const emit = () => listeners.forEach((f) => f())
const subscribe = (f) => {
  listeners.add(f)
  return () => listeners.delete(f)
}

export function markOpened(sc, reset, rootName) {
  opened = {
    id: sc.id,
    rootId: sc.parent_id || sc.id,
    name: sc.name,
    rootName: rootName || sc.name,
    key: caseKey(caseOf(sc)),
    example: !!sc.example,
    reset,
  }
  emit()
}

export function clearOpened() {
  opened = null
  emit()
}

// The open scenario, or null once the case was started over or moved to another state.
export function useOpened(o) {
  const cur = useSyncExternalStore(subscribe, () => opened)
  if (!cur || !o) return cur
  return o.resetCount > cur.reset ? null : cur
}

// Open a saved scenario in the workspace. Returns the region code (null when it has no case).
export function openScenario(o, sc, all = o.scenarios || []) {
  const c = caseOf(sc)
  if (!c) return null
  const before = o.resetCount
  const code = loadCase(o, c)
  if (!code) return null
  const root = sc.parent_id ? all.find((x) => x.id === sc.parent_id) : null
  markOpened(sc, before + 1, root?.name)
  return code
}
