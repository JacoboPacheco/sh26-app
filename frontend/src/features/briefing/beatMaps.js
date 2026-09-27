import { fmt, project } from '../../geo'
import { P, atLevel, flexWhen, joinList, moneyShort } from './showText'
import { SPREAD_MS } from './showDeck'

// What each beat of the presentation draws on the map (MapOverlay's `layer`), from the engine's report and the
// cascade on the map. Pure functions: positions are [lon, lat]; nothing here computes a number the engine didn't.

const mid = (a, z) => [(a[0] + z[0]) / 2, (a[1] + z[1]) / 2]
const dist = (p, q) => {
  const [x1, y1] = project(p[0], p[1])
  const [x2, y2] = project(q[0], q[1])
  return Math.hypot(x2 - x1, y2 - y1)
}

// A branch's place on the map: its midpoint, both ends, and whether it is a transformer (both ends in one substation).
export function branchAt(O, id) {
  const b = O.branchById.get(Number(id))
  const a = b && O.subPos(b.from_sub)
  const z = b && O.subPos(b.to_sub)
  if (!a || !z) return null
  return { a, z, mid: mid(a, z), transformer: b.from_sub === b.to_sub, kv: b.kv }
}

// Where the incident began: the first element to fail, else the campus, else the storm's first cut lines.
export function originOf(O, report) {
  const rc = report?.root_cause
  if (rc?.line?.id != null) {
    const at = branchAt(O, rc.line.id)
    if (at) return at.mid
  }
  const c = O.cascade
  const first = (c?.steps || []).find((st) => (st.tripped || []).length)
  if (report?.case?.sub_lon != null && !(first && first.n === 0)) return [report.case.sub_lon, report.case.sub_lat]
  if (first) {
    const pts = first.tripped.slice(0, 60).map((id) => branchAt(O, id)?.mid).filter(Boolean)
    if (pts.length) return [pts.reduce((n, p) => n + p[0], 0) / pts.length, pts.reduce((n, p) => n + p[1], 0) / pts.length]
  }
  if (report?.case?.sub_lon != null) return [report.case.sub_lon, report.case.sub_lat]
  return null
}

const SPREAD_CURVE = 0.62

// THE TOLL: every substation dark when the cascade settled, each lit up (gone dark) as the front reaching outward from
// where it began passes it. `at(t)` = the share of the people counted in areas the front has reached by time t (the
// counter leaps as it reaches each area; ends at 1).
const MAX_DOTS = 900
export function spreadOf(O, report) {
  const c = O.cascade
  if (!c?.steps?.length) return null
  // each step lists the substations it newly darkened: the blackout at the end is all of them
  const ids = new Set()
  c.steps.forEach((st) => (st.dark_subs || []).forEach((id) => ids.add(id)))
  let pts = [...ids].map((id) => O.subPos(id)).filter(Boolean)
  const origin = originOf(O, report) || pts[0]
  if (!origin || !pts.length) return null
  if (pts.length > MAX_DOTS) {
    const step = pts.length / MAX_DOTS
    pts = Array.from({ length: MAX_DOTS }, (_, i) => pts[Math.floor(i * step)])
  }
  const ds = pts.map((p) => dist(origin, p))
  const maxD = Math.max(1e-6, ...ds)
  const reach = SPREAD_MS * 0.9
  // the front accelerates (present.css rs-front): a place at distance d is reached at (d / maxD) ^ SPREAD_CURVE of the way
  const when = (d) => Math.pow(Math.min(1, d / maxD), SPREAD_CURVE) * reach
  const dots = pts.map((p, i) => [p[0], p[1], when(ds[i])])
  // the areas' people, reached in order of distance
  const areas = (report?.areas || []).filter((a) => Number(a.people) > 0 && Array.isArray(a.center))
  const total = areas.reduce((n, a) => n + Number(a.people), 0)
  const hits = areas.map((a) => ({ t: Math.min(reach, when(dist(origin, a.center))), p: Number(a.people) })).sort((x, y) => x.t - y.t)
  const at = (t) => {
    if (t >= reach) return 1
    if (!total) return Math.min(1, t / reach)
    let n = 0
    for (const h of hits) if (h.t <= t) n += h.p
    return n / total
  }
  const leaps = (t) => hits.filter((h) => h.t <= t).length
  return { origin, dots, ms: SPREAD_MS, extent: [origin, ...pts], at, leaps }
}

