import { useEffect, useState, useSyncExternalStore } from 'react'
import { api } from '../../api'

// The data-center catalog's backend calls (backend/catalog.py) and the shared state every catalog
// component reads (one fetch, one poll, one selection for the list, the card and the map layer).
//
//   GET /api/catalog         → {status, done, total, entries: [{id, name, company, city, county, state,
//                               state_name, lat, lon, mw, mw_basis, location_basis, status, year, ai,
//                               confidence, sources: [{title, url, supports}], duplicate_of, also_listed_as,
//                               counted, test: null | {tested, reason?, verdict, overloaded, headroom_mw,
//                               sub_name, people, flexible: {...}, firm: {...}, sentence, why}}],
//                               totals, by_state, frame, method, workspace_max_mw}
//   GET /api/catalog/status  → {status: computing|ready|error, done, total}
//   GET /api/catalog/{id}    → the entry + verification + test (with lines, areas, timeline per run)
const q = encodeURIComponent
export const getCatalog = () => api('/api/catalog')
export const getCatalogStatus = () => api('/api/catalog/status')
export const getCatalogEntry = (id) => api(`/api/catalog/${q(id)}`)

// ------------------------------------------------------------------ the list (shared)
let state = { data: null, error: null, loading: false, progress: null, selected: null, stateFilter: '' }
const listeners = new Set()
let pollTimer = null

function set(patch) {
  state = { ...state, ...patch }
  listeners.forEach((f) => f(state))
}

function load() {
  if (state.loading) return
  set({ loading: true, error: null })
  getCatalog()
    .then((d) => {
      set({ data: d, loading: false, progress: { status: d.status, done: d.done, total: d.total } })
      if (d.status === 'computing') poll()
    })
    .catch((err) => set({ error: err, loading: false }))
}

// while the batch tests every campus: poll its progress, then fetch the tested list once
function poll() {
  if (pollTimer) return
  const tick = () =>
    getCatalogStatus()
      .then((s) => {
        pollTimer = null
        set({ progress: s })
        if (s.status === 'computing') pollTimer = setTimeout(tick, 1000)
        else if (s.status === 'ready') load()
        else set({ error: new Error('Testing the campuses failed. Retry to test them again.') })
      })
      .catch((err) => {
        pollTimer = null
        set({ error: err })
      })
  pollTimer = setTimeout(tick, 700)
}

export const selectEntry = (id) => set({ selected: id || null })
export const setStateFilter = (code) => set({ stateFilter: code || '' })

function subscribe(f) {
  listeners.add(f)
  return () => listeners.delete(f)
}
const snapshot = () => state

// {data, error, loading, progress, selected, stateFilter, reload}
export function useCatalog() {
  const s = useSyncExternalStore(subscribe, snapshot)
  useEffect(() => {
    if (!state.data && !state.loading && !state.error) load()
  }, [])
  return { ...s, reload: load }
}

// ------------------------------------------------------------------ one campus's full test (cached)
const details = new Map() // id -> data

export function useCatalogEntry(id) {
  const [res, setRes] = useState(() => ({ id, data: details.get(id) || null, error: null }))
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!id || details.has(id)) return undefined
    let live = true
    getCatalogEntry(id)
      .then((d) => {
        details.set(id, d)
        if (live) setRes({ id, data: d, error: null })
      })
      .catch((err) => live && setRes({ id, data: null, error: err }))
    return () => {
      live = false
    }
  }, [id, attempt])
  // a new id shows its cached answer (or loading) at once, never the previous campus's
  const cur = res.id === id ? res : { id, data: details.get(id) || null, error: null }
  const data = cur.data || details.get(id) || null
  return {
    data,
    error: data ? null : cur.error,
    loading: !!id && !data && !cur.error,
    retry: () => {
      setRes({ id, data: null, error: null })
      setAttempt((n) => n + 1)
    },
  }
}
