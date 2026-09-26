import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { fmt } from '../../geo'
import { HOMES_PER_MW, townOf, useOverload } from '../../store'

// Who loses power, by town: the store's view.affected (sub id -> MW of existing load lost so far)
// grouped by the town each synthetic substation is named after ("NAPLES 12" -> "Naples").
// Homes are an estimate: MW x HOMES_PER_MW, the same constant as the counter.

// The 1-based cascade step at which each substation first lost load (step i shows steps[i - 1]).
// A storm's own step (n = 0) is already on screen before step 1 when the what-if solved the same
// knocked-out lines, so those substations count as step 0 — they don't arrive twice.
function arrivals(cascade, before) {
  const at = new Map()
  cascade?.steps.forEach((st, j) =>
    st.newly_affected.forEach(([id]) => {
      if (!at.has(id)) at.set(id, j === 0 && st.n === 0 && before?.[id] !== undefined ? 0 : j + 1)
    }),
  )
  return at
}

// [{name, homes, mw, subs, lat, lon, arrival, latest}] sorted by homes, most first.
// arrival: the step the town first lost power (0 = before the cascade / unknown);
// latest: the step one of its substations last joined (the town grew then).
export function groupTowns(affected, subById, arrival) {
  if (!affected?.size) return []
  const byTown = new Map()
  affected.forEach((mw, id) => {
    const s = subById.get(id)
    if (!s || !(mw > 0)) return
    const name = townOf(s.name)
    let t = byTown.get(name)
    if (!t) {
      t = { name, mw: 0, subs: [], lat: 0, lon: 0, arrival: Infinity, latest: 0 }
      byTown.set(name, t)
    }
    const at = arrival.get(id) ?? 0
    t.mw += mw
    t.subs.push(id)
    t.lat += s.lat * mw // MW-weighted: the label sits where the darkness is
    t.lon += s.lon * mw
    t.arrival = Math.min(t.arrival, at)
    t.latest = Math.max(t.latest, at)
  })
  return [...byTown.values()]
    .map((t) => ({ ...t, lat: t.lat / t.mw, lon: t.lon / t.mw, homes: Math.round(t.mw * HOMES_PER_MW) }))
    .sort((a, b) => b.homes - a.homes || a.name.localeCompare(b.name))
}

export function useTowns() {
  const { view, cascade, result, subById } = useOverload()
  const before = result?.affected
  const arrival = useMemo(() => arrivals(cascade, before), [cascade, before])
  const affected = view?.affected
  return useMemo(() => groupTowns(affected, subById, arrival), [affected, subById, arrival])
}

// Towns a step up to now announced as losing power that have it back at this step, most homes
// first. It happens at the end of a cascade: once a split cuts the data center off, a region
// that was short of power no longer is. (Earlier steps only add towns: the store accumulates.)
export function useRestored(towns) {
  const { cascade, step, subById } = useOverload()
  return useMemo(() => {
    if (!cascade || step <= 0) return []
    const now = new Set(towns.map((t) => t.name))
    const peak = new Map()
    cascade.steps.slice(0, step).forEach((st) =>
      st.newly_affected.forEach(([id, mw]) => {
        const s = subById.get(id)
        const name = s && townOf(s.name)
        if (name && !now.has(name)) peak.set(name, (peak.get(name) || 0) + mw)
      }),
    )
    return [...peak.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([name]) => name)
  }, [cascade, step, subById, towns])
}

// "A", "A and B", "A, B and 4 more towns"
export function nameList(names, max = 2) {
  if (names.length <= max) return names.length === 2 ? `${names[0]} and ${names[1]}` : names.join('')
  const rest = names.length - max
  return `${names.slice(0, max).join(', ')} and ${rest} more ${rest === 1 ? 'town' : 'towns'}`
}

// An estimate reads like one: nearest thousand from 10,000 up, nearest hundred from 1,000, else ten.
export function roundHomes(h) {
  if (h >= 10000) return Math.round(h / 1000) * 1000
  if (h >= 1000) return Math.round(h / 100) * 100
  return Math.max(10, Math.round(h / 10) * 10)
}

export const homesText = (h) => `${fmt(roundHomes(h))} homes`

// ------------------------------------------------------------------ motion helpers
const REDUCE = '(prefers-reduced-motion: reduce)'
function subscribeMotion(cb) {
  const m = window.matchMedia?.(REDUCE)
  m?.addEventListener?.('change', cb)
  return () => m?.removeEventListener?.('change', cb)
}
export const useReducedMotion = () =>
  useSyncExternalStore(
    subscribeMotion,
    () => !!window.matchMedia?.(REDUCE).matches,
    () => false,
  )

// True until `ms` after the cascade step last changed: while it's true the step's new towns ride
// at the top of the feed, flashing; then they settle into their place by homes.
export function useFreshStep(cascade, step, ms) {
  const [settled, setSettled] = useState(null)
  useEffect(() => {
    const t = setTimeout(() => setSettled({ cascade, step }), ms)
    return () => clearTimeout(t)
  }, [cascade, step, ms])
  return !(settled && settled.cascade === cascade && settled.step === step)
}

// FLIP: rows that change place glide there instead of jumping. Reads each child's layout position
// (offsetTop, unaffected by transforms) after every render and animates the difference away.
// Children need a data-key; the list needs position: relative.
export function useFlip(listRef, disabled) {
  const prev = useRef(new Map())
  useLayoutEffect(() => {
    const list = listRef.current
    const next = new Map()
    if (list) {
      for (const el of list.children) {
        const key = el.dataset.key
        const top = el.offsetTop
        next.set(key, top)
        const before = prev.current.get(key)
        if (!disabled && before !== undefined && before !== top && el.animate) {
          el.animate([{ transform: `translateY(${before - top}px)` }, { transform: 'translateY(0)' }], {
            duration: 380,
            easing: 'cubic-bezier(0.2, 0.7, 0.2, 1)',
          })
        }
      }
    }
    prev.current = next
  })
}

// ------------------------------------------------------------------ text measuring (map labels)
let ctx2d
// Width in CSS px of `text` at `px` in the app font (weight, and a canvas font-stretch keyword such
// as 'condensed' = 75 %); a per-character estimate when there's no canvas.
export function textWidth(text, px, weight = 400, stretch = 'normal') {
  if (ctx2d === undefined) {
    try {
      ctx2d = document.createElement('canvas').getContext('2d') || null
    } catch {
      ctx2d = null
    }
  }
  if (!ctx2d) return text.length * px * (stretch === 'normal' ? 0.56 : 0.46)
  ctx2d.font = `${weight} ${px}px "Archivo Variable", system-ui, sans-serif`
  if ('fontStretch' in ctx2d) ctx2d.fontStretch = stretch
  const w = ctx2d.measureText(text).width
  // a canvas without fontStretch measured the normal width; condensed is about 0.82 of it
  return 'fontStretch' in ctx2d || stretch === 'normal' ? w : w * 0.82
}
