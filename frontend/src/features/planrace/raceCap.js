// A plan from the race (PlanRacePanel's onShowPlan payload) in the shape the Strengthen page's meter and map read a
// campuses-at-once plan (capacity.js's m, as aiCapPlan builds Gemini's challenge): {today, steps, stop, gemini, bar}.
// A plan built campus by campus (every working plan, the engine's) keeps its own steps; a plan an agent listed itself
// becomes one paid step, like aiCapPlan's. `engine` (the engine's race plan, payload.engine) marks the sites the engine
// didn't pick (st.gem, CapacityLayer's blue outline) and each site's number in the engine's plan (st.engineN).
// Only a plan the referee VERIFIED becomes a meter plan: a failed or over-budget one returns null (the page keeps its
// own plan), so it can never fill the meter as if it worked. The result carries verified and status for the page.
const cache = new WeakMap()

export function raceCapPlan(plan, engine = plan?.engine) {
  if (!plan?.placements?.length) return null
  const status = plan.verdict?.status ?? (plan.verified ? 'verified' : null)
  if (plan.verified !== true || status !== 'verified') return null
  if (cache.has(plan)) return cache.get(plan)
  const eng = new Map((engine?.placements || []).map((p) => [p.id, p.n]))
  const mark = (id) => ({ gem: plan.by === 'gemini' && !eng.has(id), engineN: eng.get(id) ?? null })
  let steps
  if (plan.steps?.length) {
    steps = plan.steps.map((st) => ({ ...st, blocked_by: null, busiest_pct: null, ...mark(st.site.id) }))
  } else {
    const cost = plan.cost || { low: 0, high: 0 }
    const last = plan.placements.length - 1
    steps = plan.placements.map((p, k) => {
      const paid = k === last && (plan.projects?.length || 0) > 0
      return {
        n: k + 1,
        site: { id: p.id, area: p.area, lat: p.lat, lon: p.lon },
        free: !paid,
        cost: paid ? cost : { low: 0, high: 0 },
        cum_cost: paid ? cost : { low: 0, high: 0 },
        projects: paid ? plan.projects : [],
        blocked_by: null,
        busiest_pct: null,
        ...mark(p.id),
      }
    })
  }
  let today = 0
  while (today < steps.length && steps[today].free) today++
  const out = {
    today,
    steps,
    stop: 'race',
    gemini: plan.by === 'gemini',
    bar: engine?.campuses || 0,
    lane: plan.lane,
    name: plan.name,
    verified: true,
    status,
  }
  cache.set(plan, out)
  return out
}
