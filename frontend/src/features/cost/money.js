// Money for people, not accountants: "$18.3 million", "$1.1 billion", "$48,000", "4¢".
// Only formatting happens here; every number comes from the backend (backend/costs.py).

const UNITS = [
  [1e9, 'billion'],
  [1e6, 'million'],
]

const unitOf = (x) => UNITS.find(([div]) => x >= div) || null

// decimals by size: 276 -> "276", 18.3 -> "18.3", 1.25 -> "1.25"; trailing zeros dropped
const places = (v) => (v >= 100 ? 0 : v >= 10 ? 1 : 2)
const num = (v, d) => (d ? v.toFixed(d).replace(/\.?0+$/, '') : Math.round(v).toString())

// small amounts per household: "under 1¢", "4¢"
const cents = (v) => (v < 0.005 ? 'under 1¢' : `${Math.round(v * 100)}¢`)

// one amount in dollars
export function money(x) {
  const v = Number(x) || 0
  if (v <= 0) return '$0'
  const u = unitOf(v)
  if (u) return `$${num(v / u[0], places(v / u[0]))} ${u[1]}`
  if (v >= 10000) return `$${(Math.round(v / 100) * 100).toLocaleString('en-US')}`
  if (v >= 100) return `$${Math.round(v).toLocaleString('en-US')}`
  if (v >= 1) return `$${v.toFixed(2)}`
  return cents(v)
}

// a big figure set in two sizes: {figure: '$1.27', unit: 'billion'} ('' when the amount has no unit word)
export function moneyParts(x) {
  const s = money(x)
  const m = s.match(/^(\S+) (billion|million)$/)
  return m ? { figure: m[1], unit: m[2] } : { figure: s, unit: '' }
}

// a low–high range; when both ends share a unit it is written once, at the high end's precision:
// "$102–276 million", "$96–200 million", "$18.3–64 million"
export function moneyRange(lo, hi) {
  const a = Number(lo) || 0
  const b = Number(hi) || 0
  const mb = money(b)
  if (Math.abs(b - a) < 0.005 || money(a) === mb) return mb
  const ua = a > 0 ? unitOf(a) : null
  const ub = unitOf(b)
  if (ua && ub && ua[1] === ub[1]) {
    const d = places(b / ub[0])
    const x = num(a / ub[0], d)
    const y = num(b / ub[0], d)
    return x === y ? mb : `$${x}–${y} ${ub[1]}`
  }
  // per-household cents from zero: "up to 4¢", not "$0–4¢"
  if (a <= 0 && b < 1) return `up to ${mb}`
  const ma = money(a)
  // "under 1¢ to 4¢" reads better than a dash next to words
  return /^under/.test(ma) ? `${ma} to ${mb}` : `${ma}–${mb}`
}

// the unit a line is quoted in
export const PER = { 'one time': 'one time', year: 'a year', month: 'a month, per household' }
