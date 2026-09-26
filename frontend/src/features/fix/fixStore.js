import { useEffect, useSyncExternalStore } from 'react'
import { findFix, getBestSites } from './fixApi'

// State the fix panel and the map layer share (the found fix, best sites per size and level, the
// hovered site). It lives here, not in the app store, so this feature stays in its own folder.
// Everything it derives from — the case, the size, the load level — still comes from useOverload().

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

// ------------------------------------------------------------------ the fix for the current case
// A fix belongs to a case without its upgrades: applying or removing upgrades keeps it on screen,
// anything else (a new site, size, level, storm) makes it stale.
export function caseKey(body) {
  const { upgrades: _ignored, ...rest } = body || {}
  return JSON.stringify(rest)
}

const fixStore = createStore({ key: null, status: 'idle', data: null, error: null })
let fixSeq = 0

// Resolves to the fix, or null when it failed or a newer request replaced it.
export async function runFix(body) {
  const key = caseKey(body)
  const id = ++fixSeq
  fixStore.set({ key, status: 'loading', data: null, error: null })
  try {
    const data = await findFix(body)
    if (id !== fixSeq) return null
    fixStore.set({ key, status: 'done', data, error: null })
    return data
  } catch (error) {
    if (id === fixSeq) fixStore.set({ key, status: 'error', data: null, error })
    return null
  }
}

// the fix for this case, or an idle one when the stored fix belongs to another case
export function useFix(body) {
  const f = useStore(fixStore)
  return f.key === caseKey(body) ? f : { key: null, status: 'idle', data: null, error: null }
}

// Are exactly the fix's upgrades applied? (`apply` already includes the case's earlier upgrades.)
export function isApplied(apply, upgrades) {
  if (!apply) return false
  const a = Object.entries(apply)
  const u = upgrades || {}
  return a.length === Object.keys(u).length && a.every(([id, mva]) => Math.abs(Number(u[id]) - mva) < 0.05)
}

// ------------------------------------------------------------------ best sites, cached per size + level
const sitesStore = createStore({})
const sitesKey = (mw, lf, region) => `${region || ''}|${Math.round(mw)}|${Number(lf).toFixed(2)}`

function loadSites(mw, lf, region, force = false) {
  const key = sitesKey(mw, lf, region)
  const cur = sitesStore.get()[key]
  if (cur && !force && cur.status !== 'error') return
  sitesStore.set((m) => ({ ...m, [key]: { status: 'loading', data: null, error: null } }))
  getBestSites(mw, lf, 10, region)
    .then((data) => sitesStore.set((m) => ({ ...m, [key]: { status: 'done', data, error: null } })))
    .catch((error) => sitesStore.set((m) => ({ ...m, [key]: { status: 'error', data: null, error } })))
}

// Best sites for a campus of `mw` at load level `lf` (in `region`, when the app has one); fetched
// once per size, level and region while `enabled`.
export function useBestSites(mw, lf, enabled, region) {
  const all = useStore(sitesStore)
  const key = sitesKey(mw, lf, region)
  useEffect(() => {
    if (!enabled) return undefined
    const cur = sitesStore.get()[key]
    if (cur && cur.status !== 'error') return undefined
    const t = setTimeout(() => loadSites(mw, lf, region), cur ? 0 : 150) // settle a size slider first
    return () => clearTimeout(t)
  }, [enabled, key, mw, lf, region])
  const entry = all[key] || { status: 'loading', data: null, error: null }
  return { ...entry, retry: () => loadSites(mw, lf, region, true) }
}

// The list the map pins show: the sites that fit, else the roomiest towns.
export function shownSites(data) {
  if (!data) return { list: [], fits: true }
  return data.sites.length ? { list: data.sites, fits: true } : { list: data.closest, fits: false }
}

// ------------------------------------------------------------------ the site a pointer or focus is on
const hoverStore = createStore(null)
export const setHoverSite = (sub) => hoverStore.set(sub)
export const useHoverSite = () => useStore(hoverStore)

// ------------------------------------------------------------------ names
// "NORTH FORT MYERS 6" -> "North Fort Myers 6" (the dataset's names are upper case)
export function prettyName(name) {
  return String(name || '')
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}
