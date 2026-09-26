// Fuels: names, the order chips list them in, and the map's color family for each.
//
// Color (validated with the dataviz skill's checker on the map's land color, all pairs, dark):
// only three hues carry meaning, so the night map stays quiet — coal ochre, nuclear violet,
// renewables aqua. Gas (most plants), oil and anything else are neutral, told apart by lightness.
// Identity never rests on color alone: the hover label, the card and the fuel chips name the fuel.
// The hues themselves live in plants.css (`--pl-<family>`); this file only names the family.

const FUELS = {
  gas: { label: 'Gas', family: 'gas' },
  ng: { label: 'Gas', family: 'gas' },
  coal: { label: 'Coal', family: 'coal' },
  nuclear: { label: 'Nuclear', family: 'nuclear' },
  oil: { label: 'Oil', family: 'oil' },
  dfo: { label: 'Oil', family: 'oil' },
  solar: { label: 'Solar', family: 'renew' },
  wind: { label: 'Wind', family: 'renew' },
  'offshore wind': { label: 'Offshore wind', family: 'renew' },
  wind_offshore: { label: 'Offshore wind', family: 'renew' },
  hydro: { label: 'Hydro', family: 'renew' },
  geothermal: { label: 'Geothermal', family: 'renew' },
  other: { label: 'Other', family: 'other' },
}

export const fuelLabel = (fuel) => FUELS[fuel]?.label || String(fuel || 'other').replace(/^\w/, (c) => c.toUpperCase())
export const fuelFamily = (fuel) => FUELS[fuel]?.family || 'other'

// "INDIANTOWN 4" -> "Indiantown 4" (the dataset's names are upper case)
export function pretty(name) {
  return String(name || '')
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

// A plant's display name: its substation (synthetic) and fuel, e.g. "Bartow 6 · gas".
export const plantName = (p) => `${pretty(p.sub_name || p.name)} · ${fuelLabel(p.fuel).toLowerCase()}`

export const fmt = (n) => Math.round(Number(n) || 0).toLocaleString('en-US')

// People counts are estimates: round them so they don't look more exact than they are.
export function approx(n) {
  const v = Math.max(0, Number(n) || 0)
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)} million`
  if (v >= 10000) return fmt(Math.round(v / 1000) * 1000)
  if (v >= 1000) return fmt(Math.round(v / 100) * 100)
  return fmt(v)
}

export const pct = (share) => `${Math.round((Number(share) || 0) * 100)} %`
