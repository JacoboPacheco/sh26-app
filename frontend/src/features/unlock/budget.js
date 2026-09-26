// The Strengthen page's budget: how much of the engine's plan (cheapest per site first) is bought. The plan is a
// list of steps, each with its cumulative cost (high end of each estimate); a budget buys every step whose
// cumulative cost fits. The slider moves over round amounts ($10M, $25M, $250M, $1.2B ...) spaced evenly on a log
// scale, because the plan's cost spans three decades and its first, cheapest steps matter most. Pure helpers only.
import { fmt } from '../../geo'

export const DEFAULT_BUDGET = 250e6
const MANTISSAS = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 7.5]

// "$5.6M", "$233M", "$1.2B", "$10.2B", "$750k" (compact, for chips, axes and table cells)
export function shortMoney(x) {
  const v = Number(x) || 0
  if (v >= 1e9) return `$${(v / 1e9).toFixed(v >= 1e10 ? 1 : 2).replace(/\.?0+$/, '')}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(v >= 1e8 ? 0 : 1).replace(/\.0$/, '')}M`
  if (v >= 1e3) return `$${Math.round(v / 1e3)}k`
  return `$${Math.round(v)}`
}

// "7 GW", "1.5 GW", "750 MW": site options, each site tested alone (never capacity that connects together)
export const siteOptions = (mw) => (mw >= 1000 ? `${(mw / 1000).toLocaleString('en-US', { maximumFractionDigits: 1 })} GW` : `${fmt(mw)} MW`)

const total = (r) => r?.steps?.at(-1)?.cum_cost.high || 0

// The slider's stops for a study: nothing (today), then round amounts from just under its first step up to its
// whole plan (the last stop).
export function budgetStops(r) {
  const all = total(r)
  if (!all) return [0]
  const lo = r.steps[0].cum_cost.high * 0.5
  const out = [0]
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(all)); e++) {
    for (const m of MANTISSAS) {
      const v = Math.round(m * 10 ** e)
      if (v >= lo && v < all * 0.97) out.push(v)
    }
  }
  out.push(all)
  return out
}

// How many plan steps a budget buys (every step whose cumulative high-end cost fits).
export function stepsWithin(r, budget) {
  if (!r?.steps?.length) return 0
  let n = 0
  for (const st of r.steps) {
    if (st.cum_cost.high > budget + 0.5) break
    n = st.n
  }
  return n
}

// The budget a study opens at: $250M when that buys at least one step, else the first step, never past the plan.
export function defaultBudget(r) {
  const all = total(r)
  if (!all) return 0
  const first = r.steps[0].cum_cost.high
  if (first > DEFAULT_BUDGET) return budgetStops(r).find((v) => v >= first) ?? all
  return Math.min(DEFAULT_BUDGET, all)
}

// The stop index for a budget (the largest stop at or under it; 0 below the first).
export function stopIndex(stops, budget) {
  let i = 0
  stops.forEach((v, k) => {
    if (v <= budget + 0.5) i = k
  })
  return i
}

// What the plan has bought after n steps: the headline's figures.
export function at(r, n) {
  const st = n > 0 ? r.steps[n - 1] : null
  const ok0 = r.before.sites_ok
  let prevented = 0
  let preventedMax = 0
  for (const s of r.steps.slice(0, n)) {
    prevented += s.blackout_sites_prevented || 0
    preventedMax = Math.max(preventedMax, s.blackout_prevented_max || 0)
  }
  return {
    n,
    step: st,
    cost: st ? st.cum_cost.high : 0,
    costLow: st ? st.cum_cost.low : 0,
    upgrades: st ? st.cum_upgrades : 0,
    more: st ? st.sites_ok - ok0 : 0,
    mw: st ? (st.mw_unlocked ?? (st.sites_ok - ok0) * r.mw) : 0,
    gone: st ? st.biggest_gone || 0 : 0,
    prevented, // sites that would have set off a blackout and now connect with no line over its limit
    preventedMax, // the biggest of those blackouts (people hit, estimate)
    overloads: st ? st.overloads_left : (r.headline?.strain?.line_overloads_before ?? 0),
  }
}

// "the 3 biggest blackouts gone" / "the biggest blackout gone" / "2 blackouts prevented" / ''
export function goneText(a) {
  if (a.gone >= 2) return `the ${fmt(a.gone)} biggest blackouts gone`
  if (a.gone === 1) return 'the biggest blackout gone'
  if (a.prevented) return `${fmt(a.prevented)} ${a.prevented === 1 ? 'blackout' : 'blackouts'} prevented`
  return ''
}

// the package's lead upgrade: a weak point first, then the dearest part
export const leadOf = (projects) => [...projects].sort((a, b) => (a.weak_point || 99) - (b.weak_point || 99) || b.cost.high - a.cost.high)[0]
export const whereOf = (projects) => {
  const towns = [...new Set(projects.flatMap((p) => (p.where || '').split(' · ')).filter(Boolean))]
  return towns.length > 2 ? `${towns.slice(0, 2).join(' · ')} +${towns.length - 2}` : towns.join(' · ')
}
