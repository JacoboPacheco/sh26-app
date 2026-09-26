import { useCallback, useEffect, useSyncExternalStore } from 'react'
import { fmt } from '../../geo'
import { getArea, getAreas, getExposure, levelFor } from './areaApi'

// The areas feature's own state, shared by the panel (search + card) and the map layers, which sit
// in different parts of the tree. The case itself still lives in the app store (useOverload): this
// holds only what the areas endpoints answered, which area is open, and which test is hovered.

// ------------------------------------------------------------------ a tiny external store
function createStore(initial) {
  let state = initial
  const subs = new Set()
  return {
    get: () => state,
    set(patch) {
      state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) }
      subs.forEach((f) => f())
    },
    subscribe(f) {
      subs.add(f)
      return () => subs.delete(f)
    },
  }
}

// ------------------------------------------------------------------ fetched resources, by key
const IDLE = Object.freeze({ status: 'idle', data: null, error: null })
const cache = createStore({}) // key -> {status, data, error}
const inflight = new Map() // key -> request id

function load(key, fetcher, force = false) {
  const cur = cache.get()[key]
  if (!force && cur && (cur.status === 'loading' || cur.status === 'done')) return
  const id = (inflight.get(key) || 0) + 1
  inflight.set(key, id)
  cache.set({ [key]: { status: 'loading', data: null, error: null } })
  fetcher()
    .then((data) => inflight.get(key) === id && cache.set({ [key]: { status: 'done', data, error: null } }))
    .catch((error) => inflight.get(key) === id && cache.set({ [key]: { status: 'error', data: null, error } }))
}

// {status: 'idle' | 'loading' | 'done' | 'error', data, error, retry}. A null key stays idle.
function useResource(key, fetcher) {
  const snap = useSyncExternalStore(cache.subscribe, () => (key ? cache.get()[key] : null) || IDLE)
  useEffect(() => {
    if (key) load(key, fetcher)
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps -- the key names the request
  const retry = useCallback(() => key && load(key, fetcher, true), [key]) // eslint-disable-line react-hooks/exhaustive-deps
  return { ...snap, retry }
}

const usable = (region) => region && region !== 'US'

// Every area of a region's model, most people first.
export const useAreaList = (region) => useResource(usable(region) ? `list:${region}` : null, () => getAreas(region))

// The conditions a stress battery runs under: the workspace's firm switch and its time of day
// (a heat-clock preset; any other level falls back to the summer peak).
export const conditionsOf = (o) => ({ firm: !!o.firm, level: levelFor(o.loadFactor) })

export const useExposure = (region, c) =>
  useResource(usable(region) ? `exposure:${region}:${c.firm}:${c.level}` : null, () => getExposure(region, c))

export const useAreaProfile = (region, slug, c) =>
  useResource(usable(region) && slug ? `area:${region}:${slug}:${c.firm}:${c.level}` : null, () => getArea(region, slug, c))

// ------------------------------------------------------------------ selection and hover
const ui = createStore({ region: null, slug: null, name: null, hoverCase: null })
export const useAreaUi = () => useSyncExternalStore(ui.subscribe, ui.get)
export const selectArea = (region, area) => ui.set({ region, slug: area?.slug || null, name: area?.name || null })
export const clearArea = () => ui.set({ slug: null, name: null, hoverCase: null })
// the test under the pointer or keyboard focus in the card: the map marks where its campus sits
export const hoverCase = (c) => ui.set({ hoverCase: c?.case?.lat != null ? { lat: c.case.lat, lon: c.case.lon, label: c.label } : null })

// ------------------------------------------------------------------ opening a test in the workspace
// Replaces the workspace's case with the test's own (the same body the battery ran), so a run of
// the cascade there replays the result shown here. `c.case` = {region, lat, lon, mw, firm, load_factor}.
export function openCase(o, c) {
  const k = c.case
  if (k.region !== o.region) {
    // a region switch clears the case (firm off, summer peak); the areas are listed per region, so
    // this happens only for a test opened from another state's card
    o.setRegion(k.region, k.lat != null ? { place: [k.lat, k.lon], mw: k.mw } : {})
    return
  }
  o.setTrip([])
  o.setExtraSites([])
  o.setUpgrades({})
  if (o.firm !== !!k.firm) o.setFirm(!!k.firm)
  if (Number(o.loadFactor).toFixed(2) !== Number(k.load_factor).toFixed(2)) o.setLoadFactor(k.load_factor)
  o.setMode('campus')
  if (k.lat == null) {
    o.clearSite() // the heat wave alone: no campus
    return
  }
  o.pickScenario({ region: k.region, lat: k.lat, lon: k.lon, mw: k.mw })
}

// Does the workspace hold exactly this test's case right now (nothing else added)?
export function isOpen(o, k) {
  if (!k || k.region !== o.region || !!k.firm !== !!o.firm) return false
  if (Number(o.loadFactor).toFixed(2) !== Number(k.load_factor).toFixed(2)) return false
  if (o.trip.length || o.extraSites.length || Object.keys(o.upgrades || {}).length) return false
  if (k.lat == null) return !o.site
  return !!o.site && Math.abs(o.site.lat - k.lat) < 1e-9 && Math.abs(o.site.lon - k.lon) < 1e-9 && Number(o.mw) === Number(k.mw)
}

// ------------------------------------------------------------------ numbers
// An estimate reads like one: nearest thousand from 10,000 up, nearest hundred from 1,000, else ten.
export function roundPeople(n) {
  const v = Math.max(0, Number(n) || 0)
  if (v >= 10000) return Math.round(v / 1000) * 1000
  if (v >= 1000) return Math.round(v / 100) * 100
  return Math.round(v / 10) * 10
}
export const peopleNum = (n) => fmt(roundPeople(n))
export const mwText = (mw) => (mw >= 1000 && mw % 100 === 0 ? `${(mw / 1000).toLocaleString('en-US')} GW` : `${fmt(mw)} MW`)
export const levelText = (lf) =>
  ({ '0.62': '3 AM (62 % of summer peak)', '0.82': '9 AM (82 % of summer peak)', '1.00': 'the 4 PM summer peak', '1.04': 'a heat wave (104 % of summer peak)' })[
    Number(lf).toFixed(2)
  ] || `${Math.round(Number(lf) * 100)} % of summer peak`

// ------------------------------------------------------------------ search
const MAX_OPTIONS = 8
const norm = (s) =>
  String(s || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{M}/gu, '') // accents: "Saint Lucíe" matches "saint lucie"
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()

// Best matches first: the name starts with the query, then a word in it does, then it contains
// it; within each, the most people first (the list arrives sorted that way).
export function matchAreas(areas, query, max = MAX_OPTIONS) {
  const q = norm(query)
  if (!q) return areas.slice(0, max)
  const ranked = []
  for (const a of areas) {
    const n = norm(a.name)
    const r = n.startsWith(q) ? 0 : ` ${n}`.includes(` ${q}`) ? 1 : n.includes(q) ? 2 : -1
    if (r >= 0) ranked.push([r, a])
  }
  return ranked
    .sort((x, y) => x[0] - y[0])
    .slice(0, max)
    .map(([, a]) => a)
}
