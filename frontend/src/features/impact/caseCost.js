import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react'
import { getCost, getQuickCost } from '../cost/costApi'
import { useOverload } from '../../store'

// What the case on screen costs, fetched ONCE and shared: the cost block under the counter (features/cost/
// OutageCost), the counter's money, the towns feed, the map's hit labels and "Where the people are" all read
// this one answer (backend/costs.py), so they can never disagree and nothing asks the server twice.
//
// Florida (the demo state) is eager: the whole case is priced as soon as it settles (POST /api/cost, cached
// on the server). Every other state is lazy (user, Sat 06:24): nothing is asked before a cascade exists, and
// then only the arithmetic on that cascade's own lost load and people (POST /api/cost/quick, no engine run),
// requested the moment the cascade result arrives so the money can leap with the replay.

let snap = { key: '', status: 'idle', det: null }
const subs = new Set()
let seq = 0
const emit = (next) => {
  snap = { ...snap, ...next }
  subs.forEach((f) => f())
}
const subscribe = (f) => {
  subs.add(f)
  return () => subs.delete(f)
}
const read = () => snap

function load(key, lazy, force = false) {
  if (!force && key === snap.key && snap.status !== 'error') return // already asked (every reader calls this)
  const id = ++seq
  if (!key) {
    emit({ key, status: 'idle', det: null })
    return
  }
  emit({ key, status: 'loading' }) // the last answer stays on screen, marked stale, until this one lands
  ;(lazy ? getQuickCost(JSON.parse(key)) : getCost(JSON.parse(key)))
    .then((det) => id === seq && emit({ det, status: 'done' }))
    .catch(() => id === seq && emit({ status: 'error' }))
}

/** The case's cost estimate: {det, status: idle | loading | done | error, fresh (det belongs to this case),
 *  lazy, hasCase, retry}. */
export function useCaseCost() {
  const { caseBody, region, grid, site, extraSites, trip, loadFactor, cascade } = useOverload()
  const s = useSyncExternalStore(subscribe, read, read)
  const lazy = region !== 'FL'
  const ready = region !== 'US' && (grid?.meta?.region || 'FL') === region
  const hasCase = ready && !!(site || extraSites.length || trip.length || loadFactor !== 1.0)
  const key = !hasCase
    ? ''
    : lazy
      ? cascade
        ? JSON.stringify({ region, lost_mw: cascade.lost_mw ?? 0, people: cascade.people ?? 0 })
        : ''
      : JSON.stringify(caseBody)

  useEffect(() => {
    // eager cases wait for the size slider to settle; a cascade's own numbers are asked for at once
    const t = setTimeout(() => load(key, lazy), lazy || !key ? 0 : 250)
    return () => clearTimeout(t)
  }, [key, lazy])
  const retry = useCallback(() => load(key, lazy, true), [key, lazy])

  const mine = s.key === key
  return {
    key,
    lazy,
    hasCase,
    det: key ? s.det : null,
    status: !key ? 'idle' : mine ? s.status : 'loading',
    fresh: !!key && mine && s.status === 'done' && !!s.det,
    retry,
  }
}

/** Where the whole cascade ends, as the counter shows it at the last step (store view.peopleHit). */
export function finalHit(cascade) {
  const last = cascade?.steps?.at(-1)
  const a = last ? (last.people_hit ?? last.people_zone ?? last.people ?? 0) : 0
  return Math.max(a, cascade?.people_hit ?? cascade?.people_zone ?? cascade?.people ?? 0)
}

/**
 * Dollars per person, from the case's own blackout estimate (the high end, with its low end), so the money
 * beside every people count adds up to the cost panel's figure:
 *   with a cascade on screen: the blackout cost / everyone the cascade hits by the end (the final money equals
 *     the cost panel's estimate; each town's share is its people x the same rate);
 *   before a run (Florida only, where the case is priced eagerly): the blackout cost / the people its cascade
 *     hits when /api/cost returns that count (people_hit), else / the people who lose power.
 * → {high, low, basis: 'hit' | 'dark', total: {high, low}} or null (no blackout, or not priced yet).
 */
export function useLossRate() {
  const { cascade } = useOverload()
  const { det, fresh } = useCaseCost()
  return useMemo(() => {
    const h = det?.headline
    if (!fresh || !h || h.kind !== 'blackout' || !(h.cost_high > 0)) return null
    // before a run: the people the case's own cascade hits when the estimate carries it (the same count the
    // replay ends on, so the rate never changes when the run starts), else the people who lose power
    const people = cascade ? finalHit(cascade) : det.people_hit || h.people_hit || h.people || det.people || 0
    if (!(people > 0)) return null
    const basis = cascade || det.people_hit || h.people_hit ? 'hit' : 'dark'
    return { high: h.cost_high / people, low: (h.cost_low || 0) / people, basis, total: { high: h.cost_high, low: h.cost_low || 0 } }
  }, [det, fresh, cascade])
}
