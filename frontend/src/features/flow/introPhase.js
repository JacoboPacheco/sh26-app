import { useSyncExternalStore } from 'react'

// The opening moment's phase, shared by its two halves: Intro (mounted by App: timers, skip, the
// prompt beside the campus card) and IntroCurtain (drawn by FlowCanvas inside the map, right above
// the flow canvas and below the map's own controls). 'wait' | 'play' | 'out' | 'off'.
let phase = 'off'
const listeners = new Set()

export function setIntroPhase(next) {
  if (next === phase) return
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
