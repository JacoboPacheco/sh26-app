// The Strengthen page's site-by-site plan (the secondary view): how much of the engine's plan (cheapest per site
// first) a budget buys. The plan is a list of steps, each with its cumulative cost (high end of each estimate); a
// budget buys every step whose cumulative cost fits. Also the page's compact money ("$5.6M"). Pure helpers only.

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

// the package's lead upgrade: a weak point first, then the dearest part
export const leadOf = (projects) => [...projects].sort((a, b) => (a.weak_point || 99) - (b.weak_point || 99) || b.cost.high - a.cost.high)[0]
export const whereOf = (projects) => {
  const towns = [...new Set(projects.flatMap((p) => (p.where || '').split(' · ')).filter(Boolean))]
  return towns.length > 2 ? `${towns.slice(0, 2).join(' · ')} +${towns.length - 2}` : towns.join(' · ')
}
