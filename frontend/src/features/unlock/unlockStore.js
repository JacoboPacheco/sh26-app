import { useSyncExternalStore } from 'react'
import { DEFAULT_SIZE, getUnlockJob, startUnlock } from './unlockApi'

// State the Strengthen panel and its map layer share: the study (a background job on the backend), the
// build-up on screen (how many plan steps are shown), and what is selected. It lives in this folder so the
// feature stays self-contained; the region and load level come from useOverload() at the call.
//
// Nothing runs until runStudy() (a button press): LAZY.

const POLL_MS = 800
const MAX_FAILS = 6 // transient poll errors tolerated in a row
const SLOW_STEPS = 6 // the build-up's first steps (the biggest blackouts) play slowly, the rest quicker
const SLOW_MS = 900
const TAIL_MS = 7000 // the rest of the build-up takes about this long in all

let state = {
  size: DEFAULT_SIZE, // MW: the campus the study makes room for
  status: 'idle', // idle | starting | queued | running | done | error
  region: null,
  loadFactor: 1,
  jobId: null,
  progress: null, // {phase, done, total, message}
  partial: null, // {sites, points} while it runs
  result: null,
  error: null,
  shown: 0, // plan steps shown on the map and the chart (the build-up, or the budget picked on the chart)
  playing: false,
  selected: null, // {type: 'point' | 'site' | 'step' | 'bundle', id}
  bundle: null, // an AI bundle shown on the map instead of the plan (its index), or null
}
const subs = new Set()
const get = () => state
function set(patch) {
  state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) }
  subs.forEach((f) => f())
}
const subscribe = (f) => {
  subs.add(f)
  return () => subs.delete(f)
}
export const useUnlock = () => useSyncExternalStore(subscribe, get)

let runId = 0
let pollTimer = null
let playTimer = null

export const reducedMotion = () => {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

export function setSize(mw) {
  set({ size: mw })
}

export async function runStudy({ region, loadFactor }) {
  stopPlay()
  clearTimeout(pollTimer)
  const id = ++runId
  set({
    status: 'starting',
    region,
    loadFactor,
    jobId: null,
    progress: {
      phase: 'queued',
      done: 0,
      total: 0,
      message: 'Starting the study',
    },
    partial: null,
    result: null,
    error: null,
    shown: 0,
    selected: null,
    bundle: null,
  })
  try {
    const j = await startUnlock({ region, mw: state.size, loadFactor })
    if (id !== runId) return
    set({ jobId: j.id, status: j.status === 'done' ? 'running' : j.status })
    poll(j.id, id, 0)
  } catch (error) {
    if (id === runId) set({ status: 'error', error })
  }
}

async function poll(jobId, id, fails) {
  try {
    const s = await getUnlockJob(jobId)
    if (id !== runId) return
    if (s.status === 'done') {
      set({
        status: 'done',
        progress: s.progress,
        result: s.result,
        partial: null,
        shown: 0,
      })
      startPlay(true)
      return
    }
    if (s.status === 'error') {
      set({ status: 'error', error: new Error(s.error || 'The study failed') })
      return
    }
    set({ status: s.status, progress: s.progress, partial: s.partial })
    fails = 0
  } catch (error) {
    if (id !== runId) return
    if (++fails > MAX_FAILS) {
      set({ status: 'error', error })
      return
    }
  }
  pollTimer = setTimeout(() => poll(jobId, id, fails), POLL_MS)
}

// ------------------------------------------------------------------ the build-up
const delayFor = (at, n) => (at < SLOW_STEPS ? SLOW_MS : Math.max(45, Math.min(450, TAIL_MS / Math.max(1, n - SLOW_STEPS))))

export function startPlay(fromStart = false) {
  const n = state.result?.steps?.length || 0
  stopPlay()
  if (!n) return
  if (reducedMotion()) {
    set({ shown: n, playing: false, bundle: null })
    return
  }
  let at = fromStart || state.shown >= n ? 0 : state.shown
  set({ shown: at, playing: true, bundle: null })
  const tick = () => {
    at += 1
    set({ shown: at })
    if (at >= n) {
      set({ playing: false })
      return
    }
    playTimer = setTimeout(tick, delayFor(at, n))
  }
  playTimer = setTimeout(tick, at === 0 ? 500 : delayFor(at, n))
}

export function stopPlay() {
  clearTimeout(playTimer)
  playTimer = null
  if (state.playing) set({ playing: false })
}

export function setShown(n) {
  stopPlay()
  const max = state.result?.steps?.length || 0
  set({ shown: Math.max(0, Math.min(max, Math.round(n))), bundle: null })
}

export const select = (sel) => set({ selected: sel })
export const showBundle = (i) => {
  stopPlay()
  set((s) => ({ bundle: s.bundle === i ? null : i }))
}

// ------------------------------------------------------------------ derived
// The upgrades on the map: the plan's up to step `shown` (each line at its latest rating), or one AI bundle's.
export function drawnUpgrades(result, shown, bundle) {
  if (!result) return []
  const byId = new Map()
  if (bundle != null && result.ai?.bundles?.[bundle]) {
    for (const p of result.ai.bundles[bundle].projects) byId.set(p.branch_id, { ...p, stepAt: 0 })
  } else {
    for (const st of result.steps.slice(0, shown)) for (const p of st.projects) byId.set(p.branch_id, { ...p, stepAt: st.n })
  }
  return [...byId.values()]
}

// The ratings each needed line has by step n: what "Try it" applies for one unlocked site.
export function upgradesFor(result, site, n) {
  const need = new Set(site.needs || [])
  const out = {}
  for (const st of result.steps.slice(0, n)) for (const p of st.projects) if (need.has(p.branch_id)) out[p.branch_id] = p.rating_after_mva
  return out
}

// The unlocked sites up to step n, biggest blackout prevented first.
export function unlockedUpTo(result, n) {
  const out = []
  for (const st of result.steps.slice(0, n)) for (const s of st.newly) out.push({ ...s, step: st.n })
  return out.sort((a, b) => (b.hit0 || 0) - (a.hit0 || 0))
}

// "2.4M", "91k", "640"
export function compact(n) {
  const v = Number(n) || 0
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1).replace(/\.0$/, '')}M`
  if (v >= 1e4) return `${Math.round(v / 1e3)}k`
  if (v >= 1e3) return `${(v / 1e3).toFixed(1).replace(/\.0$/, '')}k`
  return String(Math.round(v))
}
