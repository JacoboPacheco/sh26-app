import { useCallback, useEffect, useSyncExternalStore } from 'react'
import { runCascade } from '../../api'
import { fmt } from '../../geo'
import { cleanBody, getReport, refetchReport } from '../briefing/briefingApi'
import { cascadePeople, reportPeople } from '../cost/figures'
import { money } from '../cost/money'

// THE FLIP: after the cascade, one click runs the SAME case again with the best verified fix and the map stays
// calm; "Show it without the fix" replays the original. Both cascades are kept here, so switching back and forth
// is instant (the store's startCascade takes an already computed cascade as `pending`).
//
// Every path that applies a fix goes through runWithFix: the results panel's button, each option in Fix it, and
// the presentation's "Apply the best fix" / "Apply this fix". The fix is applied with the app store's own setters
// (only the fields that change) and re-run through the cascade endpoint: never a canned animation.
//
// The best fix is the briefing report's (backend/briefing.py → best_fix, ranked by solutions.py): the same one the
// deck's bottom line names and its "Apply the best fix" applies, including a Gemini plan that ranks first.

// ------------------------------------------------------------------ the store (module-level, shared)
let st = { base: null, fix: null, fixed: null, status: 'idle' }
const subs = new Set()
const emit = (patch) => {
  st = { ...st, ...patch }
  subs.forEach((f) => f())
}
const subscribe = (f) => {
  subs.add(f)
  return () => subs.delete(f)
}
const read = () => st

/** {base, fix, fixed, status}: base = the case without the fix {key, body, cascade, hit, stillOut, cost, over};
 *  fix = {key, body, apply, words, cost, by, family, verdict, upgrades}; fixed = its cascade once it ran. */
export const useFlip = () => useSyncExternalStore(subscribe, read, read)

// A case's identity: what the backend solves, rounded so a round trip through the store compares equal.
export function flipKey(body) {
  const b = cleanBody(body || {})
  const r4 = (x) => (x == null ? null : Math.round(Number(x) * 1e4) / 1e4)
  const ups = Object.entries(b.upgrades || {})
    .map(([k, v]) => [String(k), Math.round(Number(v) * 10) / 10])
    .sort((a, z) => (a[0] < z[0] ? -1 : a[0] > z[0] ? 1 : 0))
  return JSON.stringify({
    r: b.region || 'FL',
    lat: r4(b.lat),
    lon: r4(b.lon),
    mw: b.mw != null ? Math.round(Number(b.mw)) : null,
    s: (b.sites || []).map((x) => [r4(x.lat), r4(x.lon), Math.round(Number(x.mw))]),
    lf: Number(b.load_factor ?? 1).toFixed(3),
    t: [...(b.trip || [])].map(Number).sort((a, z) => a - z),
    u: ups,
    f: !!b.firm,
  })
}

// the case with a fix's change in it; a fix's upgrades add to the case's own (a fix only raises ratings)
export function withFix(base, apply) {
  const full = { ...base, ...apply }
  if (apply?.upgrades) full.upgrades = { ...(base.upgrades || {}), ...apply.upgrades }
  return cleanBody(full)
}

// ------------------------------------------------------------------ what the fix is, in plain words
const bare = (label) => String(label || '').replace(/^the\s+/i, '')

// "Raise the North Fort Myers 6 transformer and 1 line": the biggest upgrade by name, the rest counted
function raiseWords(list) {
  const sorted = [...list].sort((a, b) => (Number(b.added_mva) || 0) - (Number(a.added_mva) || 0))
  const top = sorted[0]
  const rest = sorted.slice(1)
  const nx = rest.filter((x) => x.transformer).length
  const nl = rest.length - nx
  const more = []
  if (nx) more.push(`${nx} ${top.transformer ? 'more ' : ''}${nx === 1 ? 'transformer' : 'transformers'}`)
  if (nl) more.push(`${nl} ${top.transformer ? '' : 'more '}${nl === 1 ? 'line' : 'lines'}`)
  return `Raise the ${bare(top.label)}${more.length ? ` and ${more.join(' and ')}` : ''}`
}

