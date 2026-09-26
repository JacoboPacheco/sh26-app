import { useSyncExternalStore } from 'react'
import { defaultBudget, stepsWithin } from './budget'
import { aiCapPlan, capOf, capWithin, defaultCapBudget } from './capacity'
import { DEFAULT_SIZE, getUnlockJob, peekUnlock, startSensitivity, startUnlock } from './unlockApi'

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
  capTalk: false, // the narrated build-up (features/narrate) is on: the presenter's voice moves capShown
  pbp: false, // "Watch it get built": the full-screen play-by-play over the map is open (PlayByPlay.jsx)
  pbpFresh: false, // ... and starts from its intro on the next play (false once paused: play resumes)
  pbpEnd: false, // ... its closing has played: the final frame stands
  pbpBeat: 0, // ... the beat on screen (the script's slide index), for the map layer
  pbpScript: null, // ... the script it plays (packages, weak points), for the map layer
  budget: null, // dollars (high end) for the site-by-site plan; null = the study's default (budget.js)
  shown: 0, // site-by-site plan steps shown on the map and in its table
  playing: false,
  selected: null, // {type: 'cap' | 'step' | 'point' | 'site' | 'bundle', id}
  bundle: null, // a Gemini bundle shown on the map instead of the plan (its index), or null
  gemShow: false, // the capacity view shows Gemini's verified plan (capacity.ai) instead of the engine's
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
// Gemini's verified plan when the page shows it (always-on campuses only: the challenge ran on that plan), else null
export const gemNow = (s) => (s.gemShow && !s.flex ? aiCapPlan(s.result) : null)

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
  gemShow: false,
  ...extra,
})

