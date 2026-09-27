// The hospital beds agent (backend/hospital_agent.py): how many beds the hospitals in the dark areas have, AS
// REPORTED, each with its source, found by a Gemini + Google Search agent and checked in code.
//
// One job per case, started the moment the store's cascade result arrives (HospitalAgentStarter, mounted once in
// App.jsx), so it is done long before the presentation reaches its hospitals beat. The job's state lives in this
// module (not in a component), so the starter and every HospitalsFound panel share it. Nothing here plays audio.
//
//   POST /api/hospitals/agent {region, load_factor, affected, tab} -> {job, status: running} or a finished answer
//   GET  /api/hospitals/agent/{job}                             -> {status, hospitals, trace, counts, by, ...}
import { useEffect, useSyncExternalStore } from 'react'
import { api } from '../../api'
import { useOverload } from '../../store'

const POLL_MS = 1000
// a 429 (the venue shares one IP), the agent busy, a server restarting or a network blip: wait and try again
const BACKOFF_MS = [1500, 3000, 5000, 8000, 8000, 8000]
const transient = (error) =>
  error instanceof TypeError ||
  /too many requests|busy|try again|failed to fetch|networkerror|load failed|network|request failed \(5\d\d\)/i.test(error?.message || '')

let current = { key: null, status: 'idle', data: null, error: null }
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
// This tab's own random id: the server stops a visitor's previous search when it opens another case, and visitors
// behind one address (the venue shares one IP) must never stop each other's. Not stored anywhere.
const TAB = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 40)

// The case a job belongs to: the state, the load level and which substations lost how much (the hospitals it lands on
// follow from those alone). null when there is nothing to look up.
export function agentKey(region, loadFactor, affected) {
  if (!region || region === 'US' || !affected) return null
  const ids = Object.keys(affected).sort()
  return `${region}|${Number(loadFactor || 1).toFixed(2)}|${ids.map((k) => `${k}:${Number(affected[k]).toFixed(1)}`).join(',')}`
}

// Start (or keep) the job for this case. Calling it again for the same case does nothing; a new case replaces the old
// job (its polling stops).
export function startHospitalAgent({ region, loadFactor = 1, affected }) {
  const key = agentKey(region, loadFactor, affected)
  if (!key || (current.key === key && current.status !== 'error')) return
  const id = ++run
  clearTimeout(timer)
  emit({ key, status: 'starting', data: null, error: null })
  const body = { region, load_factor: loadFactor, affected, tab: TAB }
  const land = (data) => emit({ key, status: data.status, data, error: null })
  const fail = (error) => emit({ key, status: 'error', data: current.key === key ? current.data : null, error })
  // tries: failed polls in a row (a success starts over); only after BACKOFF_MS runs out does the panel show an error
  const poll = (job, retried, tries = 0, wait = POLL_MS) => {
    timer = setTimeout(() => {
      if (id !== run) return
      api(`/api/hospitals/agent/${encodeURIComponent(job)}`).then(
        (data) => {
          if (id !== run) return
          land(data)
          if (data.status === 'running') poll(job, retried)
        },
        (error) => {
          if (id !== run) return
          if (!retried && /no longer available/i.test(error?.message || '')) begin(true) // the job expired: once more
          else if (transient(error) && tries < BACKOFF_MS.length) poll(job, retried, tries + 1, BACKOFF_MS[tries])
          else fail(error)
        },
      )
    }, wait)
  }
  const begin = (retried = false, tries = 0) =>
    api('/api/hospitals/agent', { method: 'POST', body }).then(
      (data) => {
        if (id !== run) return
        land(data)
        if (data.status === 'running' && data.job) poll(data.job, retried)
      },
      (error) => {
        if (id !== run) return
        if (transient(error) && tries < 3) {
          clearTimeout(timer)
          timer = setTimeout(() => id === run && begin(retried, tries + 1), BACKOFF_MS[tries])
        } else fail(error)
      },
    )
  begin()
}

// Mounted once (HospitalAgentStarter): the job starts when a cascade lands, not when the presentation opens.
export function useHospitalAgentStarter() {
  const o = useOverload()
  const cascade = o?.cascade
  const region = o?.region
  const loadFactor = o?.loadFactor ?? 1
  useEffect(() => {
    if (!cascade?.affected || !region || region === 'US') return
    startHospitalAgent({ region, loadFactor, affected: cascade.affected })
  }, [cascade, region, loadFactor])
}

// The job for the case on screen: {status: 'none' | 'idle' | 'starting' | 'running' | 'done' | 'error', hospitals,
// trace, totals (the backend's counts), data (the whole answer), error, retry}. A job for another case reads as idle.
export function useHospitalAgent() {
  const o = useOverload()
  const snap = useSyncExternalStore(subscribe, () => current)
  const affected = o?.cascade?.affected
  const key = agentKey(o?.region, o?.loadFactor ?? 1, affected)
  const mine = !!key && snap.key === key
  const data = mine ? snap.data : null
  return {
    status: !key ? 'none' : mine ? snap.status : 'idle',
    hospitals: data?.hospitals || [],
    trace: data?.trace || [],
    totals: data?.counts || null,
    data,
    error: mine ? snap.error : null,
    retry: () => key && startHospitalAgent({ region: o.region, loadFactor: o.loadFactor ?? 1, affected }),
  }
}
