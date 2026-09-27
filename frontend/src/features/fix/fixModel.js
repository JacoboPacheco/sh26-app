import { useEffect, useState, useSyncExternalStore } from 'react'
import { whatIf } from '../../api'
import { fmt } from '../../geo'
import { titleCase } from '../../shell/cascadeSchedule'
import { cleanBody, getReport } from '../briefing/briefingApi'
import { getTimeToPower } from '../unlock/unlockApi'

// WHAT THE FIX CHANGES (CLAUDE.md -> Decisions -> FIX DESCRIPTORS): with a fix on the case, every element it raises in
// plain words — which line or transformer, from -> to MVA, the kind of work, its length, its cost range and a typical
// time to build — and what the campus does differently for an operating rule, on-site power or another site. Every
// number is the engine's or the report's own (detail.list, cost.items, detail.levels, the what-if solves); the only
// rule applied here is which typical build-time range a kind of work falls in (backend/leadtimes.py, the same rule).

// costs.py RECONDUCTOR_MAX_RATIO: up to twice its original rating a line is reconductored or rebuilt; past that it is
// priced as a new line beside it. leadtimes.py CORRIDOR_MAX_RATIO: past four times, it is new transmission.
const RECONDUCTOR_MAX = 2
const CORRIDOR_MAX = 4

const bare = (label) => String(label || '').replace(/^(the|el|la)\s+/i, '')
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s)

/**
 * The elements a fix raises, biggest first: [{id, xf, name, from, to, kv, kvOrig, miles, work, lead, low, high,
 * before, after}]. `from`/`to` are the element's MVA without and with the fix (the report's detail.list, else Fix it's
 * search, else the case's own rating and the fix's); `work` and the cost come from cost.items (costs.py, per element);
 * `before`/`after` are its loading in the what-if without and with the fix (% of its rating), when measured.
 *   fix       the flip's fix (flipCase.describeFix, plus `fixit` from the Fix it search)
 *   ctx       {branchById, subName, baseUps (the case's upgrades without the fix), loadsBefore {id: pct},
 *              loadsAfter (id) => pct | null}
 */
export function fixElements(fix, ctx) {
  if (!fix) return []
  const { branchById, subName, baseUps = {}, loadsBefore = {}, loadsAfter = () => null } = ctx
  const items = new Map((fix.items || []).map((it) => [Number(it.id), it]))
  const fixit = new Map((fix.fixit || []).map((u) => [Number(u.id), u]))
  // the fix's own elements, in the report's order: its list, else the Fix it search's, else the priced ones, else
  // every id it raises past the case's own rating
  let rows = fix.list?.length ? fix.list : fix.fixit?.length ? fix.fixit : fix.items?.length ? fix.items : null
  if (!rows) {
    const ups = fix.apply?.upgrades || {}
    rows = Object.entries(ups)
      .filter(([id, v]) => !(Math.abs(Number(baseUps[id] ?? NaN) - Number(v)) < 0.05))
      .map(([id, v]) => ({ id: Number(id), new_mva: Number(v) }))
  }
  const out = []
  for (const r of rows) {
    const id = Number(r.id)
    const b = branchById?.get(id)
    const it = items.get(id)
    const fx = fixit.get(id)
    const xf = b ? b.from_sub === b.to_sub : !!(r.transformer ?? fx?.transformer ?? it?.kind === 'transformer')
    const a = b ? titleCase(subName(b.from_sub)) : null
    const z = b ? titleCase(subName(b.to_sub)) : null
    const name = b ? (xf ? `${a} transformer` : `${a} → ${z}`) : cap(bare(r.label || it?.label || `line ${id}`))
    // MVA without and with the fix: the case's rating before it (detail.list, Fix it) or the original (cost.items)
    const orig = b?.rate_mva ?? null
    const from = num(r.old_mva ?? fx?.old_mva ?? baseUps[id] ?? orig ?? it?.old_mva)
    const to = num(r.new_mva ?? fx?.new_mva ?? fix.apply?.upgrades?.[id] ?? it?.new_mva)
    if (to == null) continue
    // the kind of work, priced as costs.py prices it (its `work`), else by the same rule on the original rating
    const o = num(it?.old_mva ?? orig)
    const n = num(it?.new_mva ?? to)
    const work = xf ? 'transformer' : it?.work || (o && n > RECONDUCTOR_MAX * o + 1e-6 ? 'new_line' : 'reconductor')
    const lead = xf ? 'transformer' : work !== 'new_line' ? 'line' : o && n > CORRIDOR_MAX * o + 1e-6 ? 'new_line' : 'line_doubled'
    const km = num(r.km)
    out.push({
      id,
      xf,
      name,
      from,
      to,
      kv: num(it?.kv ?? b?.kv ?? fx?.kv),
      miles: xf ? null : num(it?.miles) ?? (km ? Math.round((km / 1.609344) * 10) / 10 : null),
      work,
      lead,
      low: it ? num(it.low) : null,
      high: it ? num(it.high) : null,
      before: num(loadsBefore[id] ?? fx?.pct_before),
      after: num(loadsAfter(id) ?? fx?.pct_after),
    })
  }
  return out.sort((p, q) => (q.to - (q.from || 0)) - (p.to - (p.from || 0)))
}

