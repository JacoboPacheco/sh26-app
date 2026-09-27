// Build together in plain words, for someone who has never read a transmission plan: where two projects are, when both
// are building, what kind of window a date is, and today's date in the viewer's own time zone. Every figure is the
// engine's; this only words it.
import { KM_PER_MI, MONTHS, MONTHS_ES, fmtMi, utilityShort } from './format'

// today as YYYY-MM-DD in the viewer's time zone (the draft's "Generated" date and the timeline's "Today" use this one
// date; the server's UTC date could be a day ahead in the evening)
export function localToday() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

const ym = (iso) => {
  const [y, m] = String(iso || '').split('-').map(Number)
  return y && m ? { y, m } : null
}

// "Jan–Jun 2027", "Nov 2026–Mar 2027" (one year said once when both ends share it)
export function rangeText(start, end, lang = 'en') {
  const a = ym(start)
  const b = ym(end)
  const M = lang === 'es' ? MONTHS_ES : MONTHS
  if (!a && !b) return ''
  if (!a || !b) return `${M[(a || b).m - 1]} ${(a || b).y}`
  if (a.y === b.y && a.m === b.m) return `${M[a.m - 1]} ${a.y}`
  if (a.y === b.y) return `${M[a.m - 1]}–${M[b.m - 1]} ${a.y}`
  return `${M[a.m - 1]} ${a.y}–${M[b.m - 1]} ${b.y}`
}

const MON = Object.fromEntries(MONTHS.map((m, i) => [m, i + 1]))
// the months both build windows share: the engine's joint_window when it sends one, else read from its own reason line
// ("Build windows overlap by 5 months (Jan 2027 to Jun 2027)")
export function jointWindowOf(o) {
  if (o?.joint_window?.start) return { start: o.joint_window.start, end: o.joint_window.end }
  for (const r of o?.reasons || []) {
    const m = /\((\w{3}) (\d{4}) to (\w{3}) (\d{4})\)/.exec(r)
    if (m && /overlap/i.test(r) && MON[m[1]] && MON[m[3]]) {
      const p = (n) => String(n).padStart(2, '0')
      return { start: `${m[2]}-${p(MON[m[1]])}`, end: `${m[4]}-${p(MON[m[3]])}` }
    }
  }
  return null
}

// close only where their nearest ends meet: within the limit at the closest points, beyond it between centres
// (Sperry's method), e.g. 22.2 mi at the closest points and 29.2 mi between centres
export function nearEndsOnly(o, maxKm) {
  if (typeof o?.near_ends_only === 'boolean') return o.near_ends_only
  return o?.center_distance_km != null && o.distance_km != null && o.distance_km <= maxKm && o.center_distance_km > maxKm
}

function spanText(days) {
  if (days < 60) return `${days} days`
  if (days < 730) return `${Math.round(days / 30.44)} months`
  return `${(days / 365.25).toFixed(1)} years`
}

// where, in a few words
export function whereText(o) {
  if (o?.shared_station) return `Same substation (${o.shared_station.name})`
  if (o?.distance_km == null) return 'Distance unknown'
  if (o.distance_km < 0.05) return o.crosses ? 'The lines cross' : 'The projects touch'
  return `${fmtMi(o.distance_mi ?? o.distance_km / KM_PER_MI)} apart`
}

// a side's build window is a filed PLANNING window (Georgia's start-to-need span, which can run for years): the two
// windows overlap, which is not the same as both crews building then (J3)
export const planningSide = (o) => Array.isArray(o?.window_kinds) && o.window_kinds.includes('planning')

// when, in a few words: "both building Jan–Jun 2027", "build windows overlap Jun–Dec 2029", "build windows 8 months apart"
export function whenText(o) {
  const jw = jointWindowOf(o)
  if (o?.same_window || (o?.windows_overlap_months || 0) > 0) {
    const r = jw ? rangeText(jw.start, jw.end) : `${Math.round(o.windows_overlap_months || 0)} months`
    const plan = planningSide(o)
    if (o.ahead === 'past') return plan ? `build windows overlapped ${r}` : `both built ${r}, as filed`
    if (plan) return jw ? `build windows overlap ${r}` : `build windows overlap for ${r}`
    return jw ? `both building ${r}` : `both building for ${r}`
  }
  if (o?.window_gap_days != null) {
    if (o.window_gap_days === 0) return 'one build window starts as the other ends'
    return `build windows ${spanText(o.window_gap_days)} apart${o.ahead === 'past' ? ', as filed' : ''}`
  }
  return 'timing not filed'
}

// the list's one-line reason: "2.1 mi apart, both building Mar–Jun 2027"
export const reasonLine = (o) => `${whereText(o)}, ${whenText(o)}`

// what kind of dates a build window is (J3): Georgia's filings give a start-to-need span, which can run for years;
// DESC's come from the years its budget is spent; a missing start is estimated
export function windowKind(p) {
  const w = p?.window || p?.build_window_filed
  if (!w) return p?.in_service ? 'in-service date only' : 'no dates filed'
  if (w.label) return w.label
  if (w.assumed) return 'estimated: the 24 months before it enters service'
  if (p.utility === 'DESC') return 'the years its filing budgets spending'
  return 'filed planning window (start to need date)'
}

// a location confidence in plain words
export const CONF = {
  high: 'placed with high confidence',
  medium: 'placed with medium confidence',
  low: 'placed with low confidence: check before relying on it',
}

// who: "DESC", "Georgia Power"
export const who = (u) => utilityShort(u)
