// "Let an AI operator try" (backend/grid_operator.py): the same cascade three times on the SYNTHETIC grid model, with no
// operator, with the engine's own operator and with a Gemini operator that acts through engine tools.
//
//   POST /api/operator/run {the case}    -> {job, status: running, progress, runs, trace} or a finished answer (cached)
//   GET  /api/operator/jobs/{job}        -> the same, growing: the engine's toll first, then Gemini's trace live
//
// One job at a time, started by a click (never automatically). The state lives here, not in a component, so it
// survives the results column re-rendering; a case other than the one asked for reads as idle. Nothing plays audio.
import { useSyncExternalStore } from 'react'
import { api } from '../../api'

const POLL_MS = 900
const BACKOFF_MS = [1500, 3000, 5000, 8000]
const transient = (e) => e instanceof TypeError || /too many requests|busy|try again|failed to fetch|networkerror|load failed|network|request failed \(5\d\d\)/i.test(e?.message || '')

let current = { key: null, t0: 0, status: 'idle', data: null, error: null }
const subs = new Set()
const emit = (next) => {
  current = next
  subs.forEach((f) => f())
}
const subscribe = (f) => {
  subs.add(f)
  return () => subs.delete(f)
}
let run = 0
let timer = null

export const operatorKey = (body) => (body ? JSON.stringify(body) : '')

export function startOperator(body) {
  const key = operatorKey(body)
  if (!key) return
  const id = ++run
  const t0 = Date.now() // when the click happened: the card's progress and its "this can take a minute" note run from here
  clearTimeout(timer)
  emit({ key, t0, status: 'running', data: { progress: { phase: 'engine' }, runs: {}, trace: [] }, error: null })
  const land = (d) => emit({ key, t0, status: d.status === 'error' ? 'error' : d.status, data: d, error: d.status === 'error' ? new Error(d.error || 'The operator run failed. Try again.') : null })
  const fail = (error) => emit({ key, t0, status: 'error', data: current.key === key ? current.data : null, error })
  const poll = (job, tries = 0, wait = POLL_MS) => {
    timer = setTimeout(() => {
      if (id !== run) return
      api(`/api/operator/jobs/${encodeURIComponent(job)}`).then(
        (d) => {
          if (id !== run) return
          land(d)
          if (d.status === 'running') poll(job)
        },
        (e) => {
          if (id !== run) return
          if (transient(e) && tries < BACKOFF_MS.length) poll(job, tries + 1, BACKOFF_MS[tries])
          else fail(e)
        },
      )
    }, wait)
  }
  const begin = (tries = 0) =>
    api('/api/operator/run', { method: 'POST', body }).then(
      (d) => {
        if (id !== run) return
        land(d)
        if (d.status === 'running' && d.job) poll(d.job)
      },
      (e) => {
        if (id !== run) return
        if (transient(e) && tries < BACKOFF_MS.length) {
          timer = setTimeout(() => id === run && begin(tries + 1), BACKOFF_MS[tries])
        } else fail(e)
      },
    )
  begin()
}

// The run for this case: {status: 'idle' | 'running' | 'done' | 'error', progress, runs (tolls so far), trace, result, startedAt (ms), error}
export function useOperator(body) {
  const snap = useSyncExternalStore(subscribe, () => current)
  const key = operatorKey(body)
  const mine = !!key && snap.key === key
  const d = mine ? snap.data : null
  return {
    status: mine ? snap.status : 'idle',
    progress: d?.progress || {},
    runs: d?.runs || {},
    trace: d?.trace || [],
    result: d?.result || null,
    startedAt: mine ? snap.t0 : 0,
    error: mine ? snap.error : null,
    start: () => startOperator(body),
  }
}
