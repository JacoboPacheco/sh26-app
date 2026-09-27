import { useEffect, useRef, useSyncExternalStore } from 'react'
import { useOverload } from '../../store'
import { getDanger, sizeFor } from './dangerApi'

// State the danger panel and the map layer share: the toggle, the zone under the pointer, and the
// answers per (region, size, load level, firm). It lives here, not in the app store, so the feature
// stays in its own folder; the case it reads (size, level, region, firm) still comes from useOverload().

function createStore(initial) {
  let state = initial
  const subs = new Set()
  return {
    get: () => state,
    set(next) {
      state = typeof next === 'function' ? next(state) : next
      subs.forEach((f) => f())
    },
    subscribe(f) {
      subs.add(f)
      return () => subs.delete(f)
    },
  }
}
const useStore = (store) => useSyncExternalStore(store.subscribe, store.get)

// ------------------------------------------------------------------ the toggle + hover
const ui = createStore({ on: false, hover: null })
export const useDangerUi = () => useStore(ui)
export const setDangerOn = (on) => ui.set((s) => ({ ...s, on: typeof on === 'function' ? !!on(s.on) : !!on, hover: null }))
export const setDangerHover = (id) => ui.set((s) => (s.hover === id ? s : { ...s, hover: id }))

// ------------------------------------------------------------------ answers, per key
const DEBOUNCE_MS = 400 // let the size slider settle
const POLL_MS = 4000 // a partial answer (the server finishes it in the background) is refetched this often
const MAX_POLLS = 30 // ~2 minutes; the route allows 30 requests a minute per visitor

const answers = createStore({}) // key -> {status: 'loading' | 'done' | 'error', data, error}
const inflight = new Set()
const polls = new Map() // key -> timeout id
const pollCount = new Map() // key -> polls so far
const lastShown = new Map() // region -> the key of the last answer on screen (shown, marked stale, while the next loads)
let wanted = null // the key on screen now: polling stops for any other

export const keyOf = ({ region, mw, loadFactor, firm }) => `${region}|${sizeFor(mw)}|${Number(loadFactor).toFixed(2)}|${firm ? 1 : 0}`

function fetchKey(key, params, refresh = false) {
  if (inflight.has(key)) return
  inflight.add(key)
  if (!refresh) answers.set((m) => ({ ...m, [key]: { status: 'loading', data: null, error: null } }))
  getDanger(params)
    .then((data) => {
      answers.set((m) => ({ ...m, [key]: { status: 'done', data, error: null } }))
      if (key === wanted) lastShown.set(params.region, key)
      if (data.partial) schedulePoll(key, params)
    })
    .catch((error) => {
      // a failed refresh keeps the answer already on screen (and stops polling)
      if (!refresh) answers.set((m) => ({ ...m, [key]: { status: 'error', data: null, error } }))
    })
    .finally(() => inflight.delete(key))
}

function schedulePoll(key, params) {
  if (polls.has(key) || (pollCount.get(key) || 0) >= MAX_POLLS) return
  const t = setTimeout(() => {
    polls.delete(key)
    if (key !== wanted) return
    pollCount.set(key, (pollCount.get(key) || 0) + 1)
    fetchKey(key, params, true)
  }, POLL_MS)
  polls.set(key, t)
}

// The danger zones for the case on screen: {status, data, error, stale, size, retry}. `stale` means
// the data is the previous answer for this region (a new size or level is still computing).
// Mounted by both the panel and the layer; requests are shared, never doubled.
export function useDangerZones() {
  const { region, mw, loadFactor, firm } = useOverload()
  const { on } = useStore(ui)
  const all = useStore(answers)
  const enabled = on && region !== 'US'
  const size = sizeFor(mw)
  const key = keyOf({ region, mw, loadFactor, firm })

  useEffect(() => {
    if (!enabled) {
      if (wanted === key) wanted = null // off: stop refreshing a partial answer
      return undefined
    }
    wanted = key
    pollCount.set(key, 0)
    const params = { region, mw: size, loadFactor, firm }
    const cur = answers.get()[key]
    if (cur?.status === 'done') {
      lastShown.set(region, key)
      if (cur.data.partial) schedulePoll(key, params)
      return undefined
    }
    if (cur) return undefined // loading, or an error waiting for Retry
    const t = setTimeout(() => fetchKey(key, params), DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [enabled, key, region, size, loadFactor, firm])

  const entry = all[key] || { status: 'loading', data: null, error: null }
  let data = entry.data
  let stale = false
  if (!data && entry.status === 'loading') {
    const prev = lastShown.get(region)
    const shown = prev && prev !== key ? all[prev]?.data : null
    if (shown) {
      data = shown
      stale = true
    }
  }
  return {
    enabled,
    status: entry.status,
    data,
    error: entry.error,
    stale,
    size,
    retry: () => fetchKey(key, { region, mw: size, loadFactor, firm }),
  }
}

// "Start over" turns the zones off (like the heatmap); a region change keeps them on for the new state.
export function useResetOff() {
  const { resetCount, region } = useOverload()
  const prev = useRef({ resetCount, region })
  useEffect(() => {
    const p = prev.current
    if (p.resetCount !== resetCount && p.region === region) setDangerOn(false)
    prev.current = { resetCount, region }
  }, [resetCount, region])
}

// ------------------------------------------------------------------ numbers
// 7,120,592 -> "7.1M", 870,986 -> "871K", 1,000,000 -> "1M" (estimates: never more precise than this)
export function compact(n) {
  const v = Math.max(0, Number(n) || 0)
  if (v >= 1e6) return `${(v / 1e6).toLocaleString('en-US', { maximumFractionDigits: v >= 1e7 ? 0 : 1 })}M`
  if (v >= 1e3) return `${Math.round(v / 1e3).toLocaleString('en-US')}K`
  return String(Math.round(v))
}

// Is the campus on screen placed at this zone? (a zone click places it exactly at the zone's substation)
export const isPlacedAt = (site, z) => !!site && Math.abs(site.lat - z.lat) < 1e-6 && Math.abs(site.lon - z.lon) < 1e-6
