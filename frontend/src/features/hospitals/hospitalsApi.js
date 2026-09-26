// Hospitals on backup power: data + derived status, shared by HospitalsLayer and HospitalsList.
//
// GET /api/hospitals?region=ST → the state's OpenStreetMap hospitals, each matched by the backend
// to the nearest load-serving substation of the synthetic model ({sub, area, km, sub_load_mw,
// in_model}). The status of each one follows the case on screen: it reads view.affected from the
// store (sub id → MW of existing load lost at the step shown), so the map and the list move with the
// cascade replay. The rule is the backend's (hospitals.py → status_for) and the map's dark light:
//   backup    its substation lost >= backup_share (60 %) of its load at the case's load level
//   strained  it lost some (>= strained_mw, the map's dimmed light): partial outages around it
// At the end of a replay view.affected is the cascade's final `affected`, so the counts equal
// POST /api/hospitals/backup for the same case (backupFor) — checked in the browser test.
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { api } from '../../api'
import { useOverload } from '../../store'

const cache = new Map() // region → Promise<GET /api/hospitals payload>

export function fetchHospitals(region) {
  let p = cache.get(region)
  if (!p) {
    p = api(`/api/hospitals?region=${encodeURIComponent(region)}`)
    p.catch(() => cache.delete(region)) // a failed load can be retried
    cache.set(region, p)
  }
  return p
}

// The backend's own answer for a case (store.caseBody): re-runs the cascade server side.
// {counts: {backup, strained, ok, in_model, outside_model, total}, backup: [...], strained: [...], people, ...}
export const backupFor = (caseBody) => api('/api/hospitals/backup', { method: 'POST', body: caseBody })

// The region's hospitals: {data, error, loading, retry}. Nothing for the national map ('US').
export function useHospitals(region) {
  const [state, setState] = useState({ region: null, data: null, error: null })
  const [attempt, setAttempt] = useState(0)
  const wanted = region && region !== 'US' ? region : null
  useEffect(() => {
    if (!wanted) return undefined
    let live = true
    fetchHospitals(wanted).then(
      (data) => live && setState({ region: wanted, data, error: null }),
      (error) => live && setState({ region: wanted, data: null, error }),
    )
    return () => {
      live = false
    }
  }, [wanted, attempt])
  const retry = useCallback(() => {
    setState({ region: null, data: null, error: null })
    setAttempt((n) => n + 1)
  }, [])
  const mine = wanted && state.region === wanted
  return { data: mine ? state.data : null, error: mine ? state.error : null, loading: !!wanted && !mine, retry }
}

// the order the cascade reached them, then by name — the backend's order (hospitals.py → status_for)
const lower = (s) => s.toLowerCase()
const byArrival = (a, b) => (a.firstStep ?? 0) - (b.firstStep ?? 0) || (lower(a.name) < lower(b.name) ? -1 : lower(a.name) > lower(b.name) ? 1 : 0)

// Every hospital of the region with its status for the case on screen, and the counts.
// ready is false until the hospitals AND the map's grid are this region's.
export function useHospitalStatus() {
  const o = useOverload()
  const region = o?.region
  const gridRegion = o?.grid?.meta?.region || (o?.grid ? 'FL' : null)
  const { data, error, loading, retry } = useHospitals(region)
  const affected = o?.view?.affected
  const loadFactor = o?.loadFactor ?? 1
  const cascade = o?.cascade
  const step = o?.step ?? 0

  const derived = useMemo(() => {
    if (!data || data.region !== region || gridRegion !== region) return null
    // the step at which each substation first lost load (for "since step n")
    const firstStep = new Map()
    cascade?.steps?.forEach((st) => st.newly_affected.forEach(([sid]) => !firstStep.has(sid) && firstStep.set(sid, st.n)))
    const statusOf = (h, lost) => {
      if (lost < data.strained_mw) return 'ok'
      return lost >= data.backup_share * Math.max(h.sub_load_mw * loadFactor, 0.1) ? 'backup' : 'strained'
    }
    const all = data.hospitals.map((h) => {
      if (!h.in_model) return { ...h, status: 'outside', lost: 0, share: 0 }
      const lost = affected?.get(h.sub) || 0
      const status = statusOf(h, lost)
      if (status === 'ok') return { ...h, status, lost: 0, share: 0 }
      const load = Math.max(h.sub_load_mw * loadFactor, 0.1)
      return { ...h, status, lost, share: Math.min(lost / load, 1), firstStep: firstStep.get(h.sub) ?? null }
    })
    // the most hospitals on backup at any step shown so far (the store's per-step rule: each step
    // adds the substations it reached; the last step is the cascade's final state, which can be
    // smaller when power comes back to part of the grid as it splits)
    let peak = 0
    const steps = cascade?.steps || []
    if (step > 0 && steps.length) {
      const inModel = data.hospitals.filter((h) => h.in_model)
      const seen = new Map()
      for (let j = 0; j < Math.min(step, steps.length); j++) {
        steps[j].newly_affected.forEach(([id, lost]) => seen.set(id, lost))
        const final = j === steps.length - 1
        const at = (h) => (final ? Number(cascade.affected[h.sub] ?? cascade.affected[String(h.sub)] ?? 0) : seen.get(h.sub) || 0)
        peak = Math.max(peak, inModel.filter((h) => statusOf(h, at(h)) === 'backup').length)
      }
    }
    const backup = all.filter((h) => h.status === 'backup').sort(byArrival)
    const strained = all.filter((h) => h.status === 'strained').sort(byArrival)
    const inModel = all.filter((h) => h.in_model).length
    return {
      all,
      backup,
      strained,
      peakBackup: Math.max(peak, backup.length), // the counter's rule: never below what's on screen
      counts: {
        backup: backup.length,
        strained: strained.length,
        ok: inModel - backup.length - strained.length,
        in_model: inModel,
        outside_model: all.length - inModel,
        total: all.length,
      },
    }
  }, [data, region, gridRegion, affected, loadFactor, cascade, step])

  return { region, data, error, loading: loading || (!!data && !derived && region !== 'US'), retry, ready: !!derived, ...(derived || {}) }
}

// The hospital picked in the list (its cross gets a ring and its name on the map): {region, id} | null
let picked = null
const listeners = new Set()
export function pickHospital(next) {
  picked = next
  listeners.forEach((f) => f())
}
const subscribe = (f) => {
  listeners.add(f)
  return () => listeners.delete(f)
}
export const usePickedHospital = () => useSyncExternalStore(subscribe, () => picked)

export const plural = (n, one, many) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`