// An area's substations (the town's own), as [lon, lat].
export function areaPoints(O, name) {
  const key = String(name).toLowerCase()
  return (O.grid?.subs || []).filter((s) => String(s.area).toLowerCase() === key).map((s) => [s.lon, s.lat])
}

// The camera frame and the outline for an area: its substations; the report's bbox/center when the grid has none.
export function areaShape(O, a) {
  let pts = areaPoints(O, a.area)
  if (!pts.length && a.bbox) pts = [[a.bbox[0], a.bbox[1]], [a.bbox[2], a.bbox[3]]]
  if (!pts.length && a.center) pts = [a.center]
  const center = a.center || (pts.length ? [pts.reduce((n, p) => n + p[0], 0) / pts.length, pts.reduce((n, p) => n + p[1], 0) / pts.length] : null)
  return { pts, center }
}

// THE WEAK POINT: the element, the path any new load at the site takes to it (the lines the engine's upgrade raises,
// the element itself when it is a line), power flowing along it toward the campus.
export function weakOf(O, report, slide) {
  const rc = report?.root_cause || {}
  const wp = slide?.weak_point || {}
  const id = wp.id ?? rc.line?.id
  const at = id != null ? branchAt(O, id) : null
  if (!at) return null
  const site = report?.case?.sub_lon != null ? [report.case.sub_lon, report.case.sub_lat] : null
  const up = (report?.fixes || []).find((f) => f.family === 'upgrade' && f.verdict === 'holds')
  const ids = new Set([...(up?.detail?.list || []).map((x) => x.id), id])
  // and every line into the weak point's substation(s): the paths power takes to it
  const wb = O.branchById.get(Number(id))
  const subs = new Set(wb ? [wb.from_sub, wb.to_sub] : [])
  const flows = O.grid?.branches || []
  const now = O.result?.flow_mw || null
  flows.forEach((b) => {
    if (subs.has(b.from_sub) || subs.has(b.to_sub)) ids.add(b.id)
  })
  const idx = new Map(flows.map((b, i) => [b.id, i]))
  const path = []
  for (const bid of [...ids].slice(0, 16)) {
    const b = branchAt(O, bid)
    if (!b || b.transformer) continue
    const i = idx.get(Number(bid))
    const base = i != null ? Number(flows[i].base_flow) || 0 : 0
    const withLoad = i != null && now ? Number(now[i]) || 0 : base
    // the direction power moves (signed flow: + is from → to); without flows, toward the campus
    const dirOf = (f) => (f ? f > 0 : site ? dist(b.a, site) > dist(b.z, site) : true)
    const seg = (fwd) => (fwd ? { a: b.a, b: b.z } : { a: b.z, b: b.a })
    path.push({ ...seg(dirOf(base)), role: 'base', mw: Math.abs(base) })
    if (Math.abs(withLoad - base) > 15) path.push({ ...seg(dirOf(withLoad - base)), role: 'surge', mw: Math.abs(withLoad - base) })
  }
  return {
    id,
    at: at.mid,
    transformer: at.transformer,
    site,
    path,
    from: wp.pct_without ?? rc.pct_without,
    to: wp.pct_with ?? rc.pct_with,
    share: wp.path_share_pct ?? rc.path_share_pct,
  }
}

// What one priced element says on its pin: "$6.4M–$13.2M" and "Rebuild 6.2 mi of 345 kV line".
export function workText(it, lang) {
  const W = P[lang].work
  if (it.work === 'transformer') return W.transformer(it.old_mva, it.new_mva)
  const mi = it.miles != null ? Number(it.miles).toFixed(1).replace(/\.0$/, '') : '?'
  return (W[it.work] || W.reconductor)(mi, it.kv)
}
export const rangeText = (lo, hi) => (lo && hi && lo < hi ? `${moneyShort(lo)}–${moneyShort(hi)}` : moneyShort(hi || lo))

