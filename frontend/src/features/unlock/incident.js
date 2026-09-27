// THE INCIDENT SOLUTION STAGE on Strengthen (CLAUDE.md -> Decisions -> SOLUTIONS, SIMPLE -> IN DEPTH): one incident's
// solutions in depth, opened straight from Watch it fail or the presentation (openIncidentSolution) or by a deep link
// (#/strengthen?incident=<the drop-link query>). Built from the briefing report already computed for that exact case
// (POST /api/briefing with the same body: the review card prefetched it, so it comes from the frontend cache, else
// from the backend's report cache): no new study, no waiting on Gemini. Gemini's plans that land later (the report's
// `agentic` proposer, backend/solutions.py) are picked up by the stage through features/fix's shared poller (one per
// case, every 5 s: the venue shares one IP and /api/briefing allows 30 a minute) and handed in with takeReport.
//
// The state lives in this module (not a component), so the stage survives the Strengthen page remounting on a state
// change; UnlockLayer (always mounted with the map) switches the mode to Strengthen when an incident opens, and closes
// the stage when the viewer leaves Strengthen by the top bar. The deep link is read once at load, before App's own
// #/strengthen handler rewrites the address, and written back while the stage is open (a reload reopens it).
import { useSyncExternalStore } from 'react'
import { cleanBody, getReport } from '../briefing/briefingApi'
import { getTimeToPower } from './unlockApi'

let state = { inc: null }
const subs = new Set()
function set(patch) {
  state = { ...state, ...patch }
  subs.forEach((f) => f())
}
const subscribe = (f) => {
  subs.add(f)
  return () => subs.delete(f)
}
const getInc = () => state.inc
export const useIncident = () => useSyncExternalStore(subscribe, getInc)
const patchInc = (key, patch) => {
  if (state.inc?.key === key) set({ inc: { ...state.inc, ...(typeof patch === 'function' ? patch(state.inc) : patch) } })
}

// ------------------------------------------------------------------ the deep link
// The incident travels as the map's drop link (#/?at=lat,lon&mw=N&state=XX) plus the rest of the case: lf (load
// level), x (more campuses: lat,lon,mw;…), firm, trip (downed lines), up (upgrades id:MVA,…), preset (a catastrophe).
// Coordinates keep 5 decimals: the backend's report cache key rounds to 5, so a reload asks for the same report.
const num = (v, d) => String(Number(Number(v).toFixed(d)))

export function incidentQuery(body) {
  const b = cleanBody(body)
  const q = []
  if (b.lat != null && b.lon != null) q.push(`at=${num(b.lat, 5)},${num(b.lon, 5)}`)
  if (b.mw != null) q.push(`mw=${num(b.mw, 3)}`)
  if (b.region && b.region !== 'FL') q.push(`state=${b.region}`)
  if (b.load_factor != null && Math.abs(b.load_factor - 1) > 1e-9) q.push(`lf=${num(b.load_factor, 3)}`)
  if (b.sites?.length) q.push(`x=${b.sites.map((s) => `${num(s.lat, 5)},${num(s.lon, 5)},${num(s.mw, 3)}`).join(';')}`)
  if (b.firm) q.push('firm=1')
  if (b.preset) q.push(`preset=${encodeURIComponent(b.preset)}`)
  else if (b.trip?.length) q.push(`trip=${b.trip.join(',')}`)
  const ups = Object.entries(b.upgrades || {})
  if (ups.length) q.push(`up=${ups.map(([k, v]) => `${k}:${num(v, 3)}`).join(',')}`)
  return q.join('&')
}

// '#/strengthen?incident=at%3D26.6406%2C-81.8723%26mw%3D1500': the address of the stage for a case
export const incidentHash = (body) => `#/strengthen?incident=${encodeURIComponent(incidentQuery(body))}`

const finite = (...xs) => xs.every((x) => Number.isFinite(x))

