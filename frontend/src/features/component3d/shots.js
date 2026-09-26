// What the 3D panel shows at each moment of a cascade replay (pure: no React, no drawing).
//
// An *element* is the one thing a cascade step takes out, read from the engine's own data:
//   the step's failed lines (`tripped`), or the line held in by cutting customers (`held_line`,
//   a shed step); several at once (a storm) -> the biggest (highest kV, then most people served)
//   and "+N more".
//   Its loading when it went: the previous step's `hot` list (the engine's loading after the last
//   re-solve), else the what-if's loading (the first step), else the power it `carried` over its
//   rating. What it counts up FROM: its loading one state earlier (the grid alone, before the data
//   center, for the first step). A held line's loading after the shed: this step's `hot`.
// A *shot* is an element placed on the replay clock: it trips at its tier's t0 (the moment the
// map's line snaps, shell/cascadeSchedule.js), its approach runs before that, and it holds until
// the next shot's approach starts.

import { titleCase } from '../../shell/cascadeSchedule'

export const RELAY_PCT = 250 // past this the relay reads the surge as a fault and opens at once (no sag story)
const APPROACH_MAX = 1500 // ms of heating and sagging before a trip, at most
const APPROACH_SHARE = 0.58 // of the time since the last trip
const BURST_MS = 1000 // trips closer than this (a burst of aftershocks): no approach, each shows its own trip until the next
export const END_HOLD = 4200 // after the replay ends, the last element stays this long

const fmt = (n) => Math.round(n).toLocaleString('en-US')

function kmBetween(a, b) {
  const toRad = Math.PI / 180
  const dLat = (b.lat - a.lat) * toRad
  const dLon = (b.lon - a.lon) * toRad * Math.cos(((a.lat + b.lat) / 2) * toRad)
  return 6371 * Math.hypot(dLat, dLon)
}

const hotPct = (st, id) => st?.hot?.find((h) => h.id === id)?.pct

/**
 * The element step `j` (0-based) of `cascade` takes out, or null.
 * `ctx` = {subById, branchById, upgrades?, result?, branchIndex?} (the store's lookups).
 * -> {key, step, id, kind: 'line'|'transformer'|'storm'|'shed', mode: 'thermal'|'relay', kv, name,
 *     km, before, at, after, more, mw, people}
 */
export function elementForStep(cascade, j, ctx) {
  const steps = cascade?.steps || []
  const st = steps[j]
  if (!st) return null
  const { subById, branchById, upgrades, result, branchIndex } = ctx
  const ids = st.tripped?.length ? st.tripped : st.held_line != null ? [st.held_line] : []
  if (!ids.length || !branchById) return null
  const carried = new Map((st.carried || []).map((c) => [c.id, c]))
  const cands = []
  ids.forEach((id) => {
    const b = branchById.get(id)
    if (!b) return
    const c = carried.get(id)
    cands.push({ id, b, kv: Number(b.kv) || 0, people: c?.people ?? 0, mw: c?.mw ?? null })
  })
  if (!cands.length) return null
  cands.sort((p, q) => q.kv - p.kv || q.people - p.people)
  const top = cands[0]
  const b = top.b
  const transformer = b.from_sub === b.to_sub
  const rating = Number(upgrades?.[b.id]) || Number(b.rate_mva) || 0
  const whatif = (id) => {
    const i = branchIndex?.get(id)
    const v = i != null ? result?.loading_pct?.[i] : undefined
    return Number.isFinite(v) ? v : undefined
  }
  const byCarried = top.mw != null && rating > 0 ? (top.mw / rating) * 100 : undefined
  const kind = st.action === 'storm' ? 'storm' : st.action === 'shed' ? 'shed' : transformer ? 'transformer' : 'line'
  let at
  if (kind === 'storm') at = byCarried ?? whatif(b.id) ?? b.base_pct ?? 0
  else at = (j > 0 ? hotPct(steps[j - 1], b.id) : undefined) ?? (j === 0 ? whatif(b.id) : undefined) ?? byCarried ?? b.base_pct ?? 0
  let before = j >= 2 ? (hotPct(steps[j - 2], b.id) ?? Math.min(80, at)) : j === 1 ? (whatif(b.id) ?? b.base_pct) : b.base_pct
  if (!Number.isFinite(before)) before = at * 0.8
  before = Math.max(0, Math.min(before, at))
  const after = kind === 'shed' ? (hotPct(st, b.id) ?? 99.5) : null
  const from = subById?.get(b.from_sub)
  const to = subById?.get(b.to_sub)
  const nameOf = (s, id) => (s ? titleCase(s.name) : `#${id}`)
  return {
    key: `${j}:${b.id}`,
    step: j + 1,
    id: b.id,
    kind,
    transformer,
    mode: at > RELAY_PCT ? 'relay' : 'thermal',
    kv: top.kv,
    name: transformer ? `${nameOf(from, b.from_sub)} transformer` : `${nameOf(from, b.from_sub)} → ${nameOf(to, b.to_sub)}`,
    km: !transformer && from && to ? kmBetween(from, to) : null,
    before,
    at,
    after,
    more: cands.length - 1,
    mw: top.mw,
    people: top.people,
  }
}