// The priced elements of an option, placed: where each goes, what it is, its price range; alternating sides so two
// pins close together don't cover each other.
export function pinsOf(O, option, lang, { delay = 0, step = 0 } = {}) {
  const items = option?.cost?.items || []
  const out = []
  const seen = []
  items.slice(0, 6).forEach((it, j) => {
    const b = branchAt(O, it.id)
    if (!b) return
    const close = seen.filter((p) => dist(p, b.mid) < 40).length
    seen.push(b.mid)
    out.push({
      id: it.id,
      at: b.mid,
      text: rangeText(it.low, it.high),
      sub: workText(it, lang),
      tone: 'fix',
      side: ['ne', 'sw', 'se', 'nw'][close % 4],
      delay: delay + j * step,
    })
  })
  return out
}

// "How often": the load levels at which the full campus overloads the grid (the engine's per-level check of every
// line, so it says "the grid", not the weak point); "only" when every level below the 4 PM peak holds.
export function oftenText(often, lang) {
  if (!often?.levels?.length) return null
  const T = P[lang]
  if (often.every_level) return T.oftenEvery(atLevel(often.lowest, lang))
  const list = often.levels.filter((x) => x.over).map((x) => atLevel(x.name, lang))
  if (!list.length) return null
  return often.peak_only || often.heat_only ? T.oftenPeak(joinList(list, lang)) : T.oftenSome(joinList(list, lang))
}

// the operating rule's pin on the campus, in three short lines (SVG text doesn't wrap, and the map between the slide
// and the Ask sidebar is narrow): "Steps down to 550 MW" / "at the 4 PM peak" / "230 MW in a heat wave" (one level:
// "No new equipment" last), the engine's per-level sizes; set to the campus's upper left, clear of the town's label
// The two levels named are the plain option line's (bulletin._plain_rows): the lowest step and this case's own level
// (else the highest), each once.
export function flexPin(flex, lang) {
  const T = P[lang]
  const steps = flex?.steps?.length ? [...flex.steps].sort((a, b) => (a.level ?? 0) - (b.level ?? 0)) : flex?.peak_mw != null ? [{ runs_mw: flex.peak_mw, name: '4 PM' }] : []
  const here = steps.length > 2 ? steps.find((s) => flex?.case_level != null && Math.abs((s.level ?? -1) - flex.case_level) < 0.005) || steps[steps.length - 1] : null
  const pick = here ? [steps[0], here] : steps
  const [first, second] = pick.filter((s, i) => pick.findIndex((x) => atLevel(x.name, lang) === atLevel(s.name, lang)) === i)
  if (!first) return { text: T.noEquipment, side: 'nw' }
  return {
    text: `${T.flexTagHead} ${fmt(first.runs_mw)} MW`,
    sub: atLevel(first.name, lang),
    sub2: second ? `${fmt(second.runs_mw)} MW ${atLevel(second.name, lang)}` : T.noEquipment,
    side: 'nw',
  }
}

// what an option keeps, in words: an operating rule that only steps down at the peak keeps the full campus the rest of the time
export function keepsText(o, T) {
  if (o?.flex?.peak_only) {
    const when = flexWhen(o.flex, T === P.es ? 'es' : 'en')
    if (when) return T.flexExcept(when)
  }
  // an operating rule that steps down at other hours too: its size is this case's hour's, not the campus it keeps
  if (o?.family === 'flexible' && (o?.kept_pct ?? 100) < 99.5) return T.keepsAtHour(Math.round(o.kept_pct))
  // power of its own: the campus runs in full, the grid supplies less (its kept_mw is the grid's share)
  if (o?.family === 'onsite' && o?.gen?.net_mw != null) return T.onsiteKeeps(fmt(o.gen.net_mw))
  return (o?.kept_pct ?? 0) >= 99.5 ? T.keepsAll : T.keeps(Math.round(o?.kept_pct ?? 0))
}

// the lead option's elements as map positions (the bottom line frames them)
export function leadPoints(O, o) {
  const ids = o?.cost?.items?.length ? o.cost.items.map((it) => it.id) : (o?.lines || []).map((l) => l.id)
  return ids.map((id) => branchAt(O, id)?.mid).filter(Boolean)
}