function num(x) {
  const v = Number(x)
  return x == null || x === '' || !Number.isFinite(v) ? null : v
}

/** "a larger transformer", "new wire or a rebuild, 6.2 mi", "a new line beside it, 6.2 mi" */
export function workWords(el) {
  const mi = el.miles != null ? `, ${fmt1(el.miles)} mi` : ''
  if (el.xf) return 'a larger transformer'
  if (el.lead === 'new_line') return `a new transmission line${mi}`
  if (el.work === 'new_line') return `more than doubled: a new line beside it${mi}`
  return `new wire or a rebuild${mi}`
}

/** What the cost range's two ends are (costs.py's method, in plain words). */
export function rangeWords(el) {
  if (el.xf) return `low: a second unit beside it; high: one new ${fmt(Math.round(el.to))} MVA transformer`
  if (el.work === 'new_line') return 'low: a new line; high: a new double-circuit line'
  return 'low: new conductor on the same towers; high: a rebuilt line'
}

/** The same, once per kind of work, for the sources line under the list. */
export function rangeKind(el) {
  if (el.xf) return 'a transformer: a second unit beside it, or one new, larger transformer'
  if (el.work === 'new_line') return 'a line raised past twice its rating: a new line beside it, or a new double-circuit line'
  return 'a line up to twice its rating: new conductor on the same towers, or a rebuilt line'
}

/** The map label's first line: what gets built there, in two or three words. */
export function shortWork(el) {
  const mi = el.miles != null ? ` · ${fmt1(el.miles)} mi` : ''
  if (el.xf) return 'Larger transformer'
  if (el.work === 'new_line') return `New line${mi}`
  return `Stronger line${mi}`
}

export const fmt1 = (x) => Number(x).toLocaleString('en-US', { maximumFractionDigits: 1 })

