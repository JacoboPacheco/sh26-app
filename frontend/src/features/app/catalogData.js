import { useEffect, useSyncExternalStore } from 'react'
import { api } from '../../api'
import floridaFive from '../../data/florida_five.json'

// The national catalog of announced AI data centers (backend/catalog.py → GET /api/catalog, built by
// the catalog track from backend/demo/datacenters_us.json). One fetch shared by every page. Until
// the endpoint answers, the five sourced Florida proposals (frontend/src/data/florida_five.json)
// stand in, labeled as such, and the catalog is retried in the background.

export const MODEL_MW_MAX = 50000 // backend/grid.py MW_MAX: the largest campus a case can hold (50 GW)

let state = { status: 'idle', entries: null, totals: null, progress: null, error: null, source: null }
const listeners = new Set()
const set = (patch) => {
  state = { ...state, ...patch }
  listeners.forEach((fn) => fn())
}
const subscribe = (fn) => {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

let inFlight = null
let retryTimer = null
let statusTimer = null

const FALLBACK = floridaFive.entries.map((e) =>
  normalize({ ...e, city: e.place, state: 'FL', company: e.company || null, confidence: e.confidence || null }),
)

// Accept the catalog's entry however it names things; keep the original as `raw`.
export function normalize(e) {
  const mw = Number(e.mw ?? e.reported_mw ?? e.size_mw)
  return {
    id: String(e.id ?? e.slug ?? ''),
    name: e.name || 'Unnamed campus',
    company: e.company || e.developer || null,
    city: e.city || e.place || null,
    county: e.county || null,
    state: String(e.state || e.region || '').toUpperCase() || null,
    stateName: e.state_name || null,
    lat: Number(e.lat),
    lon: Number(e.lon),
    mw: Number.isFinite(mw) ? mw : null,
    mwBasis: e.mw_basis || null,
    status: e.status || null,
    year: e.year || null,
    ai: e.ai ?? null,
    sources: Array.isArray(e.sources) ? e.sources.filter((s) => s && s.url) : [],
    confidence: e.confidence || null,
    locationBasis: e.location_basis || null,
    verification: e.verification || null,
    test: e.test ?? e.summary ?? e.result ?? null,
    raw: e,
  }
}

function fetchCatalog() {
  if (inFlight) return inFlight
  set({ status: state.entries ? state.status : 'loading', error: null })
  inFlight = api('/api/catalog')
    .then((data) => {
      const list = Array.isArray(data) ? data : data?.entries || data?.catalog || []
      // duplicates of the same campus are folded into one row by the catalog (duplicate_of)
      const entries = list
        .filter((e) => !e.duplicate_of)
        .map(normalize)
        .filter((e) => e.id && Number.isFinite(e.lat) && Number.isFinite(e.lon))
      set({
        status: 'ready',
        entries,
        totals: Array.isArray(data) ? null : data?.totals || data?.national || null,
        source: Array.isArray(data) ? null : data?.note || data?.source || null,
        error: null,
      })
      watchProgress(data)
    })
    .catch((error) => {
      // not built yet (404) or the backend is down: keep the Florida five on screen and try again
      set({ status: 'fallback', entries: FALLBACK, error })
      clearTimeout(retryTimer)
      retryTimer = setTimeout(() => {
        inFlight = null
        if (listeners.size) fetchCatalog()
      }, 20000)
    })
    .finally(() => {
      inFlight = null
    })
  return inFlight
}

// The catalog tests every entry on its state's model in the background; while it does, poll its
// progress and refetch once it's done so every row gets its verdict.
function watchProgress(data) {
  const testing = data && !Array.isArray(data) && (data.status === 'computing' || data.status === 'testing' || data.testing)
  const untested = state.entries?.some((e) => e.test == null)
  if (testing && Number.isFinite(data.done) && Number.isFinite(data.total)) set({ progress: { done: data.done, total: data.total } })
  if (!testing && !untested) return set({ progress: null })
  clearTimeout(statusTimer)
  statusTimer = setTimeout(async () => {
    try {
      const st = await api('/api/catalog/status')
      const done = Number(st?.done ?? st?.tested ?? st?.n ?? NaN)
      const total = Number(st?.total ?? st?.of ?? st?.m ?? NaN)
      if (st?.status === 'error') return set({ progress: null })
      const finished = st?.status === 'ready' || st?.ready === true || st?.status === 'done' || (Number.isFinite(done) && done >= total)
      set({ progress: Number.isFinite(done) && Number.isFinite(total) ? { done, total } : null })
      if (finished) {
        set({ progress: null })
        if (untested || testing) fetchCatalog()
      } else if (listeners.size) watchProgress({ testing: true })
    } catch {
      set({ progress: null }) // no status endpoint: rows simply stay "not tested yet"
    }
  }, 2500)
}

export function useCatalog() {
  const snap = useSyncExternalStore(subscribe, () => state)
  useEffect(() => {
    if (state.status === 'idle') fetchCatalog()
  }, [])
  return { ...snap, retry: () => fetchCatalog() }
}

export const getCatalogState = () => state

// One entry's detail (GET /api/catalog/{id}: the entry and its full test on its state's model).
const detailCache = new Map() // id -> Promise (shared by every caller; dropped on failure so a retry refetches)
export function fetchEntry(id) {
  if (!detailCache.has(id)) {
    const p = api(`/api/catalog/${encodeURIComponent(id)}`).then((d) => {
      const entry = normalize(d?.entry || d)
      return { entry, test: d?.test ?? d?.result ?? entry.test ?? null, raw: d }
    })
    p.catch(() => detailCache.delete(id))
    detailCache.set(id, p)
  }
  return detailCache.get(id)
}

// Pure helpers ----------------------------------------------------------------------------

// A catalog test's verdict, however the catalog names it: 'holds' | 'over' | 'cascades' | null.
export function verdictOf(test) {
  if (!test || test.tested === false) return null
  if (test.verdict === 'outage') return 'cascades'
  if (test.verdict === 'overloads') return 'over'
  if (test.verdict === 'fits') return 'holds'
  const flex = test.flexible || test.flex || test
  const v = String(flex.verdict || test.verdict || '').toLowerCase()
  if (v.startsWith('hold') || v === 'ok' || v === 'calm') return 'holds'
  if (v.includes('cascade') || v.includes('black')) return 'cascades'
  if (v.includes('over')) return 'over'
  const people = Number(flex.people ?? test.people ?? 0)
  const over = Number(flex.overloaded ?? test.overloaded ?? test.over ?? 0)
  if (people > 0) return 'cascades'
  if (over > 0) return 'over'
  if (flex.people !== undefined || test.overloaded !== undefined) return 'holds'
  return null
}

export const VERDICT_TEXT = { holds: 'Holds', over: 'Over limit', cascades: 'Blackout' }

// "under construction" -> "Under construction"
export const statusText = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Status unknown')

export function placeText(e) {
  return [e.city, e.state].filter(Boolean).join(', ')
}

// The case a catalog entry becomes in the workspace: its reported size at its reported location,
// capped at the model's limit.
export function testQuery(e) {
  const mw = Math.min(Math.round(e.mw || 500), MODEL_MW_MAX)
  return { lat: e.lat, lon: e.lon, mw, dc: e.id }
}
