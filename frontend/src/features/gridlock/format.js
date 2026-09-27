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
export const TIER_LABEL_ES = {
  touching: 'Deben coordinarse',
  row: 'Compartir el terreno',
  site: 'Compartir la logística de obra',
  crews: 'Compartir cuadrillas y equipos',
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

// The distance limit in words, as the engine words it (gridlock.limit_text): whole miles read "25 mi (40.2 km)",
// anything else "30 km (18.6 mi)"; never a raw float like 14.484096000000001
export function limitText(maxKm) {
  if (maxKm == null) return '–'
  const mi = maxKm / KM_PER_MI
  if (Math.abs(mi - Math.round(mi)) < 1e-9) return `${Math.round(mi)} mi (${maxKm.toFixed(1)} km)`
  return `${Number(maxKm.toFixed(3))} km (${mi.toFixed(1)} mi)`
}

// The limit in miles alone, for the list's head: whole miles as "9 mi" (the Filters slider moves in whole miles), else
// one decimal
export function limitMi(maxKm) {
  const mi = maxKm / KM_PER_MI
  return Math.abs(mi - Math.round(mi)) < 1e-9 ? `${Math.round(mi)} mi` : `${mi.toFixed(1)} mi`
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

export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const MONTHS_ES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
export function fmtDate(iso, lang = 'en') {
  if (!iso) return lang === 'es' ? 'sin fecha' : 'no date'
  const [y, m, d] = iso.split('-').map(Number)
  if (!y) return iso
  if (lang === 'es') return d ? `${d} ${MONTHS_ES[m - 1]} ${y}` : `${MONTHS_ES[m - 1]} ${y}`
  return d ? `${MONTHS[m - 1]} ${d}, ${y}` : `${MONTHS[m - 1]} ${y}`
}

// the filings' status and the engine's kind of work, in Spanish for the sheet's ES view (the data keeps English)
const STATUS_ES = { 'In Progress': 'En curso', Planned: 'Planificado' }
export const statusText = (s, lang = 'en') => (lang === 'es' ? STATUS_ES[s] || s : s)
const KIND_ES = {
  new_line: 'línea nueva',
  line_rebuild: 'reconstrucción de línea',
  reconductor: 'recableado',
  substation: 'subestación',
  tap: 'derivación',
  other: 'otra obra',
}
export const kindText = (p, lang = 'en') => (lang === 'es' ? KIND_ES[p?.kind] || p?.kind_label : p?.kind_label || KIND_LABEL[p?.kind]?.toLowerCase())

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

// Georgia's table prints names in capitals: title case for display only (the data keeps the filing's text).
// Kept as filed: short codes (a sponsor prefix like "SAV:" or "GTC:", a code alone in parentheses like "(USA)",
// anything with "&" like "LG&E", roman numerals, the few known codes below) and any token with a digit ("#5",
// "23O"), whose "KV" becomes "kV" ("230/115KV" -> "230/115kV"). Every other word is capitalized, also after
// "(", "-" and "/" ("(Second Line)", "Echeconnee-Wellston", "Reconductor/Rebuild"), with "Mc" names ("McIntosh").
// useAgreement.js maps the backend's older rule (backend/agreement.py _display_name) onto this one in drafts.
const SMALL = new Set(['and', 'of', 'to', 'on', 'at', 'the', 'for'])
const CODES = new Set(['CC', 'TA', 'MCI', 'SPA', 'XFMR', 'II', 'III', 'IV', 'VI', 'DESC', 'APC', 'FPL', 'USA', 'SAV', 'GTC', 'MEAG', 'DU'])
function nameWord(w, i) {
  const core = w.replace(/[^A-Za-z&]/g, '')
  if (!core) return w
  if (/\d/.test(w)) return w.replace(/KV(?=[^A-Za-z]|$)/g, 'kV')
  if (w === 'KV') return 'kV'
  const letters = core.replace(/&/g, '').length
  if (letters <= 4 && (/^\([A-Z&]+\)[,.;:]?$/.test(w) || /^[A-Z&]+:$/.test(w) || core.includes('&'))) return w
  if (CODES.has(core)) return w
  const lower = w.toLowerCase()
  if (i && SMALL.has(lower)) return lower
  return lower.replace(/(^|[(\-/])mc([a-z])/g, (m, p, c) => `${p}Mc${c.toUpperCase()}`).replace(/(^|[(\-/])([a-z])/g, (m, p, c) => p + c.toUpperCase())
}
export function displayName(name) {
  if (!name || name.toUpperCase() !== name) return name
  return name
    .split(' ')
    .map((w, i) => nameWord(w, i))
    .join(' ')
}

// Projects whose names nearly match (DESC-6809E "… Plumb Branch 46kV Rebuilds" and DESC-6809G "… Plumb Branch
// 46kV" are two filed projects): the list and the sheet add the project number to tell them apart.
const nameKey = (name) =>
  (displayName(name) || '')
    .toLowerCase()
    .replace(/\b(rebuilds?|reconductors?|upgrades?|project|phase \w+)\b/g, '')
    .replace(/[^a-z0-9]/g, '')
export function nearDuplicates(projects) {
  const by = new Map()
  for (const p of projects || []) {
    const k = nameKey(p.name)
    if (!k) continue
    by.set(k, [...(by.get(k) || []), p.id])
  }
  return new Set([...by.values()].filter((ids) => ids.length > 1).flat())
}

// When the two build windows meet, against today (the engine's `ahead`): {status, tone, row, short, months}
// row: the list's phrase ("shares 12 months, still ahead"); short: the status alone ("still ahead");
// status: 'future' | 'open' | 'past' | 'apart' | 'unknown'; tone 'past' reads muted.
const WHEN = {
  en: {
    future: 'still ahead',
    open: 'open now',
    past: 'passed as filed',
    shares: (m, s) =>
      s === 'past' ? `shared ${m} month${m === 1 ? '' : 's'}, passed as filed` : `shares ${m} month${m === 1 ? '' : 's'}, ${s === 'open' ? 'open now' : 'still ahead'}`,
    // "build windows", never just "windows": a same-station pair also gives its in-service dates, and the two gaps differ
    apart: (x) => `build windows ${x} apart`,
    apartPast: (x) => `build windows ${x} apart, passed as filed`,
    meet: 'build windows meet end to start',
    unknown: 'timing unknown',
    months: (m) => `${m} month${m === 1 ? '' : 's'} shared`,
  },
  es: {
    future: 'aún por delante',
    open: 'abierta ahora',
    past: 'ya pasó, según lo publicado',
    shares: (m, s) => `${m} mes${m === 1 ? '' : 'es'} en común, ${s === 'past' ? 'ya pasados' : s === 'open' ? 'abierta ahora' : 'aún por delante'}`,
    apart: (x) => `ventanas de obra separadas ${x}`,
    apartPast: (x) => `ventanas de obra separadas ${x}, ya pasadas`,
    meet: 'una ventana de obra empieza cuando acaba la otra',
    unknown: 'calendario desconocido',
    months: (m) => `${m} mes${m === 1 ? '' : 'es'} en común`,
  },
}
function spanText(days, lang) {
  const es = lang === 'es'
  if (days < 60) return es ? `${days} días` : `${days} days`
  if (days < 730) return es ? `${Math.round(days / 30.44)} meses` : `${Math.round(days / 30.44)} months`
  return es ? `${nf1.format(days / 365.25)} años` : `${nf1.format(days / 365.25)} years`
}
export function whenOf(o, lang = 'en') {
  const t = WHEN[lang] || WHEN.en
  const m = Math.round(o?.windows_overlap_months || 0)
  if (o?.same_window || m > 0) {
    const s = o.ahead === 'past' ? 'past' : o.ahead === 'open' ? 'open' : 'future'
    return { status: s, tone: s, row: m > 0 ? t.shares(m, s) : t[s], short: t[s], months: m > 0 ? t.months(m) : null }
  }
  if (o?.window_gap_days != null) {
    const past = o.ahead === 'past'
    const row = o.window_gap_days === 0 ? t.meet : past ? t.apartPast(spanText(o.window_gap_days, lang)) : t.apart(spanText(o.window_gap_days, lang))
    return { status: 'apart', tone: past ? 'past' : 'apart', row, short: row, months: null }
  }
  return { status: 'unknown', tone: 'past', row: t.unknown, short: t.unknown, months: null }
}

// Same station: the engine's class above every distance tier (an endpoint of each project is the same substation,
// one OpenStreetMap feature in both filings). o.shared_station = {name, osm_url, osm_name, lat, lon, a_end, b_end,
// a_in_service, b_in_service, months_apart, reason, how}. The English sentence is the engine's own reason.
export const SAME_STATION = {
  en: { label: 'Same station', tag: (n) => `Same station: ${n}`, sub: 'same station in both filings' },
  es: { label: 'Misma subestación', tag: (n) => `Misma subestación: ${n}`, sub: 'misma subestación en ambos documentos' },
}
function yearsMonths(months, lang) {
  const y = Math.floor(months / 12)
  const m = months % 12
  const es = lang === 'es'
  const ys = y ? (es ? `${y} año${y === 1 ? '' : 's'}` : `${y} year${y === 1 ? '' : 's'}`) : ''
  const ms = m ? (es ? `${m} mes${m === 1 ? '' : 'es'}` : `${m} month${m === 1 ? '' : 's'}`) : ''
  return [ys, ms].filter(Boolean).join(es ? ' y ' : ' ')
}
// {lead, rest}: "As filed, both projects work at Thurmond Dam" + ": in service Dec 2024 (DESC) and Jun 2033 (Georgia Power)".
// The in-service DATES only, never a second gap: the pair's one time gap is its build windows' (the list, the sheet's
// "Shared build window" and the score all use that one), so "8 years 5 months apart" here would read as a contradiction.
export function stationSentence(o, lang = 'en') {
  const s = o?.shared_station
  if (!s) return null
  const ma = s.a_in_service?.slice(0, 7)
  const mb = s.b_in_service?.slice(0, 7)
  const ua = utilityShort(o.a_utility)
  const ub = utilityShort(o.b_utility)
  if (lang === 'es') {
    let rest
    if (ma && mb) rest = ma === mb ? `: ambos entran en servicio en ${fmtDate(ma, 'es')}` : `: entran en servicio en ${fmtDate(ma, 'es')} (${ua}) y ${fmtDate(mb, 'es')} (${ub})`
    else if (ma || mb) rest = `: el de ${ma ? ua : ub} entra en servicio en ${fmtDate(ma || mb, 'es')}; el documento de ${ma ? ub : ua} no da fecha de entrada en servicio`
    else rest = '; ningún documento da fecha de entrada en servicio'
    return { lead: `Según lo publicado, ambos proyectos trabajan en ${s.name}`, rest }
  }
  let rest
  if (ma && mb) rest = ma === mb ? `: both in service ${fmtDate(ma)}` : `: in service ${fmtDate(ma)} (${ua}) and ${fmtDate(mb)} (${ub})`
  else if (ma || mb) rest = `: in service ${fmtDate(ma || mb)} (${ma ? ua : ub}); ${ma ? ub : ua}'s filing gives no in-service date`
  else rest = '; neither filing gives an in-service date'
  return { lead: `As filed, both projects work at ${s.name}`, rest }
}
export const stationGap = (o, lang = 'en') => (o?.shared_station?.months_apart ? yearsMonths(o.shared_station.months_apart, lang) : null)

// What each tier lets two projects share, in Spanish (TIER_SHARE is the English)
export const TIER_SHARE_ES = {
  touching: 'la programación de cortes y las estructuras de cruce',
  row: 'el derecho de paso, los caminos de acceso y los permisos',
  site: 'los patios de acopio y las entregas',
  crews: 'cuadrillas y equipos',
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
