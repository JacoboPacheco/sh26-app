// The Strengthen page's capacity view (backend/capacity.py): how many campuses of one size the grid carries AT ONCE,
// all connected together, and the cheapest upgrades that let each next one in. Pure helpers only: the budget's
// stops, what a budget buys, the words for the answer, and the case "Try these campuses" hands to Watch it fail.
import { fmt } from '../../geo'
import { presetFor } from '../heat/presets'

export const DEFAULT_CAP_BUDGET = 50e6 // the page opens at the largest stop at or under this (else the first paid one)

// "500 MW", "1 GW", "2 GW", "1.5 GW"
export const sizeLabel = (mw) => (mw >= 1000 ? `${(mw / 1000).toLocaleString('en-US', { maximumFractionDigits: 1 })} GW` : `${fmt(mw)} MW`)

// the search on screen: 'firm' (always on) or 'flexible'
export const capOf = (r, flex) => r?.capacity?.[flex ? 'flexible' : 'firm'] || null

// The budget slider's stops: nothing, then each paid step's running total (high end), so every stop adds a campus.
export function capStops(m) {
  const out = [0]
  for (const st of m?.steps || []) if (!st.free && st.cum_cost.high > out.at(-1) + 0.5) out.push(st.cum_cost.high)
  return out
}

// How many campuses a budget connects at once: every step whose running total fits (free steps come along).
export function capWithin(m, budget) {
  let n = 0
  for (const st of m?.steps || []) {
    if (st.cum_cost.high > budget + 0.5) break
    n = st.n
  }
  return n
}

// The budget the page opens at: the largest stop at or under $50 million, else the first paid stop.
export function defaultCapBudget(m) {
  const stops = capStops(m)
  if (stops.length < 2) return 0
  const within = stops.filter((v) => v <= DEFAULT_CAP_BUDGET)
  return within.length > 1 ? within.at(-1) : stops[1]
}

// The stop index for a budget (the largest stop at or under it).
export function capStopIndex(stops, budget) {
  let i = 0
  stops.forEach((v, k) => {
    if (v <= budget + 0.5) i = k
  })
  return i
}

// What n campuses at once cost (high and low end of the running total).
export const costAt = (m, n) => (n > 0 && m?.steps?.[n - 1] ? m.steps[n - 1].cum_cost : { low: 0, high: 0 })

// Every line and transformer the first n steps raise, each at its final rating: what the demo applies.
export function upgradesUpTo(m, n) {
  const out = {}
  for (const st of (m?.steps || []).slice(0, n)) for (const p of st.projects) out[p.branch_id] = Math.max(out[p.branch_id] || 0, p.rating_after_mva)
  return out
}

// Why the search stopped, as the end of a sentence ("… then the power plants run out").
export const STOP_TEXT = {
  plants: 'the power plants run out',
  no_fix: 'no line upgrade fixes the next one',
  cap: 'the study’s $6 billion cap',
  max: 'the search stops at 40',
  time: 'the search’s time limit',
}
export const stopText = (stop) => STOP_TEXT[stop] || 'the search ends'

// "third", "fourth", … "12th", "21st"
const ORD = ['zeroth', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth', 'eleventh', 'twelfth']
export function ordinal(n) {
  if (n < ORD.length) return ORD[n]
  const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : { 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th'
  return `${n}${s}`
}

// "an eighth", "a third", "an 18th", "an 11th"
export const aOrdinal = (n) => {
  const w = ordinal(n)
  return `${/^(e|8|11th|18)/.test(w) ? 'an' : 'a'} ${w}`
}

// "at the 4 PM summer peak", "at 3 AM", "in a heat wave", "at 95 % of the summer peak"
export function levelPhrase(lf) {
  const p = presetFor(lf)
  if (p?.id === 'afternoon') return 'at the 4 PM summer peak'
  if (p?.id === 'wave') return 'in a heat wave'
  if (p) return `at ${p.label.replace(' ', ' ')}`
  return `at ${Math.round(lf * 100)} % of the summer peak`
}

// the name of a line or transformer in a sentence: "the Jacksonville 64 transformer"
export const theName = (b) => (b?.short ? `the ${b.short}` : b?.label || 'a line')
export const shortName = (b) => b?.short || b?.label?.replace(/^the /, '') || `#${b?.branch_id}`

// "Jacksonville 64 transformer, 234 → 350 MVA"; a line or transformer an earlier campus already raised reads
// "Jacksonville 64 transformer raised again, 350 → 450 MVA"
export const upgradeWords = (p, again = false) => `${shortName(p)}${again ? ' raised again' : ''}, ${fmt(p.rating_before_mva)} → ${fmt(p.rating_after_mva)} MVA`

// A campus count in words for the sentence: "one", "two", … "nine", then figures.
const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine']
export const count = (n) => (n < WORDS.length ? WORDS[n] : fmt(n))

// What stopped a campus before its upgrades, but only when one line or transformer reached its rating first at
// most of the sites tried: the engine always names the most common first limit, and at 3 of 30 sites that is
// not "what stops it".
export function blockOf(st) {
  const b = st?.blocked_by
  return b && b.of > 0 && b.blocks * 2 >= b.of ? b : null
}

// What stands in the way of campus n, for a sentence: "the Jacksonville 64 transformer stops the third", or, when
// no single limit stopped most sites, "the third needs the Orlando 54 transformer raised and one more upgrade".
export function stopsClause(m, n) {
  const st = m?.steps?.[n - 1]
  if (!st) return ''
  const b = blockOf(st)
  if (b) return `${theName(b)} stops the ${ordinal(n)}`
  const lead = st.projects?.[0]
  if (!lead) return `the ${ordinal(n)} fits with the upgrades before it`
  const more = st.projects.length - 1
  return `the ${ordinal(n)} needs ${theName(lead)} raised${more ? ` and ${count(more)} more ${more === 1 ? 'upgrade' : 'upgrades'}` : ''}`
}

// The campus that first raised each line or transformer: {branch_id → n}. A later step that raises it again
// is priced from its rating then to the new one, so the steps add up to building it once to its last rating.
const firstCache = new WeakMap()
export function firstRaised(m) {
  if (!m) return new Map()
  if (firstCache.has(m)) return firstCache.get(m)
  const out = new Map()
  for (const st of m.steps || []) for (const p of st.projects) if (!out.has(p.branch_id)) out.set(p.branch_id, st.n)
  firstCache.set(m, out)
  return out
}
export const raisedAgain = (m, p, n) => (firstRaised(m).get(p.branch_id) ?? n) < n
