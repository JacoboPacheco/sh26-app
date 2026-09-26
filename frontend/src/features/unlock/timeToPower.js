// Time to power on the capacity meter (backend/leadtimes.py): roughly when each campus could connect, as typical ranges
// with sources. The page fetches it once a study is on screen (the route reads the cached study only: LAZY) and keeps
// it per study; a study without it (not cached any more, no capacity plan, the request failed) simply shows no row.
import { useEffect, useState } from 'react'
import { getTimeToPower } from './unlockApi'

const cache = new Map() // key -> response
const inflight = new Map() // key -> the request on its way (a result that changes meanwhile never asks twice)
const failed = new Map() // key -> when it last failed: the study polls re-render often, so a 404/409 isn't re-asked on each
const RETRY_MS = 60_000 // a later visit (or a minute later) asks again

// one key per study AND per Gemini verdict (its plan lands after the study, and the route times it too)
const keyOf = (r) => (r?.capacity ? `${r.region}|${r.mw}|${Number(r.load_factor).toFixed(2)}|${r.capacity.ai?.status || ''}` : null)

const recentlyFailed = (key) => Date.now() - (failed.get(key) ?? -Infinity) < RETRY_MS

function load(key, r) {
  if (!inflight.has(key)) {
    const p = getTimeToPower({ region: r.region, mw: r.mw, loadFactor: r.load_factor })
      .then((data) => {
        cache.set(key, data)
        failed.delete(key)
        return data
      })
      .catch(() => {
        failed.set(key, Date.now()) // no row
        return null
      })
      .finally(() => inflight.delete(key))
    inflight.set(key, p)
  }
  return inflight.get(key)
}

export function useTimeToPower(r) {
  const key = keyOf(r)
  const [got, setGot] = useState(null)
  useEffect(() => {
    if (!key || cache.has(key) || recentlyFailed(key)) return undefined
    let live = true
    load(key, r).then((data) => {
      if (live) setGot({ key, data })
    })
    return () => {
      live = false
    }
  }, [key, r])
  if (!key) return null
  if (cache.has(key)) return cache.get(key)
  return got?.key === key ? got.data : null
}

// the plan on screen: 'firm', 'flexible' or Gemini's ('ai'); null when its count doesn't match the meter's
export function ttpPlan(t, which, n) {
  const p = t?.[which]
  return p?.campuses?.length === n ? p.campuses : null
}

// "2028", "2028–30", "2029–31+" (the meter's tick); the card writes the years out
export function yearsShort(c) {
  const a = c.from_year
  const b = c.to_year
  const tail = c.plus ? '+' : ''
  if (a === b) return `~${a}${tail}`
  return `${a}–${Math.floor(a / 100) === Math.floor(b / 100) ? String(b).slice(2) : b}${tail}`
}

// "about 2028", "about 2028 to 2030", "about 2029 to 2031 or later"
export function yearsLong(c) {
  const tail = c.plus ? ' or later' : ''
  return c.from_year === c.to_year ? `about ${c.from_year}${tail}` : `about ${c.from_year} to ${c.to_year}${tail}`
}

// "1.5–2 years", "5–15 years", "3–5+ years"
export function spanWords(it) {
  const f = (x) => x.toLocaleString('en-US', { maximumFractionDigits: 1 })
  return `${f(it.lo)}–${f(it.hi)}${it.plus ? '+' : ''} years`
}

// The width of a line of the meter's time-to-power row (0.7rem, semi-bold), measured with the page's font; a rough
// per-character guess where canvas text isn't available.
let ctx2d = null
export function textWidth(text) {
  try {
    if (!ctx2d) ctx2d = document.createElement('canvas').getContext('2d')
    const size = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
    ctx2d.font = `600 ${0.7 * size}px ${getComputedStyle(document.body).fontFamily}`
    return ctx2d.measureText(text).width
  } catch {
    return text.length * 6.2
  }
}

// Consecutive campuses that wait for the same thing: the meter's groups under the cells. `flex` is set when every
// campus of the group would connect sooner as a flexible campus, all at the same time (firm plan only).
export function ttpGroups(campuses, items) {
  const out = []
  for (const c of campuses || []) {
    const last = out.at(-1)
    if (last && last.item === c.item) {
      last.to = c.n
      last.all.push(c)
    } else out.push({ item: c.item, from: c.n, to: c.n, c, all: [c], it: items?.[c.item] })
  }
  for (const g of out) {
    const f = g.all.map((c) => (c.flex_sooner ? c.flex : null))
    g.flex = f.every((x) => x && x.from_year === f[0].from_year && x.to_year === f[0].to_year && x.plus === f[0].plus) ? f[0] : null
  }
  return out
}
