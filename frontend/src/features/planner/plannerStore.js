import { useSyncExternalStore } from 'react'
import { getPlanJob, startPlan } from './plannerApi'

// State the planner panel and its map layer share: the run (its request, the steps received, how
// many are revealed, the result) and whether the plan was loaded into the workspace. It lives here,
// not in the app store, so the feature stays in its folder; the case itself only ever changes
// through useOverload().
//
// Steps are revealed one by one (REVEAL_MS apart) even when they arrive together — the built-in
// planner answers in well under a second, Gemini a step every second or two — so the map and the
// list move together and the verdict comes last.

const POLL_MS = 600
const REVEAL_MS = 650
const MAX_POLL_FAILS = 4

// origin: which panel started the run ('boom' = the AI boom mode, which puts each step's campuses on
// the map); revealMs: the pace for this run; startedAt: performance.now() when it started (the trace's clock)
const IDLE = { status: 'idle', request: null, steps: [], shown: 0, result: null, error: null, loaded: null, runId: 0, origin: null, revealMs: REVEAL_MS, startedAt: 0, endedAt: 0 }
let state = IDLE
const subs = new Set()
function set(next) {
  state = typeof next === 'function' ? next(state) : next
  subs.forEach((f) => f())
  schedule()
}
const subscribe = (f) => {
  subs.add(f)
  return () => subs.delete(f)
}
export const usePlanner = () => useSyncExternalStore(subscribe, () => state)
export const getPlanner = () => state

const reduced = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// reveal the next step after REVEAL_MS (all at once with reduced motion)
let revealTimer = null
function schedule() {
  if (revealTimer || state.shown >= state.steps.length) return
  if (reduced()) {
    state = { ...state, shown: state.steps.length }
    subs.forEach((f) => f())
    return
  }
  revealTimer = setTimeout(
    () => {
      revealTimer = null
      if (state.shown < state.steps.length) set((s) => ({ ...s, shown: s.shown + 1 }))
    },
    state.shown === 0 ? 150 : state.revealMs || REVEAL_MS,
  )
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Start a plan and follow it to the end. A newer run (or reset) makes an older one stop quietly.
// opts: {origin, revealMs} (see IDLE).
export async function runPlan(request, opts = {}) {
  const runId = state.runId + 1
  clearTimeout(revealTimer)
  revealTimer = null
  const now = typeof performance !== 'undefined' ? performance.now() : Date.now()
  set({ ...IDLE, status: 'running', request, runId, origin: opts.origin || null, revealMs: opts.revealMs || REVEAL_MS, startedAt: now })
  let job
  try {
    job = await startPlan(request)
  } catch (error) {
    if (state.runId === runId) set((s) => ({ ...s, status: 'error', error }))
    return
  }
  let fails = 0
  while (state.runId === runId) {
    await sleep(POLL_MS)
    if (state.runId !== runId) return
    let s
    try {
      s = await getPlanJob(job.id)
      fails = 0
    } catch (error) {
      // a lost job (the server restarted) won't come back; a network blip might
      if (/no longer available/i.test(error.message) || ++fails >= MAX_POLL_FAILS) {
        if (state.runId === runId) set((st) => ({ ...st, status: 'error', error }))
        return
      }
      continue
    }
    if (state.runId !== runId) return
    const ended = performance.now()
    if (s.status === 'done') {
      set((st) => ({ ...st, status: 'done', steps: s.result.steps, result: s.result, endedAt: ended }))
      return
    }
    if (s.status === 'error') {
      set((st) => ({ ...st, status: 'error', steps: s.steps, error: new Error(s.error || 'The planner failed'), endedAt: ended }))
      return
    }
    if (s.steps.length !== state.steps.length) set((st) => ({ ...st, steps: s.steps }))
  }
}

// Forget the run (a new region's case, or "Clear").
export function resetPlanner() {
  clearTimeout(revealTimer)
  revealTimer = null
  set((s) => ({ ...IDLE, runId: s.runId + 1 }))
}

export const markLoaded = (kind) => set((s) => ({ ...s, loaded: kind }))

// the result is on screen once every step has been revealed
export const revealed = (s) => s.status === 'done' && s.shown >= s.steps.length

// ------------------------------------------------------------------ the form's draft
// Kept here so the goal and fields survive the panel closing and opening again (a mode switch).
// `picked`: the user chose the state themselves, so the form stops following the map's state.
let draft = { goal: '', region: null, total: '2000', sites: '3', picked: false, read: null }
const draftSubs = new Set()
export function setDraft(patch) {
  draft = { ...draft, ...patch }
  draftSubs.forEach((f) => f())
}
const draftSubscribe = (f) => {
  draftSubs.add(f)
  return () => draftSubs.delete(f)
}
export const useDraft = () => useSyncExternalStore(draftSubscribe, () => draft)
