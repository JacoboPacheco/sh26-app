import { useSyncExternalStore } from 'react'

// Hurricane mode's own state, shared by the panel (left column) and the layer (inside the map),
// which sit in different parts of the tree. The case itself (the knocked-out lines) still lives
// in the app store as `trip`; this only holds the storm: its path, its category and where it is.
//
//   points     the storm's path [[lon, lat], ...] (empty = no storm yet)
//   draft      the path being dragged right now, or null
//   armed      the drawing tool owns the map pointer
//   category   1-5, the panel's Saffir-Simpson control (backend/hurricane.py CATEGORIES)
//   radiusKm   the storm's reach (km): derived from category unless a preset overrides it
//   landfallKm approximately where along the path the storm weakens from (a preset's own value, or
//              the same early heuristic fraction the backend uses for a hand-drawn path)
//   presetId   the hypothetical track picked, or null for a hand-drawn one
//   phase      'none' | 'fetching' | 'storm' (the eye is moving) | 'landed' (lines are in the case)
//   hits       the backend's answer for the path, or null
//   stormAt    performance.now() when the eye set off
//   seq        bumped by every landfall and clear; an older landfall still in flight stops
//   presets    GET /api/hurricane/presets, once (each preset now carries its own baked `hits` when
//              the bake is fresh, so picking one and making landfall needs no network call at all)
//   categories the same GET's category labels/blurbs, for the panel's control
export const CATEGORY_DEFAULT = 3
// Mirrors backend/hurricane.py's CATEGORY_REACH_KM -- only used before /api/hurricane/presets answers,
// or as the value sent back to the API; keep the two in sync if either changes.
export const CATEGORY_REACH_KM = { 1: 62, 2: 72, 3: 82, 4: 92, 5: 102 }
const FALLBACK_CATEGORIES = {
  1: { label: 'Category 1', blurb: 'sustained winds near 85 mph — roofs and trees take damage' },
  2: { label: 'Category 2', blurb: 'sustained winds near 100 mph — extensive roof and tree damage, power loss' },
  3: { label: 'Category 3', blurb: 'major hurricane — sustained winds near 120 mph, structural damage likely' },
  4: { label: 'Category 4', blurb: 'sustained winds near 145 mph — severe structural damage, long outages' },
  5: { label: 'Category 5', blurb: 'sustained winds near 165 mph — catastrophic, most structures fail' },
}
const LANDFALL_FRAC_DEFAULT = 0.15 // mirrors backend/hurricane.py's LANDFALL_FRAC_DEFAULT
export const LANDFALL_HALF_KM = 160 // mirrors backend's LANDFALL_HALF_KM (visual weakening only)

// The eye crossing the whole path: slow enough to read as a real storm, and long enough that the
// cascade (computed in parallel, see makeLandfall in HurricanePanel) is always ready well before it's
// needed. Position moves at a steady rate (so a knocked-out line's flash delay, timed off the same
// clock, never drifts out of sync); the *look* of easing in and weakening lives in the storm's own
// intensity (fading in on arrival, visibly shrinking after landfall), not in its speed over ground.
export const STORM_MS = 24000

let state = {
  points: [],
  draft: null,
  armed: false,
  category: CATEGORY_DEFAULT,
  radiusKm: CATEGORY_REACH_KM[CATEGORY_DEFAULT],
  landfallKm: null,
  presetId: null,
  phase: 'none',
  hits: null,
  stormAt: 0,
  seq: 0,
  presets: null,
  categories: null,
  presetsError: null,
  error: null,
}
const listeners = new Set()

export const getHurricane = () => state

export function setHurricane(patch) {
  const next = typeof patch === 'function' ? patch(state) : patch
  state = { ...state, ...next }
  listeners.forEach((l) => l())
}

function subscribe(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export const useHurricane = () => useSyncExternalStore(subscribe, getHurricane)

// The app store's latest value (useOverload()), kept fresh by both the panel and the map layer.
// A landfall runs a little after its click; if the panel unmounts meanwhile (a mode switch) its own
// copy would go stale and the cascade would run on an old case (say, without a campus placed
// mid-storm). The layer stays mounted with the map, so this one keeps up.
export const liveOverload = { current: null }

export const reducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export function categoryInfo(categories, cat) {
  return (categories && categories[cat]) || (categories && categories[String(cat)]) || FALLBACK_CATEGORIES[cat]
}

export function landfallKmFor(totalKm, explicit) {
  return explicit != null ? explicit : LANDFALL_FRAC_DEFAULT * totalKm
}

// How much of the storm's peak strength is left at this distance along the track (0..1): full
// strength up to landfall, halving every LANDFALL_HALF_KM past it. Visual only (the backend's own
// wind-field solve is the one that decides which lines actually fail).
export function weakenAt(alongKm, landfallKm) {
  return alongKm <= landfallKm ? 1 : 0.5 ** ((alongKm - landfallKm) / LANDFALL_HALF_KM)
}
