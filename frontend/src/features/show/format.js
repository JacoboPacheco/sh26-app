// Number formats for the show's counters, bars and meters. Every number comes from the show payload (the
// backend's engine and fact sheet); the player only formats it.

const int = (n) => Math.round(n).toLocaleString('en-US')
const one = (n) => (Math.abs(n) >= 100 ? int(n) : Number(n.toFixed(1)).toLocaleString('en-US'))

// {num, unit, pre}: the figure and its unit apart, so the counter can set the unit smaller
export function parts(value, format) {
  const v = Number(value) || 0
  switch (format) {
    case 'usd': {
      const a = Math.abs(v)
      if (a >= 1e9) return { pre: '$', num: one(v / 1e9), unit: 'billion' }
      if (a >= 1e6) return { pre: '$', num: one(v / 1e6), unit: 'million' }
      return { pre: '$', num: int(v), unit: '' }
    }
    case 'mw':
      return { pre: '', num: int(v), unit: 'MW' }
    case 'gw': {
      // the value is in GW; a value this large can only be MW
      const gw = Math.abs(v) >= 1000 ? v / 1000 : v
      return { pre: '', num: one(gw), unit: 'GW' }
    }
    case 'pct':
      return { pre: '', num: int(v), unit: '%' }
    case 'people':
      return { pre: '', num: int(v), unit: '' }
    default:
      return { pre: '', num: int(v), unit: '' }
  }
}

export function fmtValue(value, format) {
  if (typeof value === 'string') return value
  const p = parts(value, format)
  return `${p.pre}${p.num}${p.unit ? ` ${p.unit}` : ''}`
}
