// Display helpers for Build plans (numbers come from the engine; this only words them).

export const KM_PER_MI = 1.609344

// The utilities in the filings. DESC and Georgia Power are the headline pair; Georgia's other ITS
// sponsors are extra comparisons, off by default.
export const UTILITIES = [
  { id: 'DESC', name: 'Dominion Energy South Carolina', short: 'DESC', state: 'SC', tone: 'desc' },
  { id: 'GPC', name: 'Georgia Power', short: 'Georgia Power', state: 'GA', tone: 'gpc' },
  { id: 'GTC', name: 'Georgia Transmission Corp.', short: 'GTC', state: 'GA', tone: 'ga' },
  { id: 'MEAG', name: 'MEAG Power', short: 'MEAG', state: 'GA', tone: 'ga' },
  { id: 'DU', name: 'Dalton Utilities', short: 'Dalton', state: 'GA', tone: 'ga' },
]
export const UTILITY = Object.fromEntries(UTILITIES.map((u) => [u.id, u]))
export const utilityName = (id) => UTILITY[id]?.name || id
export const utilityShort = (id) => UTILITY[id]?.short || id
export const toneOf = (id) => UTILITY[id]?.tone || 'ga'

// Sperry's tiers in plain words: what each distance lets two projects share.
export const TIER_SHARE = {
  touching: 'Outage timing and crossing structures',
  row: 'Right-of-way, access roads and permits',
  site: 'Laydown yards and deliveries',
  crews: 'Crews and equipment',
}
export const TIER_ORDER = ['touching', 'row', 'site', 'crews']
export const TIER_LABEL = {
  touching: 'Must coordinate',
  row: 'Share the land',
  site: 'Share site logistics',
  crews: 'Share crews and equipment',
}

const nf1 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })

export const fmtInt = (n) => (n == null ? '–' : nf0.format(n))

export function fmtKm(km) {
  if (km == null) return '–'
  if (km < 0.05) return 'touching'
  return km < 10 ? `${nf1.format(km)} km` : `${nf0.format(km)} km`
}

export function fmtMi(mi) {
  if (mi == null) return '–'
  return mi < 10 ? `${nf1.format(mi)} mi` : `${nf0.format(mi)} mi`
}

export function fmtDistance(km, mi) {
  if (km == null) return '–'
  if (km < 0.05) return 'Touching'
  return `${fmtKm(km)} (${fmtMi(mi ?? km / KM_PER_MI)})`
}

// The distance the chosen method measures: the engine always sends the closest-points distance as
// distance_km, and Sperry's centre-to-centre distance separately as center_distance_km / _mi.
export function pairDistance(o, method) {
  if (method === 'center' && o.center_distance_km != null) {
    return { km: o.center_distance_km, mi: o.center_distance_mi ?? o.center_distance_km / KM_PER_MI, how: 'between centers' }
  }
  return { km: o.distance_km, mi: o.distance_mi, how: 'at the closest points' }
}

export function fmtPairDistance(o, method) {
  const d = pairDistance(o, method)
  if (method === 'center') return d.km == null ? '–' : `${fmtKm(d.km)} (${fmtMi(d.mi)}) between centers`
  return fmtDistance(d.km, d.mi)
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export function fmtDate(iso) {
  if (!iso) return 'no date'
  const [y, m, d] = iso.split('-').map(Number)
  if (!y) return iso
  return d ? `${MONTHS[m - 1]} ${d}, ${y}` : `${MONTHS[m - 1]} ${y}`
}

export function fmtGap(days) {
  if (days == null) return 'timing unknown'
  if (days === 0) return 'in service the same day'
  if (days < 60) return `in service ${days} days apart`
  const years = days / 365.25
  return years < 2 ? `in service ${fmtInt(days)} days apart` : `in service ${fmtInt(days)} days (${nf1.format(years)} years) apart`
}

// the timeline in one phrase: shared build months when the windows overlap, else the gap
export function fmtTimeline(o) {
  const m = Math.round(o.windows_overlap_months || 0)
  if (m > 0) return `build windows share ${m} month${m === 1 ? '' : 's'}`
  return fmtGap(o.time_gap_days)
}

export function fmtMoney(n) {
  if (n == null) return '–'
  const a = Math.abs(n)
  if (a >= 1e9) return `$${nf1.format(n / 1e9)}B`
  if (a >= 1e6) return `$${nf1.format(n / 1e6)}M`
  if (a >= 1e4) return `$${nf0.format(n / 1e3)}K`
  return `$${nf0.format(n)}`
}

export function fmtAmount(v, unit) {
  if (v == null) return '–'
  if (!unit || /usd|\$|dollar/i.test(unit)) return fmtMoney(v)
  return `${nf0.format(v)} ${unit}`
}

export function fmtRange(low, high, unit) {
  if (low == null && high == null) return '–'
  if (low === high || high == null) return fmtAmount(low, unit)
  if (low == null) return `up to ${fmtAmount(high, unit)}`
  if (!unit || /usd|\$|dollar/i.test(unit)) return `${fmtMoney(low)}–${fmtMoney(high)}`
  return `${nf0.format(low)}–${nf0.format(high)} ${unit}`
}

export const KIND_LABEL = {
  new_line: 'New line',
  line_rebuild: 'Line rebuild',
  reconductor: 'Reconductor',
  substation: 'Substation',
  tap: 'Tap',
  other: 'Other work',
}

export const fmtKv = (kv) => (kv?.length ? `${kv.join(' / ')} kV` : null)

export function fmtBuiltAt(iso) {
  if (!iso) return 'unknown'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function osmUrl(osm) {
  if (!osm?.type || osm.id == null) return null
  return `https://www.openstreetmap.org/${osm.type}/${osm.id}`
}

// A link to the filing: a PDF opens at the page (browsers honour #page=N); a docket page (Georgia's
// filing is on the PSC's docket search, not a direct PDF) can't, so it's labelled as the docket.
export function sourceLink(source, page) {
  if (!source?.url) return null
  const isPdf = /\.pdf($|[?#])/i.test(source.url)
  if (!isPdf) return { url: source.url, label: 'Open the docket' }
  return { url: page ? `${source.url}#page=${page}` : source.url, label: page ? `Open the PDF at page ${page}` : 'Open the PDF' }
}

// all [lon, lat] points of a project (geometry, else its located endpoints, else its center)
export function projectPoints(p) {
  if (!p) return []
  if (p.geometry?.coords?.length) return p.geometry.coords
  const eps = (p.endpoints || []).filter((e) => e.lat != null && e.lon != null).map((e) => [e.lon, e.lat])
  if (eps.length) return eps
  return p.center ? [[p.center[1], p.center[0]]] : []
}

// [minLon, minLat, maxLon, maxLat] of [lon, lat] points
export function boundsOf(points) {
  if (!points.length) return null
  let b = [Infinity, Infinity, -Infinity, -Infinity]
  for (const [x, y] of points) b = [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)]
  return b
}
