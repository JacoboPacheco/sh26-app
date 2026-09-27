import { useSyncExternalStore } from 'react'

// The opening moment's phase, shared by its two halves: Intro (mounted by App: timers, skip, the
// prompt beside the campus card) and IntroCurtain (drawn by FlowCanvas inside the map, right above
// the flow canvas and below the map's own controls). 'gate' | 'wait' | 'play' | 'out' | 'off'.
// 'gate' is the quiet "Click anywhere to begin" wait on the bare home page: the map sits under a static veil,
// dimmed but alive; the opening that follows the click keeps that veil (`soft`) so nothing flashes dark.
let phase = 'off'
let soft = false
const listeners = new Set()

export function setIntroPhase(next) {
  if (next === phase) return
  if (next === 'gate') soft = true
  phase = next
  listeners.forEach((fn) => fn())
}

function subscribe(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function useIntroPhase() {
  return useSyncExternalStore(subscribe, () => phase, () => 'off')
}

/** true when the opening follows a gate: its veil starts dim instead of black */
export function useIntroSoft() {
  return useSyncExternalStore(subscribe, () => soft, () => false)
}
