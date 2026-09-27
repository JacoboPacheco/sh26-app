import { SNAP_MS } from '../../shell/cascadeSchedule'

// THE CHAIN IN FOUR PARTS (user, Sat 23:37-23:48): the chain slide is one arc over the whole cascade, in order. The
// backend cuts the cascade's steps into up to four consecutive phases and writes one flowing sentence per phase
// (backend/chain_arc.py: where it begins, where the chains cause damage, the people hit on the way outward, where it
// finally reaches and the result). Nothing is labeled or numbered. This file is the slide's clock and its text:
// pure functions, no React. Everything that moves reads one timeline (the map's replay schedule when the blast is
// running, else a steady clock), so the sentences, the rail, the counter and the map's highlight never disagree.

export const REVEAL_MS = 700 // a sentence writes itself in over this long once its part lands
export const SETTLE_MS = 380 // the counter chases its target with this time constant (a continuous climb, never a step)
const clamp01 = (v) => Math.min(1, Math.max(0, v))

// the deck's arc: [{k, kind, steps, line_ids, areas, people_delta, people_total, text {en, es}, marks {en, es}}], or null
// for a deck without one (an older server, the fixture)
export const arcOf = (slide) => (Array.isArray(slide?.arc) && slide.arc.length ? slide.arc : null)

// where the counter ends: the panel's People hit (the backend's own figure), else the last part's running total
export const finalHit = (slide, arc) => Number(slide?.people?.hit) || Number(arc?.[arc.length - 1]?.people_total) || 0

// the cascade's failure steps (a storm's own step 0 is not one)
export const stepsIn = (arc) => arc.reduce((n, p) => n + p.steps.filter((s) => s > 0).length, 0)

// ---------------------------------------------------------------------------------- the timeline
// per part {t0, a (when its first failure snaps: the sentence starts writing), t1}, the counter's leaps, the whole length.
// From the map's replay schedule (shell/cascadeSchedule.js), matched to the parts by step number.
export function timelineOf(fx, arc) {
  const sch = fx?.schedule
  if (!sch?.tiers?.length) return null
  const scale = sch.scale || 1
  const byN = new Map(sch.tiers.map((t) => [t.n, t]))
  const parts = arc.map((p, i) => {
    const ts = p.steps.map((n) => byN.get(n)).filter(Boolean)
    if (!ts.length) return null
    const t0 = Math.min(...ts.map((t) => t.t0))
    const t1 = Math.max(...ts.map((t) => t.t1))
    return { t0, a: Math.min(t1, t0 + SNAP_MS * scale), t1, i }
  })
  // a part the schedule doesn't know (the replay started later, or another case): it is over when the replay starts
  const known = parts.filter(Boolean)
  if (!known.length) return null
  return {
    parts: parts.map((x) => x || { t0: 0, a: 0, t1: 0 }),
    leaps: sch.leaps || [],
    startHit: sch.start?.hit || 0,
    total: Math.max(sch.total || 0, ...known.map((x) => x.t1)),
    synthetic: false,
  }
}

// without a replay clock (the map isn't playing): parts one after another, PART_MS each
export const PART_MS = 2600
export function syntheticTimeline(arc) {
  const parts = arc.map((_, i) => {
    const t0 = 500 + i * PART_MS
    return { t0, a: t0 + 150, t1: t0 + PART_MS - 200, i }
  })
  return { parts, leaps: null, startHit: 0, total: parts[parts.length - 1].t1 + 300, synthetic: true }
}

// ---------------------------------------------------------------------------------- one moment
// `el`: ms into the replay (Infinity = the finished picture); `cueStep`: the last step the voice has reached (0 = none)
// → {rows [{f (0..1, how far through its steps), r (0..1, how much of its sentence is written in), landed}], active
//    (the part being played, -1 when none), hit (the counter's target), step (failures so far), done}
export function snapshotAt(tl, arc, el, { cueStep = 0, final = 0 } = {}) {
  const done = el >= tl.total
  const lastStep = Math.max(...arc.flatMap((p) => p.steps))
  const rows = arc.map((p, i) => {
    const w = tl.parts[i]
    const first = Math.min(...p.steps.filter((n) => n > 0), Infinity)
    const viaCue = cueStep > 0 && cueStep >= first // the voice has reached this part
    let f = done ? 1 : w.t1 > w.t0 ? clamp01((el - w.t0) / (w.t1 - w.t0)) : el >= w.t1 ? 1 : 0
    if (cueStep > 0 && cueStep >= Math.max(...p.steps)) f = 1 // and moved past it
    const landed = done || el >= w.a || viaCue
    const r = !landed ? 0 : done || (viaCue && el < w.a) ? 1 : clamp01((el - w.a) / REVEAL_MS)
    return { f, r, landed }
  })
  let active = -1
  if (!done) {
    rows.forEach((r, i) => r.landed && r.f < 1 && (active = i))
    if (active < 0) rows.forEach((r, i) => r.landed && (active = i))
  } else if (cueStep > 0 && cueStep < lastStep) active = arc.findIndex((p) => p.steps.includes(cueStep)) // the voice still reading: its part stays lit
  // the counter: the map's own leaps when the blast is running (it lands on each town as the front reaches it), else the parts' running totals
  let hit
  if (done) hit = final || arc[arc.length - 1].people_total
  else if (tl.leaps) {
    hit = tl.startHit
    for (const l of tl.leaps) if (l.t <= el) hit = l.hit
    hit = Math.min(hit, final || Infinity)
  } else {
    hit = 0
    let prev = 0
    arc.forEach((p, i) => {
      hit += (p.people_total - prev) * rows[i].f
      prev = p.people_total
    })
  }
  const step = Math.round(arc.reduce((n, p, i) => n + p.steps.filter((s) => s > 0).length * rows[i].f, 0))
  return { rows, active, hit, step, done }
}

// the picture before anything has happened: no part landed, the counter at zero
export const startSnapshot = (arc) => ({
  rows: arc.map(() => ({ f: 0, r: 0, landed: false })),
  active: -1,
  hit: 0,
  step: 0,
  done: false,
  shown: 0,
})

// the finished picture: everything landed, nothing lit, the counter on its final figure
export function finalSnapshot(arc, final) {
  const tl = syntheticTimeline(arc)
  return { ...snapshotAt(tl, arc, Infinity, { final }), shown: final || arc[arc.length - 1].people_total }
}

// ---------------------------------------------------------------------------------- the words
const esc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const NUMBER = String.raw`\$?\d[\d,.]*(?:\s(?:million|billion|mil millones|millones|mil))?(?:\s(?:megawatts|megavatios))?`

// A sentence as runs: [{t, b}], b = set in heavier weight: the part's names, places and numbers (the backend's `marks`,
// which come from the same facts as the sentence) plus any figure the text carries. Gemini's sentence bolds the same way.
export function runsOf(text, marks = []) {
  const names = [...new Set((marks || []).filter((m) => typeof m === 'string' && m.trim()))].sort((a, b) => b.length - a.length)
  const re = new RegExp([...names.map(esc), NUMBER].join('|'), 'gi')
  const out = []
  let last = 0
  for (const m of text.matchAll(re)) {
    if (m.index > last) out.push({ t: text.slice(last, m.index), b: false })
    out.push({ t: m[0], b: true })
    last = m.index + m[0].length
  }
  if (last < text.length) out.push({ t: text.slice(last), b: false })
  return out
}
