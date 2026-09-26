import { useSyncExternalStore } from 'react'
import { defaultBudget, stepsWithin } from './budget'
import { DEFAULT_SIZE, getUnlockJob, peekUnlock, startUnlock } from './unlockApi'

// State the Strengthen page and its map layer share: the study (a background job on the backend), the budget
// (how much of the plan is bought), the build-up on screen (how many plan steps are shown), and what is selected.
// It lives in this folder so the feature stays self-contained; the region and load level come from useOverload().
//
// openStudy() shows what the backend already has (a finished or running study) without starting one; in Florida
// (warmed at startup, LAZY allows it) it starts one when there is none, elsewhere the page offers a button.

const POLL_MS = 700
const MAX_FAILS = 6 // transient poll errors tolerated in a row
const SLOW_STEPS = 6 // the build-up's first steps (the biggest blackouts) play slowly, the rest quicker
const SLOW_MS = 850
const TAIL_MS = 5000 // the rest of the build-up takes about this long in all

let state = {
  size: DEFAULT_SIZE, // MW: the campus the study makes room for
  status: 'idle', // idle | peeking | cta (not run yet: the page offers a button) | starting | queued | running | done | error
  key: null, // region|size|load of the study on screen (keyOf)
  region: null,
  loadFactor: 1,
  jobId: null,
  progress: null, // {phase, done, total, message}
  partial: null, // {sites, points} while it runs
  result: null,
  error: null,
  estimate: null, // {seconds, sites}: how long a run should take (from /api/unlock/peek)
  startedAt: null, // Date.now() when this page first saw the study running
  budget: null, // dollars (high end); null = the study's default (budget.js)
  shown: 0, // plan steps shown on the map, the table and the chart (the build-up)
  playing: false,
  selected: null, // {type: 'step' | 'point' | 'site' | 'bundle', id}
  bundle: null, // a Gemini bundle shown on the map instead of the plan (its index), or null
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
const played = new Set() // studies whose build-up already played once on open

export const keyOf = (region, size, loadFactor) => `${region}|${size}|${Number(loadFactor).toFixed(2)}`
export const budgetOf = (s) => (s.budget != null ? s.budget : defaultBudget(s.result))
export const targetOf = (s) => (s.result ? stepsWithin(s.result, budgetOf(s)) : 0)

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

const fresh = (region, loadFactor, extra) => ({
  key: keyOf(region, state.size, loadFactor),
  region,
  loadFactor,
  jobId: null,
  progress: null,
  partial: null,
  result: null,
  error: null,
  startedAt: null,
  shown: 0,
  selected: null,
  bundle: null,
  ...extra,
})

// Show the study for (region, the size picked, load level): at once when the backend has it or is computing it;
// else start it when `auto` (Florida), or offer the button (status 'cta'). `force` re-checks the same study.
export async function openStudy({ region, loadFactor, auto = false, force = false }) {
  const key = keyOf(region, state.size, loadFactor)
  if (!force && state.key === key && state.status !== 'error' && state.status !== 'idle') {
    refreshAi(region, loadFactor)
    return
  }
  stopPlay()
  clearTimeout(pollTimer)
  const id = ++runId
  set(fresh(region, loadFactor, { status: 'peeking', estimate: null }))
  try {
    const p = await peekUnlock({ region, mw: state.size, loadFactor })
    if (id !== runId) return
    set({ estimate: { seconds: p.estimate_s, sites: p.sites } })
    if (p.id) {
      set({ jobId: p.id, status: p.state === 'done' ? 'running' : p.state, startedAt: Date.now() })
      poll(p.id, id, 0)
    } else if (auto) {
      runStudy({ region, loadFactor })
    } else {
      set({ status: 'cta' })
    }
  } catch (error) {
    if (id === runId) set({ status: 'error', error })
  }
}

// The study on screen was shown while Gemini was unavailable; the server retries that step for the warm study.
// Coming back to the page picks up Gemini's verified bundles when they are in (the plan and the build-up stay).
const AI_REFRESH_MS = 30000
let aiCheckedAt = 0
async function refreshAi(region, loadFactor) {
  const ai = state.result?.ai?.status
  if (state.status !== 'done' || (ai !== 'offline' && ai !== 'error') || Date.now() - aiCheckedAt < AI_REFRESH_MS) return
  aiCheckedAt = Date.now()
  const key = state.key
  try {
    const p = await peekUnlock({ region, mw: state.size, loadFactor })
    if (p.state !== 'done' || !p.id) return
    const s = await getUnlockJob(p.id)
    const got = s.result?.ai?.status
    if (state.key === key && state.status === 'done' && got && got !== 'offline' && got !== 'error') set({ result: s.result })
  } catch {
    // the answer on screen stands
  }
}

export async function runStudy({ region, loadFactor }) {
  stopPlay()
  clearTimeout(pollTimer)
  const id = ++runId
  set(
    fresh(region, loadFactor, {
      status: 'starting',
      startedAt: Date.now(),
      progress: { phase: 'queued', done: 0, total: 0, message: 'Starting the study' },
    }),
  )
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
      finish(s)
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

// A finished study lands at the budget's answer; the first time it is shown the build-up plays once from zero.
function finish(s) {
  const result = s.result
  set({ status: 'done', progress: s.progress, result, partial: null })
  const target = targetOf(state)
  set({ shown: target })
  if (!played.has(state.key) && target > 0 && !reducedMotion()) {
    played.add(state.key)
    startPlay(true)
  } else played.add(state.key)
}

// ------------------------------------------------------------------ the budget and the build-up
export function setBudget(dollars) {
  stopPlay()
  set((s) => ({ budget: Math.max(0, dollars), bundle: null, shown: s.result ? stepsWithin(s.result, Math.max(0, dollars)) : 0 }))
}

const delayFor = (at, n) => (at < SLOW_STEPS ? SLOW_MS : Math.max(60, Math.min(450, TAIL_MS / Math.max(1, n - SLOW_STEPS))))

// Build the plan up on the map, one step at a time, to the budget's last step (from zero, or from where it paused).
export function startPlay(fromStart = false) {
  const n = targetOf(state)
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
  playTimer = setTimeout(tick, at === 0 ? 450 : delayFor(at, n))
}

export function stopPlay() {
  clearTimeout(playTimer)
  playTimer = null
  if (state.playing) set({ playing: false })
}

// Move the build-up by hand (0 .. the budget's last step).
export function scrub(n) {
  stopPlay()
  set((s) => ({ shown: Math.max(0, Math.min(targetOf(s), Math.round(n))), bundle: null }))
}

// Show n steps whatever the budget (the chart and the old panel pick a step directly).
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

// The plan step that first raises a line or transformer (a weak point's fix), or null.
export function stepFixing(result, branchId) {
  return result?.steps.find((st) => st.projects.some((p) => p.branch_id === branchId)) || null
}

// "2.4M", "91k", "640"
export function compact(n) {
  const v = Number(n) || 0
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1).replace(/\.0$/, '')}M`
  if (v >= 1e4) return `${Math.round(v / 1e3)}k`
  if (v >= 1e3) return `${(v / 1e3).toFixed(1).replace(/\.0$/, '')}k`
  return String(Math.round(v))
}