export function fixWords(f, fromMw) {
  const list = Array.isArray(f?.detail?.list) ? f.detail.list.filter((x) => x?.label) : []
  const kept = f?.kept_mw ?? f?.apply?.mw
  if ((f.family === 'upgrade' || f.family === 'agentic') && list.length) return raiseWords(list)
  if (f.family === 'combo' && list.length) return `Build ${fmt(kept)} MW and ${raiseWords(list).replace(/^Raise/, 'raise')}`
  if (f.family === 'shrink' && kept != null && fromMw) return `Build ${fmt(kept)} MW instead of ${fmt(fromMw)}`
  return f.action || f.label || 'The verified fix'
}

// the fix as the panels show it: what it is, what it costs (the high end), who proposed it
export function describeFix(f, fromMw) {
  if (!f?.apply) return null
  const cost = f.cost?.high > 0 ? { high: Number(f.cost.high), low: Number(f.cost.low) || 0 } : null
  return {
    apply: f.apply,
    family: f.family,
    by: f.by === 'gemini' || f.family === 'agentic' ? 'gemini' : 'engine',
    verdict: f.verdict,
    outcome: f.outcome || null,
    cost,
    words: fixWords(f, fromMw),
    upgrades: Object.keys(f.apply.upgrades || {}).map(Number),
    strainPct: f.strain?.peak_pct ?? null,
  }
}

// one line under the button: "Raise … · $64 million · engine-verified"
export function fixLine(fix) {
  return [fix.words, fix.cost ? money(fix.cost.high) : null, fix.by === 'gemini' ? 'Gemini · engine-verified' : 'engine-verified'].filter(Boolean).join(' · ')
}

// The report's best fix when the deck would offer "Apply the best fix" (backend/bulletin.py s_bottom: a verified
// fix of a preventable or partly preventable case), else null (a storm's "no fix": no button).
export function bestFixOf(report) {
  if (!report || report.no_fix || report.best_fix == null) return null
  if (report.verdict && !['preventable', 'partly'].includes(report.verdict)) return null
  const f = report.fixes?.[report.best_fix]
  if (!f?.apply || !(f.verdict === 'holds' || f.verdict === 'partly') || f.family === 'remove') return null
  return describeFix(f, Number(report.case?.mw) || 0)
}

// The briefing report for a case (cached with the presentation's; Florida's is warmed while the case holds
// still), and its best fix. While the AI proposer is still adding verified plans, ask again every few seconds (up
// to 90 s): a Gemini plan that ranks first becomes the button's fix, as it becomes the deck's.
//
// ONE poller per case, shared by everything that asks (the flip's button, Fix it, "Let Gemini fix it"): the whole
// venue shares one IP and /api/briefing allows 30 a minute, so three components must not ask three times. The
// first answer for a new case can come before the proposer has started (agentic not in yet): asked again a few
// times. A failed ask (a 429, a dropped connection) waits longer and asks again instead of ending the chain.
const POLL_MS = 5000
const POLL_RETRY_MS = 9000
const POLL_FOR_MS = 90000
const POLL_NOT_STARTED = 3 // answers with no agentic yet, before the poller stops waiting for it
const PENDING = { report: null, failed: false, done: false, polling: false }
const polls = new Map() // key -> {snap, subs, timer, t0, empty, again}

function pollFor(key) {
  const p = { snap: { ...PENDING, polling: true }, subs: new Set(), timer: 0, t0: performance.now(), empty: 0, again: null }
  polls.set(key, p)
  const b = JSON.parse(key)
  const mine = () => polls.get(key) === p
  const set = (patch) => {
    p.snap = { ...p.snap, ...patch }
    p.subs.forEach((f) => f())
  }
  const later = (ms) => {
    if (!mine()) return
    if (performance.now() - p.t0 >= POLL_FOR_MS) return set({ polling: false })
    clearTimeout(p.timer)
    p.timer = setTimeout(() => refetchReport(b).then(take, miss), ms)
  }
  const take = (r) => {
    if (!mine()) return
    const st = r?.agentic?.status
    if (!st) p.empty += 1
    const more = st === 'running' || (!st && p.empty <= POLL_NOT_STARTED)
    set({ report: r, failed: false, done: true, polling: more })
    if (more) later(POLL_MS)
  }
  const miss = () => {
    if (!mine()) return
    if (p.snap.report) return later(POLL_RETRY_MS) // keep the answer on hand and ask again, later
    set({ failed: true, done: true, polling: false })
  }
  // ask again from now (the offer's "Check again" once the 90 s ran out)
  p.again = () => {
    if (!mine()) return
    p.t0 = performance.now()
    p.empty = 0
    set({ polling: true })
    later(0)
  }
  getReport(b).then(take, miss)
  return p
}

