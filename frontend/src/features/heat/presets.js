// The heat-wave clock's load levels. `factor` scales every existing load in the synthetic model;
// 1.0 is the dataset's own snapshot, a summer-afternoon peak (44.8 GW). The backend caches one
// solved grid per level, so the UI offers a handful of fixed presets, never a free slider.
//
// Measured on this model (scratch/heat_facts*.py, backend/smoke_checks/heat.py keeps them true):
//   ×1.04 with no data center: nothing over limit — the heat alone holds.
//   ×1.04 + 500 MW at Fort Myers (calm at ×1.0): 14-step cascade, ~1.4M homes (estimate), all
//   Southwest Florida (Naples, Cape Coral, Fort Myers, Bonita Springs, Port Charlotte…).
//   ×1.08 would put 3 lines over with no data center, and the heat alone would cascade 18 steps
//   (~1.06M homes) — it blurs "the data center did this", so the heat wave is ×1.04.
export const HEAT_WAVE = 1.04

export const PRESETS = [
  { id: 'night', label: '3 AM', factor: 0.62, hint: 'Overnight low' },
  { id: 'morning', label: '9 AM', factor: 0.82, hint: 'Morning' },
  { id: 'afternoon', label: '4 PM', factor: 1.0, hint: 'Summer afternoon peak — the dataset’s own snapshot' },
  { id: 'wave', label: 'Heat wave', factor: HEAT_WAVE, hint: 'A heat wave pushes every load past the summer peak' },
]

// What each hour stands for (user, Sat 20:20: hovering an hour should say why the time of day matters and what
// temperature it represents). The temperatures are a typical Florida summer day, rounded ("about"); the model
// scales demand for the hour, it does not model weather. Other states get the demand and the reason only.
const FL_TEMP = { night: 'about 76°F', morning: 'about 83°F', afternoon: 'about 92°F', wave: '97°F and up' }
const WHY = {
  night: 'Air conditioning is mostly idle, so demand is at its lowest and the lines have the most room for a new data center.',
  morning: 'Air conditioning is starting up: demand is climbing toward the afternoon peak.',
  afternoon: 'Air conditioning runs flat out, so the grid carries its daily peak and a new data center has the least room left. This is the model’s own snapshot.',
  wave: 'Days of extreme heat push demand past the usual peak: a data center that fits at 4 PM can set off a blackout.',
}
/** the hover card for one hour: {head, demand, why, note} */
export function hourTip(p, region) {
  const fl = region === 'FL'
  return {
    head: fl ? `${p.label} · ${FL_TEMP[p.id]}` : p.label,
    demand: `Demand: ${peakPhrase(p.factor)}`,
    why: WHY[p.id],
    note: fl ? 'A typical Florida summer day. The model changes demand for the hour, not the weather.' : 'The model changes demand for the hour, not the weather.',
  }
}

export const presetFor = (factor) => PRESETS.find((p) => p.factor.toFixed(2) === Number(factor).toFixed(2)) || null

// The light over the map for any level, so a level set elsewhere (not a preset) still gets a mood.
export function ambienceFor(factor) {
  const f = Number(factor)
  if (!Number.isFinite(f)) return 'afternoon'
  if (f < 0.72) return 'night'
  if (f < 0.91) return 'morning'
  if (f <= 1.02) return 'afternoon'
  return 'wave'
}

// "62 % of summer peak" · "the summer peak" · "104 % of summer peak"
export function peakPhrase(factor) {
  const pct = Math.round(Number(factor) * 100)
  return pct === 100 ? 'the summer peak' : `${pct} % of summer peak`
}

// the tooltip and screen-reader description of the whole control
export const HEAT_HINT =
  "Each hour scales every load in the synthetic model from its summer-afternoon peak (4 PM, the dataset's own snapshot); generation re-dispatches to match."
