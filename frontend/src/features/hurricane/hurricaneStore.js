import { useSyncExternalStore } from 'react'

// Hurricane mode's own state, shared by the panel (left column) and the layer (inside the map),
// which sit in different parts of the tree. The case itself (the knocked-out lines) still lives
// in the app store as `trip`; this only holds the storm: its path, its size and where it is.
//
//   points     the storm's path [[lon, lat], ...] (empty = no storm yet)
//   draft      the path being dragged right now, or null
//   armed      the drawing tool owns the map pointer
//   radiusKm   every line within this distance of the eye's path goes down
//   presetId   the hypothetical track picked, or null for a hand-drawn one
//   phase      'none' | 'fetching' | 'storm' (the eye is moving) | 'landed' (lines are in the case)
//   hits       the backend's answer for the path, or null
//   stormAt    performance.now() when the eye set off
//   seq        bumped by every landfall and clear; an older landfall still in flight stops
//   presets    GET /api/hurricane/presets, once
export const DEFAULT_RADIUS_KM = 20
export const STORM_MS = 6000 // the eye crossing the whole path: slow enough to watch the lines go down one by one

let state = {
  points: [],
  draft: null,
  armed: false,
  radiusKm: DEFAULT_RADIUS_KM,
  presetId: null,
  phase: 'none',
  hits: null,
  stormAt: 0,
  seq: 0,
  presets: null,
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
// A landfall runs ~3 s after its click; if the panel unmounts meanwhile (a mode switch) its own
// copy would go stale and the cascade would run on an old case (say, without a campus placed
// mid-storm). The layer stays mounted with the map, so this one keeps up.
export const liveOverload = { current: null }

export const reducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
