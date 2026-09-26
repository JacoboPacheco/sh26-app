import { useLayoutEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { fmt } from '../../geo'
import { hitEvents } from '../../shell/cascadeSchedule'
import { townOf, useOverload } from '../../store'

// Who is hit, by town. During a cascade: the engine's hit groups and darkness waves (backend
// powerflow.hits / waves — everyone whose power ran through a failed line or went out, each person
// counted once), grouped by the town each synthetic substation is named after ("NAPLES 12" ->
// "Naples"). Before the cascade (a storm that already cut lines): the what-if's lost load per
// substation x the state's people per MW. People are estimates.

/** Each step's hit events (shell/cascadeSchedule.js hitEvents), for the cascade on screen. */
export function useHitEvents() {
  const { cascade, subById } = useOverload()
  return useMemo(() => hitEvents(cascade, subById), [cascade, subById])
}

/** Hit events (flat, in the order they land) -> towns [{name, people, subs, lat, lon, first, last}]:
 *  first / last = the index of the event that first / last hit the town. Biggest first. */
export function hitTowns(list, subById) {
  const byTown = new Map()
  list.forEach((e, i) => {
    if (!e || !(e.people > 0)) return
    const name = e.area || 'Unnamed area'
    let t = byTown.get(name)
    if (!t) {
      t = { name, people: 0, subs: new Set(), lat: 0, lon: 0, w: 0, first: i, last: i }
      byTown.set(name, t)
    }
    t.people += e.people
    t.last = i
    e.subs.forEach((id) => {
      const s = subById.get(id)
      if (!s || t.subs.has(id)) return
      t.subs.add(id)
      const w = Math.max(s.load_mw || 0, 0.5) // load-weighted: the label sits where the people are
      t.lat += s.lat * w
      t.lon += s.lon * w
      t.w += w
    })
  })
  return [...byTown.values()]
    .filter((t) => t.w > 0)
    .map(({ w, ...t }) => ({ ...t, subs: [...t.subs], lat: t.lat / w, lon: t.lon / w }))
    .sort((a, b) => b.people - a.people || a.name.localeCompare(b.name))
}

/** The what-if's lost load (sub id -> MW) by town, as people (MW x people per MW). Biggest first. */
export function groupTowns(affected, subById, peoplePerMw) {
  if (!affected?.size) return []
  const byTown = new Map()
  affected.forEach((mw, id) => {
    const s = subById.get(id)
    if (!s || !(mw > 0)) return
    const name = townOf(s.name)
    let t = byTown.get(name)
    if (!t) {
      t = { name, mw: 0, subs: [], lat: 0, lon: 0, first: 0, last: 0 }
      byTown.set(name, t)
    }
    t.mw += mw
    t.subs.push(id)
    t.lat += s.lat * mw // MW-weighted: the label sits where the darkness is
    t.lon += s.lon * mw
  })
  return [...byTown.values()]
    .map((t) => ({ ...t, lat: t.lat / t.mw, lon: t.lon / t.mw, people: Math.round(t.mw * peoplePerMw) }))
    .sort((a, b) => b.people - a.people || a.name.localeCompare(b.name))
}

/** The towns at the step on screen (the map labels): hit towns once the cascade is under way,
 *  else the what-if's towns without power. Biggest first; `hit` says which. */
export function useTowns() {
  const { view, cascade, step, subById, peoplePerMw } = useOverload()
  const events = useHitEvents()
  const affected = view?.affected
  return useMemo(() => {
    if (cascade && step > 0) return { hit: true, towns: hitTowns(events.slice(0, step).flat(), subById) }
    return { hit: false, towns: groupTowns(affected, subById, peoplePerMw) }
  }, [cascade, step, events, affected, subById, peoplePerMw])
}

// "A", "A and B", "A, B and 4 more towns"
export function nameList(names, max = 2) {
  if (names.length <= max) return names.length === 2 ? `${names[0]} and ${names[1]}` : names.join('')
  const rest = names.length - max
  return `${names.slice(0, max).join(', ')} and ${rest} more ${rest === 1 ? 'town' : 'towns'}`
}

// An estimate reads like one, on the higher end: up to the next thousand from 10,000, the next
// hundred from 1,000, else the next ten.
export function roundPeople(n) {
  if (n >= 10000) return Math.ceil(n / 1000) * 1000
  if (n >= 1000) return Math.ceil(n / 100) * 100
  return Math.max(10, Math.ceil(n / 10) * 10)
}

export const peopleText = (n, unit = 'people') => `${fmt(roundPeople(n))} ${unit}`

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