function watchPoll(key, f) {
  if (!key) return () => {}
  const p = polls.get(key) || pollFor(key)
  p.subs.add(f)
  return () => {
    p.subs.delete(f)
    if (p.subs.size) return
    clearTimeout(p.timer)
    if (polls.get(key) === p) polls.delete(key)
  }
}

export function useBestFix(body, enabled) {
  const key = enabled && body ? JSON.stringify(cleanBody(body)) : ''
  const sub = useCallback((f) => watchPoll(key, f), [key])
  const snap = useCallback(() => (key ? polls.get(key)?.snap || PENDING : PENDING), [key])
  const s = useSyncExternalStore(sub, snap, snap)
  const report = key ? s.report : null
  return {
    report,
    fix: bestFixOf(report),
    loading: !!key && !s.done,
    failed: !!key && s.failed,
    polling: !!key && s.polling,
    recheck: () => polls.get(key)?.again?.(),
  }
}

// A plant outage's cascade (Plants tab: backend/plants.py echoes `outages`, `retire_fuels` and `removed`) is a
// different case from the map's caseBody, which has no plant fields: the briefing's best fix is for the case with
// every plant running, so the flip is not offered on it and it is never taken as the "without the fix" side.
// (The "plant back" replay has none of these: it IS the case with every plant running.)
export const plantsOut = (cascade) =>
  !!cascade && ((cascade.outages?.length || 0) > 0 || (cascade.retire_fuels?.length || 0) > 0 || (cascade.removed?.length || 0) > 0)

// ------------------------------------------------------------------ applying a case through the store's setters
const sameNums = (a, b) => a.length === b.length && a.every((x, i) => Number(x) === Number(b[i]))
const sameUps = (a, b) => {
  const ea = Object.entries(a || {})
  return ea.length === Object.keys(b || {}).length && ea.every(([id, v]) => Math.abs(Number(b[id]) - Number(v)) < 0.05)
}
const sitesOf = (list) => (list || []).map((x) => [Number(x.lat), Number(x.lon), Number(x.mw)])

// Make `full` the map's case, touching only what differs (a same-site fix keeps the camera where it is).
export function applyCase(O, full) {
  if (full.mw != null && Number(full.mw) !== Number(O.mw)) O.setMw(Number(full.mw))
  if (full.lat != null && full.lon != null && (!O.site || Math.abs(O.site.lat - full.lat) > 1e-7 || Math.abs(O.site.lon - full.lon) > 1e-7)) O.place(full.lat, full.lon)
  if (Math.abs(Number(full.load_factor ?? 1) - Number(O.loadFactor)) > 1e-9) O.setLoadFactor(Number(full.load_factor ?? 1))
  if (!sameNums([...(full.trip || [])].sort((a, b) => a - b), [...(O.trip || [])].sort((a, b) => a - b))) O.setTrip(full.trip || [])
  if (!sameUps(full.upgrades || {}, O.upgrades || {})) O.setUpgrades({ ...(full.upgrades || {}) })
  if (JSON.stringify(sitesOf(full.sites)) !== JSON.stringify(sitesOf(O.extraSites))) O.setExtraSites((full.sites || []).map((x, i) => ({ id: `fix-${i}`, lat: x.lat, lon: x.lon, mw: x.mw })))
  if (!!full.firm !== !!O.firm) O.setFirm(!!full.firm)
}

// ------------------------------------------------------------------ the three actions
/**
 * Run the case again with a fix. `fix` = describeFix(...) (or {apply, words, ...}); opts:
 *   base          the case without the fix (default: the case the map's cascade ran)
 *   baseCascade   its cascade (default: the map's, when it is this case's; else the report's replay; else fetched)
 *   report        the briefing report (its people and cost figures stand in until the base cascade is in)
 *   rate          the panel's loss rate (useLossRate): the cost of the outage the panel shows
 * Resolves to the fixed case's cascade (or null).
 */
