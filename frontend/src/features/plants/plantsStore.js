import { useEffect, useState, useSyncExternalStore } from 'react'
import { getPlants, getRanking, getTrace, tripPlants } from './plantsApi'

// Plant Down's own state, shared by the map layer (inside the camera) and the panel, which sit in
// different parts of the tree. The case itself (site, size, load level, storm, upgrades) still
// comes from the app store (useOverload); this holds only the plants side:
//
//   region       the state the plant list belongs to
//   list         GET /api/plants for `region`: {status, data, error}
//   selectedId   the plant whose card is open
//   hoverId      the plant under the pointer (the map's label)
//   fuelHover    a fuel chip under the pointer or focus: its plants light up on the map
//   outages      plant ids tripped by hand
//   retireFuels  whole fuel classes taken out
//   trip         the last POST /api/plants/trip: {status, key, data, error}; `key` says which case
//                and which plants it answers, so a changed scenario shows as out of date
//   traces       per (region, plant, case): {status, data, error}
//   rankings     per (region, load level): {status, data, error}

function createStore(initial) {
  let state = initial
  const subs = new Set()
  return {
    get: () => state,
    set(patch) {
      const next = typeof patch === 'function' ? patch(state) : patch
      state = { ...state, ...next }
      subs.forEach((f) => f())
    },
    subscribe(f) {
      subs.add(f)
      return () => subs.delete(f)
    },
  }
}

const IDLE = { status: 'idle', data: null, error: null }
const EMPTY = {
  region: null,
  list: IDLE,
  selectedId: null,
  hoverId: null,
  fuelHover: null,
  outages: [],
  retireFuels: [],
  trip: { ...IDLE, key: null },
  traces: {},
  rankings: {},
}
const store = createStore(EMPTY)

export const getPlantsState = store.get
export const setPlants = store.set
export const usePlants = () => useSyncExternalStore(store.subscribe, store.get)

// The app store's latest value (useOverload()), kept fresh by the layer and the panel: a trip
// resolves after an await, when a render-time copy may be stale.
export const liveOverload = { current: null }

// The region the app is showing: the store's when it has one, else the grid's, else Florida.
export const regionOf = (o) => String(o?.region || o?.grid?.meta?.region || 'FL').toUpperCase()

// The case the backend solves, with its region. Plants removed are not part of it.
export const caseWithRegion = (o, region) => ({ ...(o?.caseBody || {}), region })
const caseKey = (body) => JSON.stringify(body || {})

// ------------------------------------------------------------------ the plant list
let listSeq = 0
export function ensurePlants(region, force = false) {
  const s = store.get()
  if (!force && s.region === region && s.list.status !== 'error' && s.list.status !== 'idle') return
  const id = ++listSeq
  const fresh = s.region !== region
  store.set({
    region,
    list: { status: 'loading', data: fresh ? null : s.list.data, error: null },
    ...(fresh ? { selectedId: null, hoverId: null, fuelHover: null, outages: [], retireFuels: [], trip: { ...IDLE, key: null }, traces: {} } : {}),
  })
  getPlants(region)
    .then((data) => id === listSeq && store.set({ list: { status: 'done', data, error: null } }))
    .catch((error) => id === listSeq && store.set({ list: { status: 'error', data: null, error } }))
}

// Every plant out of service right now: tripped by hand or in a retired fuel class.
export function removedIds(s) {
  const out = new Set(s.outages)
  if (s.retireFuels.length) {
    const fuels = new Set(s.retireFuels)
    ;(s.list.data?.plants || []).forEach((p) => fuels.has(p.fuel) && out.add(p.id))
  }
  return out
}

export const selectPlant = (id) => store.set({ selectedId: id })
export const hoverPlant = (id) => store.get().hoverId !== id && store.set({ hoverId: id })
export const hoverFuel = (fuel) => store.get().fuelHover !== fuel && store.set({ fuelHover: fuel })

// "Start over": every plant back, nothing selected, no result.
export function resetPlants() {
  tripSeq++
  store.set({ selectedId: null, hoverId: null, fuelHover: null, outages: [], retireFuels: [], trip: { ...IDLE, key: null } })
}

// ------------------------------------------------------------------ trip
// The key a trip result answers: the case plus exactly which plants are out.
export const tripKey = (body, outages, fuels) =>
  caseKey({ ...body, outages: [...outages].sort(), retire_fuels: [...fuels].sort() })

let tripSeq = 0
// Start a trip on the case with these plants out. Returns the request (a promise that rejects on
// failure, for the app store's startCascade(extra, pending) to play on the map), or null when
// nothing is out. The result lands here too, unless a newer trip replaced it.
export function startTrip(caseBody, outages, fuels) {
  const id = ++tripSeq
  if (!outages.length && !fuels.length) {
    store.set({ trip: { ...IDLE, key: null } })
    return null
  }
  const key = tripKey(caseBody, outages, fuels)
  store.set((s) => ({ trip: { status: 'loading', key, data: s.trip.data, error: null } }))
  const req = tripPlants({ ...caseBody, outages, retire_fuels: fuels })
  req
    .then((data) => id === tripSeq && store.set({ trip: { status: 'done', key, data, error: null } }))
    .catch((error) => id === tripSeq && store.set({ trip: { status: 'error', key, data: null, error } }))
  return req
}

