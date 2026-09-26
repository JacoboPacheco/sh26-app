import { useEffect, useSyncExternalStore } from 'react'
import { api } from '../../api'

// Know before you run (backend/forecast.py). Two answers for the case on screen:
//   /api/forecast        the verdict, the site's room, the first line to go and the cascade in both
//                        modes (flexible and firm), for the exact size;
//   /api/forecast/sweep  the same site at 100-2,000 MW: people without power by size, both modes.
// Both come back with flexible AND firm, so the firm switch never refetches. Answers are cached by
// case (a small shared store), so the card and the chart share one request and a size you go back to
// is instant.

export const postForecast = (body) => api('/api/forecast', { method: 'POST', body })
export const postSweep = (body) => api('/api/forecast/sweep', { method: 'POST', body })

export const FORECAST_DELAY_MS = 300 // wait for the size slider to settle
export const SWEEP_DELAY_MS = 150
const MAX_ENTRIES = 60

// ------------------------------------------------------------------ the case each answer is for
// The store's case (useOverload().caseBody) as the forecast asks for it, or null when there is
// nothing to forecast: the national map, a state whose grid is still loading, or an empty case.
export function forecastBody(o) {
  if (!o || !o.caseBody) return null
  const { region, grid, site, extraSites = [], trip = [], loadFactor = 1 } = o
  if (region === 'US' || (grid?.meta?.region || 'FL') !== region) return null
  if (!(site || extraSites.length || trip.length || loadFactor !== 1)) return null
  const { firm: _firm, ...rest } = o.caseBody // both modes come back
  return rest
}

// The sweep varies the main campus's size, so it needs a site and ignores the size.
export function sweepBody(o) {
  const body = forecastBody(o)
  if (!body || body.lat == null || body.lon == null) return null
  const { mw: _mw, ...rest } = body
  return rest
}

// ------------------------------------------------------------------ a tiny shared store
const entries = new Map() // key -> {status: 'loading' | 'done' | 'error', data, error}
const lastDone = { forecast: null, sweep: null } // the newest answer of each kind (held, dimmed, while the next loads)
const subs = new Set()
let seq = 0
const inflight = new Map() // key -> request id

function emit() {
  subs.forEach((f) => f())
}
function subscribe(f) {
  subs.add(f)
  return () => subs.delete(f)
}
function put(key, entry) {
  entries.delete(key)
  entries.set(key, entry)
  while (entries.size > MAX_ENTRIES) entries.delete(entries.keys().next().value)
  emit()
}

const keyOf = (kind, body) => (body ? `${kind}:${JSON.stringify(body)}` : null)

// Load (or reload with force) one answer; concurrent callers share the request.
export function load(kind, body, force = false) {
  const key = keyOf(kind, body)
  if (!key) return
  const cur = entries.get(key)
  if (!force && cur && cur.status !== 'error') return
  const id = ++seq
  inflight.set(key, id)
  put(key, { status: 'loading', data: cur?.data || null, error: null })
  const call = kind === 'sweep' ? postSweep : postForecast
  call(body)
    .then((data) => {
      if (inflight.get(key) !== id) return
      inflight.delete(key)
      lastDone[kind] = { key, data }
      put(key, { status: 'done', data, error: null })
    })
    .catch((error) => {
      if (inflight.get(key) !== id) return
      inflight.delete(key)
      put(key, { status: 'error', data: null, error })
    })
}

const IDLE = { status: 'idle', data: null, error: null }

// {status, data (for this case), shown (this case's, else the last answer, dimmed), stale, error, retry}
function useAnswer(kind, body, delay) {
  const key = keyOf(kind, body)
  const entry = useSyncExternalStore(subscribe, () => (key ? entries.get(key) || IDLE : IDLE))
  useEffect(() => {
    if (!key) return undefined
    if (entries.has(key)) return undefined // loaded, loading, or failed (Retry reloads it)
    const t = setTimeout(() => load(kind, JSON.parse(key.slice(kind.length + 1))), delay)
    return () => clearTimeout(t)
  }, [kind, key, delay])
  // a sweep answers progressively (complete: false while sizes are still being tested): ask again
  const partial = entry.status === 'done' && entry.data?.complete === false
  useEffect(() => {
    if (!partial || !key) return undefined
    const t = setTimeout(() => load(kind, JSON.parse(key.slice(kind.length + 1)), true), 250)
    return () => clearTimeout(t)
  }, [partial, key, kind, entry])
  const prev = lastDone[kind]
  const shown = entry.data || (key && prev ? prev.data : null)
  return {
    status: key ? (entry.status === 'idle' ? 'loading' : entry.status) : 'idle',
    data: entry.data,
    shown,
    stale: !!shown && !entry.data,
    error: entry.error,
    retry: () => key && load(kind, JSON.parse(key.slice(kind.length + 1)), true),
  }
}

export const useForecast = (body) => useAnswer('forecast', body, FORECAST_DELAY_MS)
export const useSweep = (body) => useAnswer('sweep', body, SWEEP_DELAY_MS)

// ------------------------------------------------------------------ words and numbers
export const VERDICTS = {
  holds: { label: 'Holds', hint: 'Every line stays within its limit' },
  over_limit: { label: 'Over limit', hint: 'Lines go over their limits, but nobody loses power' },
  cascades: { label: 'Blackout', hint: 'People lose power' },
}

// 1,250,000 -> "1.25M", 28,350 -> "28k" (axis ticks); exact numbers are shown elsewhere
export function compact(n) {
  const v = Math.abs(n)
  if (v >= 1e6) return `${Number((n / 1e6).toFixed(2))}M`
  if (v >= 1e3) return `${Math.round(n / 1e3)}k`
  return String(Math.round(n))
}

// the mode the case is set to, from a forecast answer
export const modeOf = (data, firm) => (firm && data?.cascade?.firm ? data.cascade.firm : data?.cascade?.flexible || null)