// ------------------------------------------------------------------ what the campus does differently
// An operating rule, a smaller campus, on-site power or another site: the fix changes the campus, not the grid.
// [{key, text, sub}] from the report's numbers (detail.levels, detail.onsite_mw, kept_mw, detail.sites).
export function campusChanges(fix) {
  if (!fix) return []
  const d = fix.detail || {}
  const from = Number(fix.fromMw ?? d.from_mw) || null
  const kept = fix.keptMw ?? d.mw ?? fix.apply?.mw ?? null
  const fam = fix.family
  const out = []
  if (fam === 'flexible') {
    const { here, others } = flexLevels(fix)
    out.push({
      key: 'rule',
      text: `The campus steps down to ${fmt(Math.round(kept))} MW ${here?.word || 'at this hour'}${d.peak_cut_mw ? ` (${fmt(Math.round(d.peak_cut_mw))} MW less)` : ''}`,
      sub: 'An operating rule, not new equipment: the grid itself is unchanged.',
    })
    if (others.length)
      out.push({
        key: 'hours',
        text: `At other hours: ${others.map((x) => (x.full && from ? `the full ${fmt(from)} MW ${x.word || x.name}` : `${fmt(Math.round(x.runs_mw))} MW ${x.word || x.name}`)).join(' · ')}`,
        sub: 'The most it can draw then with no line over its limit (one engine solve each).',
      })
  } else if (fam === 'onsite' && d.onsite_mw) {
    out.push({ key: 'onsite', text: `${fmt(Math.round(d.onsite_mw))} MW of power made on site`, sub: `The grid supplies ${fmt(Math.round(d.net_mw || 0))} MW of the campus's ${fmt(from || 0)} MW.` })
  } else if (fam === 'move') {
    const s = (d.sites || [])[0]
    if (s) out.push({ key: 'move', text: `The campus connects at ${titleCase(s.name || s.town)} instead`, sub: `A ${s.town} substation with room for about ${fmt(Math.round(s.headroom_mw))} MW before a line goes over its limit.` })
  } else if (fam === 'time_of_day' && fix.apply?.load_factor != null) {
    out.push({ key: 'hour', text: `Runs at ${Math.round(Number(fix.apply.load_factor) * 100)} % of the peak demand`, sub: 'A quieter hour: the same campus, a less loaded grid.' })
  }
  // a smaller campus (on its own, or with the upgrades: the combo and some of Gemini's plans)
  const smaller = kept != null && from && kept < from - 0.5 && (fam === 'shrink' || fam === 'combo' || fam === 'agentic')
  if (smaller) out.unshift({ key: 'size', text: `Built at ${fmt(Math.round(kept))} MW instead of ${fmt(from)} MW`, sub: `${Math.round((100 * kept) / from)} % of the planned size.` })
  return out
}

/** A flexible campus's size at each load level the engine checked (detail.levels): the case's own hour and the rest. */
export function flexLevels(fix) {
  const levels = (fix?.detail?.levels || []).filter((x) => x && x.level != null)
  const lf = Number(fix?.body?.load_factor ?? 1)
  const here = levels.find((x) => Math.abs(Number(x.level) - lf) < 0.005) || null
  return { levels, here, others: levels.filter((x) => x !== here) }
}

// ------------------------------------------------------------------ typical time to build
// backend/leadtimes.py's ITEMS (typical ranges with their sources) come with GET /api/unlock/time-to-power, which reads
// a CACHED Strengthen study (LAZY: it never starts one). The ranges are the same for every study, state and size, so
// the first study that answers serves them; Florida's are baked (backend/demo/strengthen), so it is asked first.
let leadSnap = { status: 'idle', items: null, sources: null }
let leadAt = 0
const leadSubs = new Set()
const setLead = (s) => {
  leadSnap = s
  leadSubs.forEach((f) => f())
}
async function loadLead(region) {
  if (leadSnap.status === 'loading' || leadSnap.status === 'done') return
  if (leadSnap.status === 'failed' && Date.now() - leadAt < 60000) return
  leadAt = Date.now()
  setLead({ status: 'loading', items: null, sources: null })
  const tries = [['FL', 1000], ...(region && region !== 'FL' && region !== 'US' ? [[region, 1000]] : []), ['FL', 500], ['FL', 2000]]
  for (const [r, mw] of tries) {
    try {
      const t = await getTimeToPower({ region: r, mw, loadFactor: 1 })
      if (t?.items) return setLead({ status: 'done', items: t.items, sources: t.sources || {} })
    } catch {
      // not cached for that study: the next one
    }
  }
  setLead({ status: 'failed', items: null, sources: null })
}

/** {items, sources} of the typical build times, or null until (or unless) a study answers. */
export function useLeadTimes(enabled, region) {
  const s = useSyncExternalStore(
    (f) => {
      leadSubs.add(f)
      return () => leadSubs.delete(f)
    },
    () => leadSnap,
    () => leadSnap,
  )
  useEffect(() => {
    if (enabled) loadLead(region)
  }, [enabled, region])
  return s.status === 'done' ? s : null
}

/** "1.5–4 years", "3–5+ years" */
export function yearsWords(it) {
  if (!it) return null
  const f = (x) => Number(x).toLocaleString('en-US', { maximumFractionDigits: 1 })
  return `${f(it.lo)}–${f(it.hi)}${it.plus ? '+' : ''} years`
}