// Show the study for (region, the size picked, load level): at once when the backend has it or is computing it;
// else start it when `auto` (Florida), or offer the button (status 'cta'). `force` re-checks the same study.
export async function openStudy({ region, loadFactor, auto = false, force = false }) {
  const key = keyOf(region, state.size, loadFactor)
  if (!force && state.key === key && state.status !== 'error' && state.status !== 'idle') {
    refreshAi(region, loadFactor)
    if (capPending(state.result) && !pendTimer) watchPending()
    return
  }
  stopPlay()
  stopCap()
  stopPending()
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
const FELL = new Set(['offline', 'error'])
// how many of the two Gemini steps (the site bundles, the challenge to the capacity plan) fell back
const fellCount = (r) => (FELL.has(r?.ai?.status) ? 1 : 0) + (FELL.has(r?.capacity?.ai?.status) ? 1 : 0)
async function refreshAi(region, loadFactor) {
  if (state.status !== 'done' || !fellCount(state.result) || Date.now() - aiCheckedAt < AI_REFRESH_MS) return
  aiCheckedAt = Date.now()
  const key = state.key
  try {
    const p = await peekUnlock({ region, mw: state.size, loadFactor })
    if (p.state !== 'done' || !p.id) return
    const s = await getUnlockJob(p.id)
    // either step coming back is worth showing (the other may still be unavailable)
    if (state.key === key && state.status === 'done' && s.result && fellCount(s.result) < fellCount(state.result)) {
      set({ result: s.result, jobId: p.id })
      if (capPending(s.result)) watchPending()
    }
  } catch {
    // the answer on screen stands
  }
}

// Three parts of the capacity section never hold the study back: Gemini's challenge to the plan (capacity.ai), the
// single-outage screen (capacity.n1) and the sensitivity cases (capacity.sensitivity: a warm study's, or one the
// viewer asked for). The study arrives with them "pending" and each lands in the same job on the server a few seconds
// later; the page picks each up here as it lands (the plan, its numbers and the build-up on screen stay as they are).
const PENDING_POLL_MS = 1500
const PENDING_MAX_MS = 240000
let pendTimer = null
const pendingCount = (r) => [r?.capacity?.ai, r?.capacity?.n1, r?.capacity?.sensitivity].filter((x) => x?.status === 'pending').length
const capPending = (r) => pendingCount(r) > 0
function stopPending() {
  clearTimeout(pendTimer)
  pendTimer = null
}
function watchPending() {
  stopPending()
  const { key, region, size, loadFactor } = state
  const since = Date.now()
  const tick = async () => {
    pendTimer = null
    if (state.key !== key || state.status !== 'done' || !capPending(state.result) || Date.now() - since > PENDING_MAX_MS) return
    try {
      let s = null
      try {
        s = state.jobId ? await getUnlockJob(state.jobId) : null
      } catch {
        s = null // the job may be gone (a restart, or the server let it go): ask for the study again below
      }
      if (!s || s.status !== 'done') {
        s = null
        const p = await peekUnlock({ region, mw: size, loadFactor })
        if (p.state === 'done' && p.id) {
          s = await getUnlockJob(p.id)
          if (state.key === key) set({ jobId: p.id })
        }
      }
      if (state.key !== key || state.status !== 'done') return
      // a part landed: show it (the rest keep polling)
      if (s?.status === 'done' && s.result && pendingCount(s.result) < pendingCount(state.result)) set({ result: s.result })
      if (!capPending(state.result)) return
    } catch {
      // try again on the next tick
    }
    if (state.key === key && !pendTimer) pendTimer = setTimeout(tick, PENDING_POLL_MS)
  }
  pendTimer = setTimeout(tick, PENDING_POLL_MS)
}

// "How sure is this number?" outside the warm study (LAZY): the viewer asks, the server re-runs the always-on search
// under the other assumptions in the background, and the cases land in the study like the parts above.
const withSens = (s, sensitivity) => ({ result: { ...s.result, capacity: { ...s.result.capacity, sensitivity } } })
export async function requestSensitivity() {
  const { key, result } = state
  if (state.status !== 'done' || !result?.capacity) return
  const was = result.capacity.sensitivity || {}
  set((s) => withSens(s, { ...was, status: 'pending', requested: true }))
  try {
    const p = await startSensitivity({ region: result.region, mw: result.mw, loadFactor: result.load_factor })
    if (state.key !== key || !state.result?.capacity) return
    set((s) => withSens(s, p.sensitivity))
    if (p.status === 'pending' && !pendTimer) watchPending()
  } catch (error) {
    if (state.key === key && state.result?.capacity) set((s) => withSens(s, { ...was, status: 'error', error: error?.message || 'The check did not start' }))
  }
}

export async function runStudy({ region, loadFactor }) {
  stopPlay()
  stopCap()
  stopPending()
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
  if (capPending(result)) watchPending()
}

// ------------------------------------------------------------------ the capacity view: budget, type, build-up
export function setView(view) {
  stopPlay()
  stopCap()
  set((s) => ({ view, autoSites: false, selected: null, bundle: null, gemShow: false, capShown: capTargetOf(s) }))
}

export function setCapBudget(dollars) {
  stopCap()
  set((s) => {
    const next = { ...s, capBudget: Math.max(0, dollars) }
    return { capBudget: next.capBudget, capShown: capTargetOf(next), gemShow: false }
  })
}

// Always on / flexible: each has its own plan, so the budget goes back to that plan's default.
export function setFlex(flex) {
  stopCap()
  set((s) => {
    const next = { ...s, flex: !!flex, capBudget: null }
    return { flex: next.flex, capBudget: null, capShown: capTargetOf(next), selected: null, gemShow: false }
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
  set({ capShown: at, capPlaying: true, gemShow: false })
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
  // every change that stops the build-up (size, type, budget, view, Gemini's plan, state, load level, leaving the
  // page) also closes the play-by-play: it only ever shows the answer on screen
  if (state.capPlaying || state.capTalk || state.pbp) set({ capPlaying: false, capTalk: false, pbp: false, pbpEnd: false })
}

// The narrated play-by-play (features/narrate, PlayByPlay.jsx): the presenter's voice moves the campuses instead of
// the timer while capTalk is on (openPbp / resumePbp below); showCap(n) is each step the voice reaches (the map and the
// scoreboard show the campus being built). stopCap() ends it like the timer, so every change that stops the build-up
// (size, type, budget, view, Gemini's plan, state, load level, leaving the page) stops the voice and closes the mode.
export function showCap(n) {
  set((s) => ({ capShown: Math.max(0, n), capPlaying: s.capTalk }))
}

// ------------------------------------------------------------------ "Watch it get built": the play-by-play
// openPbp() leaves the dashboard for the full-screen play-by-play (PlayByPlay.jsx): the map starts from today's
// campuses and the presenter's voice (or the captions' timer) moves them; with reduced motion nothing plays by itself
// and the viewer steps through the beats. holdPbp() pauses (the mode stays), resumePbp() plays on (or again, after
// the end), endPbp() lands on the final frame (the budget's answer, all of it green), closePbp() goes back to the
// dashboard exactly as it was (the answer standing). Any change that calls stopCap() closes it too.
export function openPbp() {
  clearTimeout(capTimer)
  capTimer = null
  set((s) => ({
    pbp: true,
    pbpFresh: true,
    pbpEnd: false,
    pbpBeat: 0,
    pbpScript: null, // the last run's script must not flash on the map while the dashboard slides away
    view: 'capacity',
    selected: null,
    bundle: null,
    gemShow: false,
    capPlaying: false,
    capTalk: !reducedMotion(),
    capShown: capNow(s)?.today ?? 0,
  }))
}

export const holdPbp = () => set({ capTalk: false, pbpFresh: false })

export function resumePbp() {
  if (!state.pbp) return
  const talk = !reducedMotion() // reduced motion: the viewer steps through the beats, nothing plays by itself
  set((s) => (s.pbpEnd ? { capTalk: talk, pbpEnd: false, pbpFresh: true, capShown: capNow(s)?.today ?? 0 } : { capTalk: talk }))
}

export function endPbp() {
  if (!state.pbp) return
  set((s) => ({ capTalk: false, capPlaying: false, pbpEnd: true, capShown: capTargetOf(s) }))
}

export function closePbp() {
  stopCap()
  set((s) => ({ pbp: false, pbpEnd: false, pbpBeat: 0, pbpScript: null, capShown: capTargetOf(s) }))
}

// the beat on screen and the script it belongs to (the map layer draws from them)
export function setPbpView(script, beat) {
  if (state.pbpScript !== script || state.pbpBeat !== beat) set({ pbpScript: script, pbpBeat: beat })
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
// Show Gemini's verified plan on the meter and the map in place of the engine's (or go back to the engine's)
export function showGemini(on) {
  stopCap()
  set((s) => ({ gemShow: !!on && !!aiCapPlan(s.result), selected: null, capShown: capTargetOf(s) }))
}
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