/** Every step's element (null where a step takes nothing out). */
export function elementsFor(cascade, ctx) {
  return (cascade?.steps || []).map((_, j) => elementForStep(cascade, j, ctx))
}

/**
 * The shots of a replay: one per tier that takes an element out, on the schedule's clock (ms from
 * the start of playback). -> [{el, t0 (the trip), approach, start, end}]
 */
export function shotsFor(schedule, elements) {
  const list = []
  ;(schedule?.tiers || []).forEach((t) => {
    const el = elements[t.step - 1]
    if (el) list.push({ el, t0: t.t0 })
  })
  list.forEach((s, i) => {
    const prev = list[i - 1]
    const room = prev ? s.t0 - prev.t0 : s.t0
    s.approach = prev && room < BURST_MS ? 0 : Math.max(0, Math.min(APPROACH_MAX, prev ? room * APPROACH_SHARE : room))
    s.start = s.t0 - s.approach
  })
  list.forEach((s, i) => {
    s.end = list[i + 1] ? list[i + 1].start : Infinity
  })
  return list
}

/** The shot on screen `ms` into playback: {i, shot, u (ms from its trip)} or null before the first. */
export function shotAt(shots, ms) {
  if (!shots?.length) return null
  let i = -1
  for (let k = 0; k < shots.length; k++) if (ms >= shots[k].start || k === 0) i = k
  if (i < 0) return null
  const shot = shots[i]
  return { i, shot, u: ms - shot.t0 }
}

// ------------------------------------------------------------------ the words
export function kindLine(el) {
  const kv = `${fmt(el.kv)} kV`
  if (el.transformer) return `${kv} transformer`
  const km = el.km != null ? ` · ${el.km < 10 ? el.km.toFixed(1) : fmt(el.km)} km` : ''
  return `${kv} line${km}`
}

export function whyLine(el) {
  if (el.kind === 'storm')
    return el.kv < 200 ? 'Wind: the storm snaps a pole and the line comes down; the relay opens it.' : 'Wind: the storm drops a tree across the line; the relay opens it.'
  if (el.kind === 'shed') return 'Held on purpose: the operator cuts customers nearby so this line stays in.'
  if (el.transformer)
    return el.mode === 'relay'
      ? 'Far past its rating: the relay reads the surge as a fault and trips it at once.'
      : 'Too hot inside: the relay trips it before the oil and windings are damaged.'
  return el.mode === 'relay'
    ? 'Far past its rating: the relay reads the surge as a fault and opens the line at once.'
    : 'Too hot: the line sags toward the trees below, and the relay opens it in a fraction of a second.'
}

// the 2003 Northeast blackout, where it fits, in one line (U.S.-Canada Power System Outage Task Force final
// report, April 2004: it began with loaded 345 kV lines sagging into overgrown trees; the Sammis-Star line
// then tripped on a zone 3 relay that read the heavy overload as a fault, and the cascade spread)
export const REPORT_URL = 'https://www.energy.gov/sites/prod/files/oeprod/DocumentsandMedia/BlackoutFinal-Web.pdf'
export function precedent(el) {
  if (el.kind !== 'line') return null
  return el.mode === 'relay'
    ? 'Relays did this in the 2003 Northeast blackout.'
    : 'The 2003 Northeast blackout began this way.'
}

export const STATUS_TEXT = {
  rising: '',
  over: 'Over its rating',
  wind: 'Storm',
  tripped: 'Tripped',
  down: 'Down',
  cut: 'Customers cut',
}