// ------------------------------------------------------------------ trace (which areas run on a plant)
// The case a trace is taken on: the app's case plus the plants out right now (the trace then
// shows who leans on this plant with those gone). With nothing in it, the cached base-case trace.
export function traceBody(caseBody, s) {
  const out = s.outages.length || s.retireFuels.length
  return out ? { ...caseBody, outages: [...s.outages].sort(), retire_fuels: [...s.retireFuels].sort() } : caseBody
}
const isBaseCase = (body) =>
  !body ||
  (body.lat == null &&
    !(body.sites || []).length &&
    !(body.trip || []).length &&
    !Object.keys(body.upgrades || {}).length &&
    !(body.outages || []).length &&
    !(body.retire_fuels || []).length &&
    Number(body.load_factor ?? 1) === 1)

export const traceKey = (region, id, body) => `${region}|${id}|${isBaseCase(body) ? 'base' : caseKey(body)}`

export function useTrace(region, id, body, enabled = true) {
  const s = usePlants()
  const key = id == null ? null : traceKey(region, id, body)
  const bodyJson = isBaseCase(body) ? null : caseKey(body) // a new object with the same case doesn't refetch
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!enabled || key == null) return undefined
    const cur = store.get().traces[key]
    if (cur && cur.status !== 'error') return undefined
    let live = true
    // after the trip's request has gone: tracing must never hold up a trip
    const t = setTimeout(() => {
      store.set((st) => ({ traces: { ...st.traces, [key]: { status: 'loading', data: null, error: null } } }))
      getTrace(region, id, bodyJson ? JSON.parse(bodyJson) : null)
        .then((data) => live && store.set((st) => ({ traces: { ...st.traces, [key]: { status: 'done', data, error: null } } })))
        .catch((error) => live && store.set((st) => ({ traces: { ...st.traces, [key]: { status: 'error', data: null, error } } })))
    }, 60)
    return () => {
      live = false
      clearTimeout(t)
      // a trace abandoned mid-flight is forgotten, so coming back fetches it again
      const cur2 = store.get().traces[key]
      if (cur2?.status === 'loading') store.set((st) => ({ traces: { ...st.traces, [key]: undefined } }))
    }
  }, [enabled, key, region, id, bodyJson, retry])
  const entry = (key && s.traces[key]) || { status: key == null ? 'idle' : 'loading', data: null, error: null }
  return { ...entry, retry: () => setRetry((n) => n + 1) }
}

// ------------------------------------------------------------------ ranking (computed in the background, polled)
// Per region, load level and campus (`site` = {lat, lon, mw, firm} or null): the backend ranks
// with the campus placed, so the list shows how a data center changes which plants matter.
const POLL_MS = 1500
export const rankingKey = (region, lf, site) =>
  `${region}|${Number(lf).toFixed(2)}|${site ? `${site.lat.toFixed(5)},${site.lon.toFixed(5)},${Math.round(site.mw)},${site.firm ? 1 : 0}` : '-'}`

export function useRanking(region, lf, site, enabled = true) {
  const s = usePlants()
  const key = rankingKey(region, lf, site)
  const [lat, lon, mw, firm] = site ? [site.lat, site.lon, Math.round(site.mw), !!site.firm] : [null, null, null, false]
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!enabled) return undefined
    const cur = store.get().rankings[key]
    if (cur?.status === 'done') return undefined
    let stop = false
    let t = 0
    const put = (entry) => store.set((st) => ({ rankings: { ...st.rankings, [key]: entry } }))
    const tick = () =>
      getRanking(region, lf, lat != null ? { lat, lon, mw, firm } : null)
        .then((data) => {
          if (stop) return
          const ready = data?.status === 'ready'
          put({ status: ready ? 'done' : 'computing', data, error: null })
          if (!ready) t = setTimeout(tick, POLL_MS)
        })
        .catch((error) => !stop && put({ status: 'error', data: store.get().rankings[key]?.data || null, error }))
    if (!cur) put({ status: 'loading', data: null, error: null })
    t = setTimeout(tick, cur ? 0 : 400) // a heat-clock or size-slider drag settles first
    return () => {
      stop = true
      clearTimeout(t)
    }
  }, [enabled, key, region, lf, lat, lon, mw, firm, retry])
  const entry = s.rankings[key] || { status: 'loading', data: null, error: null }
  return { ...entry, retry: () => setRetry((n) => n + 1) }
}