/** The slowest kind of work among the elements (the work runs side by side, so the whole is no sooner): leadtimes.py
 *  picks it the same way, by the range's end, then its start. */
export function slowest(els, items) {
  let best = null
  for (const el of els) {
    const it = items?.[el.lead]
    if (!it) continue
    if (!best || it.hi > best.it.hi || (it.hi === best.it.hi && it.lo > best.it.lo)) best = { el, it }
  }
  return best
}

// ------------------------------------------------------------------ the case without the fix, measured
// A fix that is on the case outside the flip (ActiveFix.jsx) has no "without" what-if on hand: one engine solve of the
// case without it (POST /api/grid/whatif, one sparse solve, 120/minute), kept per case so a re-render or a switch back
// asks nothing again.
const WI_KEEP = 24
const SETTLE_MS = 350 // a size slider changes the case on every step: ask once it holds still (the venue shares one IP)
const wiCache = new Map() // case key -> promise of the what-if

/** {result, failed} of the what-if of `body` while `enabled` (result null until it lands). */
export function useWhatIfOf(body, enabled) {
  const key = enabled && body ? JSON.stringify(cleanBody(body)) : ''
  const [got, setGot] = useState({ key: '', result: null, failed: false })
  useEffect(() => {
    if (!key) return undefined
    let live = true
    const go = () => {
      let p = wiCache.get(key)
      if (!p) {
        p = whatIf(JSON.parse(key))
        wiCache.set(key, p)
        p.catch(() => wiCache.get(key) === p && wiCache.delete(key)) // a failed solve is asked again next time
        if (wiCache.size > WI_KEEP) wiCache.delete(wiCache.keys().next().value)
      }
      p.then(
        (r) => live && setGot({ key, result: r, failed: false }),
        () => live && setGot({ key, result: null, failed: true }),
      )
    }
    const t = setTimeout(go, wiCache.has(key) ? 0 : SETTLE_MS)
    return () => {
      live = false
      clearTimeout(t)
    }
  }, [key])
  return got.key === key ? got : { key, result: null, failed: false }
}

/** The briefing report of `body` while `enabled`, asked ONCE the case holds still (the briefing's own cache: a report
 *  another panel already asked for costs nothing; no polling here). null until it lands, or if it fails. */
export function useCaseReport(body, enabled) {
  const key = enabled && body ? JSON.stringify(cleanBody(body)) : ''
  const [got, setGot] = useState({ key: '', report: null })
  useEffect(() => {
    if (!key) return undefined
    let live = true
    const t = setTimeout(
      () =>
        getReport(JSON.parse(key)).then(
          (r) => live && setGot({ key, report: r }),
          () => {},
        ),
      SETTLE_MS,
    )
    return () => {
      live = false
      clearTimeout(t)
    }
  }, [key])
  return got.key === key ? got.report : null
}

// ------------------------------------------------------------------ are the fix's labels on the map?
// FixLabels says so while it draws, so Fix it's own "+N MVA" labels (BestSitesLayer) never say the same thing twice.
let labelled = 0
const labelSubs = new Set()
export function markFixLabels(on) {
  labelled = Math.max(0, labelled + (on ? 1 : -1))
  labelSubs.forEach((f) => f())
}
export const useFixLabelsShown = () =>
  useSyncExternalStore(
    (f) => {
      labelSubs.add(f)
      return () => labelSubs.delete(f)
    },
    () => labelled > 0,
    () => labelled > 0,
  )

// ------------------------------------------------------------------ the element a pointer or focus is on
// The list in the results column and the labels on the map point at the same element (hover or focus a row: its
// label stands out on the map).
let aimed = null
const aimSubs = new Set()
export function setAimedElement(id) {
  if (aimed === id) return
  aimed = id
  aimSubs.forEach((f) => f())
}
export const useAimedElement = () =>
  useSyncExternalStore(
    (f) => {
      aimSubs.add(f)
      return () => aimSubs.delete(f)
    },
    () => aimed,
    () => aimed,
  )