export async function runWithFix(O, fix, { base: baseIn, baseCascade, report, rate } = {}) {
  if (!fix?.apply) return null
  const base = cleanBody(baseIn || O.caseBody)
  delete base.preset
  const full = withFix(base, fix.apply)
  const baseKey = flipKey(base)
  const fixKey = flipKey(full)
  // the map's cascade is this case's only when its body matches AND it is not a plant outage (that one has plants
  // out that the case body cannot carry: taking it as the base would compare a different case with the fix)
  const mapIsBase = flipKey(O.caseBody) === baseKey && !plantsOut(O.cascade)
  const mine = O.cascade && mapIsBase ? O.cascade : null
  const casc = baseCascade || mine || report?.replay || null // the report passed in is this case's: its replay is this cascade
  const people = casc ? cascadePeople(casc) : report ? reportPeople(report) : { hit: 0, stillOut: 0 }
  const c = report?.cost
  // the panel's loss rate prices the map's cascade: only when that cascade is the base
  const cost =
    mapIsBase && rate?.total?.high
      ? { high: rate.total.high, low: rate.total.low }
      : c?.blackout_high_usd
        ? { high: Number(c.blackout_high_usd), low: Number(c.ranges?.blackout_usd?.[0]) || 0 }
        : null
  const over = flipKey(O.caseBody) === baseKey ? (O.result?.overloaded || []).map((o) => o.id) : st.base?.key === baseKey ? st.base.over : []
  emit({
    base: { key: baseKey, body: base, cascade: casc, ...people, cost, over },
    fix: { ...fix, key: fixKey, body: full },
    fixed: st.fix?.key === fixKey ? st.fixed : null,
    status: 'running',
  })
  // "Show it without the fix" replays the original: fetch it now if nothing on hand is this case's
  if (!casc)
    runCascade(base)
      .then((cc) => st.base?.key === baseKey && emit({ base: { ...st.base, cascade: cc, ...cascadePeople(cc) } }))
      .catch(() => {})
  applyCase(O, full)
  const kept = st.fixed
  const res = await O.startCascade(full, kept ? Promise.resolve(kept) : null)
  if (st.fix?.key !== fixKey) return res
  emit({ fixed: res || st.fixed, status: res ? 'done' : 'error' })
  return res
}

/** Back to the case without the fix: straight to where its cascade settled (instant: it is kept), so the flip
 *  button is one click away again; the timeline replays the blast. */
export function showWithout(O) {
  const b = st.base
  if (!b) return
  applyCase(O, b.body)
  O.startCascade(b.body, b.cascade ? Promise.resolve(b.cascade) : null).then((c) => {
    if (!c) return
    if (!b.cascade && st.base?.key === b.key) emit({ base: { ...st.base, cascade: c, ...cascadePeople(c) } })
    if (st.base?.key === b.key) O.setStep(c.steps.length)
  })
}

/** A fix belongs to its case: when the campus moves to another site after a flip, the fix's upgrades come off
 *  (the case goes back to the upgrades it had before the fix). A new size at the same site keeps them. Mounted
 *  once, by the results column. */
export function useFixFollowsCase(O) {
  const lat = O.site?.lat
  const lon = O.site?.lon
  const { upgrades, setUpgrades } = O
  useEffect(() => {
    const f = st.fix
    if (!f || lat == null || f.body.lat == null) return
    const moved = Math.abs(lat - f.body.lat) > 1e-7 || Math.abs(lon - f.body.lon) > 1e-7
    if (!moved || !Object.keys(upgrades || {}).length || !sameUps(upgrades, f.body.upgrades || {})) return
    const before = st.base?.body?.upgrades || {}
    if (!sameUps(upgrades, before)) setUpgrades({ ...before })
    // (only the site: the upgrades are read at the moment it moved)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lat, lon])
}

/** And with the fix again (instant once it ran). */
export function showWith(O) {
  const f = st.fix
  if (!f) return
  applyCase(O, f.body)
  O.startCascade(f.body, st.fixed ? Promise.resolve(st.fixed) : null).then((res) => res && st.fix?.key === f.key && emit({ fixed: res, status: 'done' }))
}

/** Which side of the flip the map's case is on: 'fixed' | 'base' | null. */
export function flipSide(flip, caseBody) {
  if (!flip.fix) return null
  const k = flipKey(caseBody)
  if (k === flip.fix.key) return 'fixed'
  if (flip.base && k === flip.base.key) return 'base'
  return null
}
