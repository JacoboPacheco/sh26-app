import { useSyncExternalStore } from 'react'
import { defaultBudget, stepsWithin } from './budget'
import { capOf, capWithin, defaultCapBudget } from './capacity'
import { DEFAULT_SIZE, getUnlockJob, peekUnlock, startUnlock } from './unlockApi'

// State the Strengthen page and its map layer share: the study (a background job on the backend), what is on
// screen (the capacity view: campuses connected at once, or the site-by-site plan), the capacity budget and its
// build-up (how many campuses the map shows), the site-by-site plan's budget, and what is selected.
// It lives in this folder so the feature stays self-contained; the region and load level come from useOverload().
//
// openStudy() shows what the backend already has (a finished or running study) without starting one; in Florida
// (warmed at startup, LAZY allows it) it starts one when there is none, elsewhere the page offers a button.

const POLL_MS = 700
const MAX_FAILS = 6 // transient poll errors tolerated in a row
const CAP_STEP_MS = 1200 // the build-up: one paid campus (its upgrades drawn, its marker dropped) every 1.2 s
const CAP_FREE_MS = 650 // a campus that fits with the upgrades so far comes quicker
const CAP_LEAD_MS = 700 // before the first one

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
  view: 'capacity', // 'capacity' (campuses at once, the page's answer) | 'sites' (the site-by-site plan, secondary)
  autoSites: false, // the site-by-site view opened by itself (a study without the capacity section)
  flex: false, // the capacity view's campuses: always on (false) or flexible
  capBudget: null, // dollars (high end) for the capacity plan; null = the default (capacity.js)
  capShown: 0, // campuses on the map and lit in the meter (the build-up)
  capPlaying: false,
  budget: null, // dollars (high end) for the site-by-site plan; null = the study's default (budget.js)
  shown: 0, // site-by-site plan steps shown on the map and in its table
  playing: false,
  selected: null, // {type: 'cap' | 'step' | 'point' | 'site' | 'bundle', id}
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
let capTimer = null
const played = new Set() // studies whose build-up already played once on open (once per page load)

export const keyOf = (region, size, loadFactor) => `${region}|${size}|${Number(loadFactor).toFixed(2)}`
export const budgetOf = (s) => (s.budget != null ? s.budget : defaultBudget(s.result))
export const targetOf = (s) => (s.result ? stepsWithin(s.result, budgetOf(s)) : 0)
// the capacity view: the search on screen, its budget and how many campuses that budget connects at once
export const capNow = (s) => capOf(s.result, s.flex)
export const capBudgetOf = (s) => (s.capBudget != null ? s.capBudget : defaultCapBudget(capNow(s)))
export const capTargetOf = (s) => capWithin(capNow(s), capBudgetOf(s))

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
  capBudget: null,
  capShown: 0,
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
  stopCap()
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
  stopCap()
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

// A finished study lands at the budget's answer; the first time it is shown (once per page load) the capacity
// build-up plays by itself, from today's campuses to the budget's last one. A study without the capacity section
// (an old cached result, or its search failed) opens on the site-by-site plan.
function finish(s) {
  const result = s.result
  set({ status: 'done', progress: s.progress, result, partial: null, shown: 0 })
  // no capacity section: the site-by-site plan opens by itself (and closes again for the next study that has one)
  const view = !result?.capacity ? 'sites' : state.autoSites ? 'capacity' : state.view
  set({ shown: targetOf(state), view, autoSites: !result?.capacity })
  const m = capNow(state)
  const n = capTargetOf(state)
  set({ capShown: n })
  const first = !played.has(state.key)
  played.add(state.key)
  if (first && m && n > m.today && !reducedMotion() && state.view === 'capacity') playCap(true)
}

// ------------------------------------------------------------------ the capacity view: budget, type, build-up
export function setView(view) {
  stopPlay()
  stopCap()
  set((s) => ({ view, autoSites: false, selected: null, bundle: null, capShown: capTargetOf(s) }))
}

export function setCapBudget(dollars) {
  stopCap()
  set((s) => {
    const next = { ...s, capBudget: Math.max(0, dollars) }
    return { capBudget: next.capBudget, capShown: capTargetOf(next) }
  })
}

// Always on / flexible: each has its own plan, so the budget goes back to that plan's default.
export function setFlex(flex) {
  stopCap()
  set((s) => {
    const next = { ...s, flex: !!flex, capBudget: null }
    return { flex: next.flex, capBudget: null, capShown: capTargetOf(next), selected: null }
  })
}

// Build the plan up on the map: from today's campuses (or where it paused) to the budget's last one, one campus
// per tick: its upgrades draw in green, its numbered marker drops at its site, its meter cell and plan row light.
export function playCap(fromStart = false) {
  const m = capNow(state)
  const n = capTargetOf(state)
  stopCap()
  if (!m || n <= m.today) {
    set({ capShown: n })
    return
  }
  if (reducedMotion()) {
    set({ capShown: n, capPlaying: false })
    return
  }
  let at = fromStart || state.capShown >= n || state.capShown < m.today ? m.today : state.capShown
  set({ capShown: at, capPlaying: true })
  const delay = (k) => (m.steps[k]?.free ? CAP_FREE_MS : CAP_STEP_MS) // k: the index of the campus about to land
  const tick = () => {
    at += 1
    set({ capShown: at })
    if (at >= n) {
      capTimer = null
      set({ capPlaying: false })
      return
    }
    capTimer = setTimeout(tick, delay(at))
  }
  capTimer = setTimeout(tick, CAP_LEAD_MS)
}

export function stopCap() {
  clearTimeout(capTimer)
  capTimer = null
  if (state.capPlaying) set({ capPlaying: false })
}

// Leaving the page mid build-up: the answer stands complete for the next visit.
export function settleCap() {
  stopCap()
  set((s) => ({ capShown: capTargetOf(s) }))
}

// ------------------------------------------------------------------ the site-by-site plan: its budget
// (secondary: no build-up of its own; the map shows the plan up to the budget while that view is open)
export function setBudget(dollars) {
  stopPlay()
  set((s) => ({ budget: Math.max(0, dollars), bundle: null, shown: s.result ? stepsWithin(s.result, Math.max(0, dollars)) : 0 }))
}

export function stopPlay() {
  clearTimeout(playTimer)
  playTimer = null
  if (state.playing) set({ playing: false })
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