// The case a deep link names, or null. Accepts the encoded form above and, leniently, the drop link's own
// parameters beside it (#/strengthen?incident&at=…&mw=…).
export function parseIncidentHash(hash) {
  const m = String(hash || '').match(/^#\/strengthen\?(.*)$/)
  if (!m) return null
  const outer = new URLSearchParams(m[1])
  if (!outer.has('incident')) return null
  const inner = outer.get('incident')
  const q = inner ? new URLSearchParams(inner) : outer
  const body = { region: (q.get('state') || 'FL').toUpperCase(), sites: [], load_factor: 1, trip: [], upgrades: {}, firm: false }
  if (!/^[A-Z]{2}$/.test(body.region)) return null
  const at = (q.get('at') || '').split(',').map(Number)
  const mw = Number(q.get('mw'))
  if (at.length === 2 && finite(...at, mw) && mw > 0) Object.assign(body, { lat: at[0], lon: at[1], mw })
  const lf = Number(q.get('lf'))
  if (q.has('lf') && Number.isFinite(lf) && lf > 0) body.load_factor = lf
  for (const s of (q.get('x') || '').split(';').filter(Boolean)) {
    const [lat, lon, smw] = s.split(',').map(Number)
    if (finite(lat, lon, smw) && smw > 0) body.sites.push({ lat, lon, mw: smw })
  }
  if (q.get('firm') === '1') body.firm = true
  if (q.get('preset')) body.preset = q.get('preset')
  else
    body.trip = (q.get('trip') || '')
      .split(',')
      .filter((s) => s.trim() !== '')
      .map(Number)
      .filter((x) => Number.isInteger(x) && x >= 0)
  for (const p of (q.get('up') || '').split(',').filter(Boolean)) {
    const [k, v] = p.split(':').map(Number)
    if (Number.isInteger(k) && Number.isFinite(v) && v > 0) body.upgrades[String(k)] = v
  }
  const something = body.lat != null || body.sites.length || body.trip.length || body.preset || Math.abs(body.load_factor - 1) > 1e-9
  return something ? body : null
}

// ------------------------------------------------------------------ open / close
let seq = 0

// Open the stage for a case (the body the briefing was fetched with: features/briefing/stage.js bodyFor, or the
// store's caseBody). `o` (useOverload()) switches to Strengthen at once; without it, UnlockLayer does (`want`).
export function openIncidentSolution(body, o = null) {
  const b = cleanBody(body)
  if (!b.region || b.region === 'US') return false
  const key = begin(b, o ? 'app' : 'call')
  if (o?.setMode) {
    patchInc(key, { want: false })
    if (o.mode !== 'unlock') o.setMode('unlock')
  }
  return true
}

// (the key is the cleaned body's JSON: the same order briefingApi caches by, whoever opened it)
function begin(bIn, from) {
  const b = cleanBody(bIn)
  const key = JSON.stringify(b)
  const id = ++seq
  const same = state.inc?.key === key && state.inc.status === 'done'
  set({
    inc: same
      ? { ...state.inc, want: true, shown: false, entered: false, from, opened: Date.now() }
      : { key, body: b, region: b.region, from, want: true, shown: false, entered: false, status: 'loading', report: null, error: null, sel: null, opened: Date.now() },
  })
  loadLeadItems(b.region)
  if (same) return key
  getReport(b).then(
    (report) => id === seq && patchInc(key, { status: 'done', report, error: null }),
    (error) => id === seq && patchInc(key, { status: 'error', error }),
  )
  return key
}

// A newer answer for the incident on screen (the shared poller's, while Gemini's proposer runs): Gemini's verified
// plans join the list. An answer with fewer fixes than the one on screen is an older one: kept out.
export function takeReport(key, report) {
  const inc = state.inc
  if (!report || inc?.key !== key || inc.status !== 'done' || inc.report === report) return
  if ((report.fixes?.length || 0) < (inc.report?.fixes?.length || 0)) return
  patchInc(key, { report })
}

export function retryIncident() {
  const inc = state.inc
  if (inc) begin(inc.body, inc.from)
}

export function closeIncident() {
  seq++
  if (state.inc) set({ inc: null, aim: null })
}

export const selectOption = (i) => state.inc && set({ inc: { ...state.inc, sel: i }, aim: null })
// the element the pointer is on in the rail's list: the map rings it (IncidentLayer)
export const setAim = (id) => (state.aim ?? null) !== id && set({ aim: id })
const getAim = () => state.aim ?? null
export const useAim = () => useSyncExternalStore(subscribe, getAim)
// the mode switch was made (UnlockLayer) / Strengthen is on screen with the stage, loaded or not (from then on,
// leaving Strengthen closes it) / the stage rendered on its own state (from then on, picking another state closes it)
export const consumeWant = () => state.inc?.want && set({ inc: { ...state.inc, want: false } })
export const markEntered = () => state.inc && !state.inc.want && !state.inc.entered && set({ inc: { ...state.inc, entered: true } })
// the stage itself is on screen (it renders only on Strengthen): the switch is made, even before the map (and with it
// UnlockLayer) has loaded, so leaving before then still closes it and a late UnlockLayer never switches back
export const markOnScreen = () => state.inc && (state.inc.want || !state.inc.entered) && set({ inc: { ...state.inc, want: false, entered: true } })
export const markShown = () => state.inc && !state.inc.shown && set({ inc: { ...state.inc, shown: true } })

// ------------------------------------------------------------------ typical time to build (backend/leadtimes.py)
// The kinds of work and their sourced ranges are the same for every state; the route serves them with a cached
// study's time to power (LAZY: it never starts one), so any cached study will do: this state's, else Florida's.
let leadP = null
export function loadLeadItems(region) {
  if (leadP) return leadP
  const pick = (d) => ({ items: d.items, sources: d.sources, flexNote: d.flex_note, flexSources: d.flex_sources || [] })
  // Florida's 1 GW study is baked and warmed: ask it first (another state's is often not cached: a 404 in the console)
  leadP = getTimeToPower({ region: 'FL', mw: 1000 })
    .catch((e) => (region !== 'FL' ? getTimeToPower({ region, mw: 1000 }) : Promise.reject(e)))
    .then(pick)
    .catch(() => {
      leadP = null // asked again on the next incident
      return null
    })
  leadP?.then?.((v) => v && set({ lead: v }))
  return leadP
}
const getLead = () => state.lead || null
export const useLeadItems = () => useSyncExternalStore(subscribe, getLead)

// ------------------------------------------------------------------ read the deep link once, at load
// (before App's mount effect turns #/strengthen into #/; App then switches to Strengthen by itself)
// and when one is opened later in the same tab (a pasted link): UnlockLayer switches to Strengthen (`want`)
try {
  const b = parseIncidentHash(window.location.hash)
  if (b) begin(b, 'link')
  window.addEventListener('hashchange', () => {
    const nb = parseIncidentHash(window.location.hash)
    if (nb && JSON.stringify(cleanBody(nb)) !== state.inc?.key) begin(nb, 'link')
  })
} catch {
  // no window (a test) or a malformed link: the page opens as usual
}
