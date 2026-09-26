import { useSyncExternalStore } from 'react'
import { getHardenInfo, pollHarden, startHarden } from './hardenApi'

// "Harden before the storm": the plan (HardenControl, in the hurricane results) and its replay on the map
// (HardenLayer, inside the map) sit in different parts of the tree, so they share this small store.
//
//   status    'idle' | 'running' | 'done' | 'error'
//   budget    the budget picked (one of BUDGETS)
//   progress  the job's {phase: 'singles'|'engine'|'gemini'|'done', done, total} while it runs
//   trace     the agent's rows so far (features/ai/AgentTrace), then the result's
//   result    POST /api/harden/run's result (backend/harden.py): {storm, none, engine, gemini, best, rounds, trace, by, ...}
//   forHits   the hurricane store's `hits` the plan was made for: a new landfall or a cleared storm drops it
//   forCascade the app's cascade the plan was made for: a changed case (a campus moved, the time of day) drops it
//   shown     the plan is drawn on the map
//   which     the plan drawn: 'best' | 'engine' | 'gemini'
//   clock     the replay: {key (restarts the CSS animations), startedAt, pausedAt} (performance.now())
//   info      GET /api/harden/info once: {configured (Gemini plans on this server), ...}; null until it lands
export const BUDGETS = [50e6, 150e6, 500e6]
export const SWEEP_MS = 6000 // the storm crossing the path again (hurricane mode's own pace)
export const AREAS_MS = 1600 // then the towns kept on and the ones still out
const POLL_MS = 800
const POLL_RETRIES = 3 // a dropped poll (a blip, a 429 on the venue's shared IP) is asked again before the run shows an error

const IDLE = { status: 'idle', job: null, progress: null, trace: [], result: null, error: null, forHits: null, forCascade: null, shown: false, which: 'best' }
let state = { ...IDLE, budget: 150e6, seq: 0, clock: { key: 0, startedAt: 0, pausedAt: null }, info: null }
const listeners = new Set()

export const getHarden = () => state
export function setHarden(patch) {
  const next = typeof patch === 'function' ? patch(state) : patch
  state = { ...state, ...next }
  listeners.forEach((l) => l())
}
const subscribe = (l) => {
  listeners.add(l)
  return () => listeners.delete(l)
}
export const useHarden = () => useSyncExternalStore(subscribe, getHarden)

export const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// A new storm, a cleared one, Start over: the plan belongs to the storm it was made for. A run still polling stops.
export function resetHarden() {
  setHarden((s) => ({ ...IDLE, budget: s.budget, seq: s.seq + 1 }))
}

// Whether Gemini plans on this server (the control's label before a run), fetched once.
let infoAsked = false
export function loadHardenInfo() {
  if (infoAsked) return
  infoAsked = true
  getHardenInfo()
    .then((info) => setHarden({ info }))
    .catch(() => {
      infoAsked = false // ask again next time the control mounts
    })
}

async function poll(job) {
  for (let i = 0; ; i++) {
    try {
      return await pollHarden(job)
    } catch (err) {
      if (i >= POLL_RETRIES) throw err
      await sleep(1500 * (i + 1))
    }
  }
}

// Start planning. `body` is POST /api/harden/run's body; `hits` the hurricane store's hits for this storm; `cascade`
// the app's cascade for this case; `onDone(which)` shows the finished plan (the control focuses the map on the storm
// and, on a phone, scrolls the map into view), else it is just drawn.
export async function planHarden(body, hits, cascade, onDone) {
  const seq = state.seq + 1
  setHarden({ ...IDLE, seq, status: 'running', forHits: hits, forCascade: cascade })
  try {
    let r = await startHarden(body)
    while (r.status === 'running') {
      if (state.seq !== seq) return
      setHarden({ job: r.job, progress: r.progress, trace: r.trace || [] })
      await sleep(POLL_MS)
      if (state.seq !== seq) return
      r = await poll(r.job)
    }
    if (state.seq !== seq) return
    if (r.status !== 'done' || !r.result) throw new Error(r.error || 'The planner could not finish this storm. Try again.')
    setHarden({ status: 'done', progress: r.progress, trace: r.result.trace || [], result: r.result })
    if (onDone) onDone('best')
    else showPlan('best')
  } catch (err) {
    if (state.seq === seq) setHarden({ status: 'error', error: err })
  }
}

// Stop following a run (the backend finishes it and caches it: planning again picks it up at once).
export function stopPlanning() {
  setHarden((s) => ({ ...IDLE, budget: s.budget, seq: s.seq + 1 }))
}

// Draw a plan on the map and replay the storm with it (from the top).
export function showPlan(which = state.which) {
  setHarden((s) => ({ shown: true, which, clock: { key: s.clock.key + 1, startedAt: performance.now(), pausedAt: null } }))
}
export function pauseReplay() {
  setHarden((s) => (s.clock.pausedAt != null ? {} : { clock: { ...s.clock, pausedAt: performance.now() } }))
}
export function resumeReplay() {
  setHarden((s) =>
    s.clock.pausedAt == null ? {} : { clock: { ...s.clock, startedAt: s.clock.startedAt + (performance.now() - s.clock.pausedAt), pausedAt: null } },
  )
}
export function closeMap() {
  setHarden({ shown: false })
}

// ms into the replay (stops while paused)
export const replayAt = (clock, now = performance.now()) => Math.max(0, (clock.pausedAt ?? now) - clock.startedAt)
export const replayDone = (clock) => replayAt(clock) >= SWEEP_MS + AREAS_MS

// the plan a view names: 'best' is Gemini's when it won, else the engine's
export function planOf(result, which) {
  if (!result) return null
  if (which === 'gemini') return result.gemini || result.engine
  if (which === 'engine') return result.engine
  return result.best?.by === 'gemini' && result.gemini ? result.gemini : result.engine
}

// 1,234,567 → "1.2 million"; 45,678 → "45,700"; small numbers exact
export function people(n) {
  const v = Math.max(0, Math.round(Number(n) || 0))
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)} million`
  if (v >= 10000) return (Math.round(v / 100) * 100).toLocaleString('en-US')
  return v.toLocaleString('en-US')
}
export function short(n) {
  const v = Math.max(0, Math.round(Number(n) || 0))
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `${Math.round(v / 1e3)}k`
  return String(v)
}
