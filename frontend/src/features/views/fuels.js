// The generation fuels the energy view stacks, in the validated palette order (colors are tokens in views.css).

// The generation fuels in stack order. Fixed: a fuel keeps its color wherever it appears. The five real hues
// come in the palette's own validated order; "Oil and other" is the neutral fold-in (never a generated hue).
export const FUELS = [
  { id: 'gas', label: 'Gas', color: 'var(--vw-c1)' },
  { id: 'coal', label: 'Coal', color: 'var(--vw-c2)' },
  { id: 'hydro', label: 'Hydro', color: 'var(--vw-c3)' },
  { id: 'solar', label: 'Solar', color: 'var(--vw-c4)' },
  { id: 'wind', label: 'Wind', color: 'var(--vw-c5)' },
  { id: 'nuclear', label: 'Nuclear', color: 'var(--vw-c6)' },
  { id: 'other', label: 'Oil and other', color: 'var(--vw-neutral)' },
]
const FUEL_GROUP = { 'offshore wind': 'wind', oil: 'other', geothermal: 'other' }
export const fuelGroup = (f) => FUEL_GROUP[f] || (FUELS.some((x) => x.id === f) ? f : 'other')

// {fuel: mw} (the API's ten fuels) -> {gas, coal, hydro, solar, wind, nuclear, other}
export function groupFuels(byFuel = {}) {
  const out = Object.fromEntries(FUELS.map((f) => [f.id, 0]))
  Object.entries(byFuel).forEach(([f, v]) => {
    out[fuelGroup(f)] += Number(v) || 0
  })
  return out
}
