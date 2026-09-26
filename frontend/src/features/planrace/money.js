// "$36.6M", "$1.2B", "$750k", "$0" (the Strengthen page's compact money)
export function money(x) {
  const v = Number(x) || 0
  if (v >= 1e9) return `$${(v / 1e9).toFixed(v >= 1e10 ? 1 : 2).replace(/\.?0+$/, '')}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(v >= 1e8 ? 0 : 1).replace(/\.0$/, '')}M`
  if (v >= 1e3) return `$${Math.round(v / 1e3)}k`
  return `$${Math.round(v)}`
}
