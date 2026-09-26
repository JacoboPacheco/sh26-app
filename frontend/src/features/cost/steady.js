import { useCaseCost, finalHit, useLossRate } from '../impact/caseCost'
import { useOverload } from '../../store'

// The flip switches the map between two cases in a fraction of a second ("Show it without the fix" / "Run it
// again with the fix"). The shared cost estimate (features/impact/caseCost.js) holds one case at a time and
// takes a moment to answer for the other, so for that moment the panel would show the other case's figures (a
// fixed case's "no outage" under a blackout's toll) or none. These remember the last answers by case: a switch
// back shows its own figures at once. Only answers the server gave for that exact case are ever reused.

const MAX = 8
const NONE = { none: true } // "this case has no blackout rate" (remembered too, so it is not mistaken for unknown)

function remember(map, key, value) {
  map.delete(key)
  map.set(key, value)
  if (map.size > MAX) map.delete(map.keys().next().value)
}

const dets = new Map()
/** useCaseCost(), with the last estimate of this same case standing in while a switch back re-asks. */
export function useSteadyCaseCost() {
  const c = useCaseCost()
  if (!c.key) return c
  if (c.fresh) {
    if (dets.get(c.key) !== c.det) remember(dets, c.key, c.det)
    return c
  }
  const known = dets.get(c.key)
  return known ? { ...c, det: known, status: 'done', fresh: true } : c
}

const rates = new Map()
/** useLossRate(), the same way: the dollars per person of this case (and this cascade) while a switch re-asks. */
export function useSteadyLossRate() {
  const { cascade } = useOverload()
  const { key, fresh } = useCaseCost()
  const live = useLossRate()
  const k = key ? `${key}|${cascade ? finalHit(cascade) : '-'}` : ''
  if (!k) return live
  if (fresh) {
    const v = live || NONE
    if (rates.get(k) !== v && !(v === NONE && rates.get(k) === NONE)) remember(rates, k, v)
    return live
  }
  const known = rates.get(k)
  return known && known !== NONE ? known : live
}
