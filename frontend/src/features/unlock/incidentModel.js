// The incident solution stage's reading of a briefing report (backend/briefing.py + solutions.py): which verified
// options to show and in what order, what each one builds or changes element by element, its typical time to build
// (backend/leadtimes.py's kinds of work), what it prevents, how often the overload happens at this size, and the
// incident's name. Formatting and ordering only: every number is the engine's (or costs.py's, leadtimes.py's).
import { fmt } from '../../geo'
import { reportPeople } from '../cost/figures'

// ------------------------------------------------------------------ small words
export const mwText = (mw) => `${fmt(mw)} MW`
const cap1 = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s)
export const noThe = (s) => String(s || '').replace(/^the /, '')
// "NORTH FORT MYERS 6" -> "North Fort Myers 6"
export const titleName = (s) => String(s || '').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
const joinWords = (xs) => (xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs.at(-1)}`)
export const pct = (v) => `${v >= 99.5 && v < 100.5 ? Number(v).toFixed(1) : Math.round(v)} %`
// "a" or "an" before a number read aloud: an 8-step, an 11-step, an 18-step, an 80-step, an 11,000
export const aN = (n) => {
  const s = String(Math.round(Math.abs(Number(n) || 0)))
  return /^8/.test(s) || (s.length % 3 === 2 && /^1[18]/.test(s)) ? 'an' : 'a'
}
// "an 18-step cascade"
export const stepsWords = (n) => `${aN(n)} ${fmt(n)}-step`

// the campuses of the case and their total size
export function campusOf(report) {
  const c = report?.case || {}
  const sites = c.sites?.length ? c.sites : c.mw ? [c] : []
  const total = sites.reduce((a, s) => a + (Number(s.mw) || 0), 0)
  return { sites, total, where: c.sub_area || sites[0]?.sub_area || '', n: sites.length }
}

// ------------------------------------------------------------------ the incident's name
// "The Fort Myers 1,500 MW incident: how to build it without the blackout"
export function stageTitle(report) {
  const c = report?.case || {}
  const { total, where, n } = campusOf(report)
  let name
  if (c.preset?.name) name = c.preset.name
  else if (n > 1) name = `The ${n}-campus ${mwText(total)} incident${where ? ` around ${where}` : ''}`
  else if (n === 1) name = `The ${where ? `${where} ` : ''}${mwText(total)} incident`
  else if (c.trip_count) name = 'The storm incident'
  else name = `The incident ${c.load_word || ''}`.trim()
  const v = report?.verdict
  const tail =
    v === 'nothing_happened'
      ? 'nothing trips, so there is nothing to fix'
      : v === 'no_fix'
        ? 'what no fix prevents, and what still helps'
        : n
          ? `how to build ${n > 1 ? 'them' : 'it'} without the blackout`
          : 'how to keep the lights on'
  return { name, tail, full: `${name}: ${tail}` }
}

// ------------------------------------------------------------------ how often the overload happens at this size
// The engine's per-level check of the full campus (the time-of-day fix: every hour it checked, over or not).
export function oftenOf(report) {
  const fixes = report?.fixes || []
  const tod = fixes.find((f) => f.family === 'time_of_day')
  let levels = (tod?.detail?.levels || []).filter((x) => x && x.level != null).map((x) => ({ level: x.level, name: x.name, over: !x.holds, overLines: x.over_lines ?? null }))
  if (!levels.length) {
    const flex = fixes.find((f) => f.family === 'flexible')
    levels = (flex?.detail?.levels || []).map((x) => ({ level: x.level, name: x.name, over: !x.full, overLines: null, runsMw: x.runs_mw }))
  }
  levels.sort((a, b) => a.level - b.level)
  if (!levels.length) return null
  const over = levels.filter((x) => x.over)
  const below = levels.filter((x) => x.level < 0.995)
  const toPeak = levels.filter((x) => x.level <= 1.005)
  const every = over.length === levels.length
  const peakOnly = over.length > 0 && below.length > 0 && !below.some((x) => x.over)
  const heatOnly = over.length > 0 && toPeak.length > 0 && !toPeak.some((x) => x.over)
  const words = !over.length
    ? 'at none of the hours checked'
    : every
      ? `at every hour checked, even ${levels[0].name}`
      : heatOnly
        ? 'only in a heat wave'
        : `at ${joinWords(over.map((x) => x.name))}${peakOnly ? ', not at quieter hours' : ''}`
  return { levels, every, peakOnly, heatOnly, none: !over.length, words }
}

// ------------------------------------------------------------------ the options, distinct kinds first
export const KIND_LABEL = {
  rule: 'An operating rule',
  targeted: 'The targeted upgrade',
  bigger: 'A wider upgrade set',
  other: 'A different upgrade set',
  onsite: 'Power on site',
  combo: 'A little smaller, plus upgrades',
  combo_deep: 'Smaller, plus upgrades',
  shrink: 'A smaller campus',
  move: 'Another site',
}

const upsOf = (f) => new Set(Object.keys(f?.apply?.upgrades || {}).map(Number))
const high = (f) => Number(f?.cost?.high) || Infinity
const FULL = 99.5

// A plan's MAIN elements: those it raises that the case overloads first (step 1 of the cascade), and any element that
// takes at least a quarter of the MVA it adds. Plans with the same main elements do the same thing (they raise the
// same lines and transformers, to other ratings, some with a small line beside them): one option, its plans variants
// (user, Sat 19:12: "3 of the options are the same? clear distinctions between the options").
const MAIN_SHARE = 0.25
export function mainOf(f, first) {
  const list = f?.detail?.list || []
  const tot = list.reduce((a, x) => a + (Number(x.added_mva) || 0), 0)
  const ids = new Set()
  for (const x of list) if (first.has(Number(x.id)) || (tot > 0 && (Number(x.added_mva) || 0) >= MAIN_SHARE * tot)) ids.add(Number(x.id))
  if (!ids.size) for (const id of upsOf(f)) ids.add(id)
  return ids
}
export const firstOf = (report) => new Set(((report?.timeline || [])[0]?.lines || []).map((x) => Number(x.id)))
const keyOf = (ids) => [...ids].sort((a, b) => a - b).join(',')
// groups in order of first appearance (the input is sorted: each group's first plan is its cheapest)
function groupBy(xs, key) {
  const m = new Map()
  for (const x of xs) {
    const k = key(x)
    if (!m.has(k)) m.set(k, [])
    m.get(k).push(x)
  }
  return [...m.values()]
}

// The verified options (verdict "holds"; "partly" ones follow, labeled) in the order the stage lists them: an operating
// rule that only steps down at the peak or in a heat wave first, then the targeted upgrade (the cheapest plan that keeps
// the whole campus, the engine's or Gemini's) and the other full-size upgrade sets, power on site, an operating rule
// that steps down at every hour, then a smaller campus with upgrades, a smaller campus, another site. "Don't build it
// here" and "change the hour" are never options (the hour is the "how often" line). Each option carries `variants`
// (its plans, cheapest first: the option's own is the first); an upgrade set's are the plans with its main elements.
export function optionsOf(report) {
  const fixes = report?.fixes || []
  const all = fixes.map((f, i) => ({ f, i })).filter(({ f }) => (f.verdict === 'holds' || f.verdict === 'partly') && f.family !== 'remove' && f.family !== 'time_of_day')
  const holds = all.filter(({ f }) => f.verdict === 'holds')
  const partly = all.filter(({ f }) => f.verdict === 'partly')
  const often = oftenOf(report)
  const first = firstOf(report)
  const out = []
  const add = (x, kind, extra = {}) =>
    out.push({ i: x.i, fix: x.f, kind, label: KIND_LABEL[kind], ...extra, variants: (extra.variants || [x]).map((v) => ({ i: v.i, fix: v.f })) })

  const flex = holds.find(({ f }) => f.family === 'flexible')
  const flexLevels = (flex?.f.detail?.levels || []).slice().sort((a, b) => a.level - b.level)
  const flexBelowFull = flexLevels.filter((x) => x.level < 0.995).every((x) => x.full) && flexLevels.some((x) => x.level < 0.995)
  const flexToPeakFull = flexLevels.filter((x) => x.level <= 1.005).every((x) => x.full) && flexLevels.some((x) => x.level > 1.005)
  const flexLight = !!flex && flexBelowFull
  if (flexLight) add(flex, 'rule', { note: flexToPeakFull ? 'Full size except in a heat wave' : 'Full size except at the peak' })

  const full = holds.filter(({ f }) => (f.family === 'upgrade' || f.family === 'agentic') && (f.kept_pct ?? 0) >= FULL).sort((a, b) => high(a.f) - high(b.f))
  const fullGroups = groupBy(full, (x) => keyOf(mainOf(x.f, first)))
  const head = fullGroups[0]?.[0]
  if (head) add(head, 'targeted', { variants: fullGroups[0] })
  for (const g of fullGroups.slice(1)) {
    const a = upsOf(head.f)
    const b = upsOf(g[0].f)
    const superset = [...a].every((id) => b.has(id)) && b.size > a.size
    add(g[0], superset ? 'bigger' : 'other', { variants: g })
  }
  const onsite = holds.find(({ f }) => f.family === 'onsite')
  if (onsite) add(onsite, 'onsite')
  if (flex && !flexLight) add(flex, 'rule', { note: often?.every ? 'Steps down at every hour checked' : 'Steps down at several hours' })
  const smallerUps = holds.filter(({ f }) => f.family === 'combo' || (f.family === 'agentic' && (f.kept_pct ?? 0) < FULL)).sort((a, b) => (b.f.kept_pct ?? 0) - (a.f.kept_pct ?? 0) || high(a.f) - high(b.f))
  for (const g of groupBy(smallerUps, (x) => `${Math.round(Number(x.f.kept_mw ?? x.f.detail?.mw) || 0)}|${keyOf(mainOf(x.f, first))}`))
    add(g[0], (g[0].f.kept_pct ?? 0) >= 85 ? 'combo' : 'combo_deep', { variants: g })
  const shrink = holds.find(({ f }) => f.family === 'shrink')
  if (shrink) add(shrink, 'shrink')
  const move = holds.find(({ f }) => f.family === 'move')
  if (move) add(move, 'move')
  for (const x of partly) {
    const kind = x.f.family === 'flexible' ? 'rule' : x.f.family === 'upgrade' || x.f.family === 'agentic' ? 'other' : x.f.family === 'combo' ? 'combo' : x.f.family
    out.push({ i: x.i, fix: x.f, kind, label: KIND_LABEL[kind] || x.f.label, partly: true, variants: [{ i: x.i, fix: x.f }] })
  }
  return out
}

// The option on screen and the plan picked among its variants: `{...option, i, fix, group}` (`group` is the option
// itself, `fix` the picked plan). Nothing picked yet: the first option's own plan.
export function pickSel(options, selI) {
  const wrap = (op, v) => ({ ...op, i: v.i, fix: v.fix, group: op, variant: v !== op.variants[0] })
  for (const op of options) {
    const v = op.variants.find((x) => x.i === selI)
    if (v) return wrap(op, v)
  }
  return options[0] ? wrap(options[0], options[0].variants[0]) : null
}

// what a plan raises in all: "3 upgrades, +2,335 MVA" (the engine's own totals: its element list is cut at 20)
export function scopeOf(fix) {
  const d = fix?.detail || {}
  const list = d.list || []
  const n = Number(d.lines) || list.length
  if (!n) return null
  const mva = d.mva != null ? Number(d.mva) : list.reduce((a, x) => a + (Number(x.added_mva) || 0), 0)
  return { n, mva, km: Number(d.km) || 0, listed: list.length }
}
export const scopeText = (s) => `${fmt(s.n)} ${s.n === 1 ? 'upgrade' : 'upgrades'}, +${fmt(s.mva)} MVA`

// ------------------------------------------------------------------ what gets built or changed, element by element
const MI_PER_KM = 1 / 1.609344
export const WORK = {
  transformer: {
    short: 'Transformer capacity',
    text: (e) =>
      `Add ${fmt(e.added)} MVA of transformer capacity alongside the one there (the low end) or replace it with a new ${fmt(e.newMva)} MVA transformer (the high end).`,
  },
  reconductor: {
    short: 'Reconductor or rebuild',
    text: (e) =>
      `Restring the line with an advanced conductor (the low end) or rebuild it on its corridor (the high end), ${e.miles != null ? `${e.miles.toLocaleString('en-US', { maximumFractionDigits: 1 })} miles` : 'its length'}${e.kv ? ` at ${fmt(e.kv)} kV` : ''}.`,
  },
  new_line: {
    short: 'New line beside it',
    text: (e) =>
      `More than doubles its rating, more than restringing gives: a new line beside it (the low end) or a new double-circuit line (the high end), ${e.miles != null ? `${e.miles.toLocaleString('en-US', { maximumFractionDigits: 1 })} miles` : 'its length'}${e.kv ? ` at ${fmt(e.kv)} kV` : ''}.`,
  },
}

// the lead-time kind of one element (backend/leadtimes.py _line_kind: past 4x its rating a new line, past 2x (or priced
// as a new line) a line more than doubled on its corridor, else a line; a transformer is a transformer)
const CORRIDOR_MAX_RATIO = 4 // backend/leadtimes.py
const RECONDUCTOR_MAX_RATIO = 2 // backend/costs.py
function leadKindOf(e) {
  if (e.transformer) return 'transformer'
  const ratio = e.oldMva > 0 ? e.newMva / e.oldMva : 1
  if (ratio > CORRIDOR_MAX_RATIO + 1e-6) return 'new_line'
  if (e.work === 'new_line' || ratio > RECONDUCTOR_MAX_RATIO + 1e-6) return 'line_doubled'
  return 'line'
}

// Every line and transformer a fix raises: its two ends (from the grid), the kind of work, before -> after, length,
// voltage, its own cost range (costs.py, priced element by element by solutions.py) and its lead-time kind.
// `branchById`/`subName` come from the store (useOverload); without them the engine's label names it.
export function elementsOf(fix, branchById = null, subName = null) {
  const list = fix?.detail?.list || []
  const items = new Map((fix?.cost?.items || []).map((it) => [Number(it.id), it]))
  return list.map((x, k) => {
    const it = items.get(Number(x.id))
    const b = branchById?.get?.(Number(x.id))
    const a = b && subName ? titleName(subName(b.from_sub)) : null
    const z = b && subName ? titleName(subName(b.to_sub)) : null
    const transformer = !!x.transformer || (b && b.from_sub === b.to_sub)
    const oldMva = Number(x.old_mva ?? it?.old_mva) || 0
    const newMva = Number(x.new_mva ?? it?.new_mva) || 0
    // the length is the fix's own (km, and the same in miles); the price may count a longer one (costs.py prices a
    // line between two substations at almost one spot as 1 mile): said beside it, never mixed into the length
    const km = Number(x.km) || 0
    const priced = it?.miles != null ? Number(it.miles) : null
    const miles = km ? km * MI_PER_KM : priced
    const work = it?.work || (transformer ? 'transformer' : oldMva > 0 && newMva > RECONDUCTOR_MAX_RATIO * oldMva ? 'new_line' : 'reconductor')
    const e = {
      n: k + 1,
      id: Number(x.id),
      transformer,
      name: transformer ? (a ? `${a} transformer` : cap1(noThe(x.label))) : a && z ? `${a} to ${z} line` : cap1(noThe(x.label)),
      ends: transformer ? (a ? [a] : null) : a && z ? [a, z] : null,
      fromSub: b?.from_sub ?? null,
      toSub: b?.to_sub ?? null,
      kv: it?.kv ?? null,
      oldMva,
      newMva,
      added: Number(x.added_mva) || Math.max(newMva - oldMva, 0),
      km: transformer ? 0 : km,
      miles: transformer ? null : miles,
      pricedMiles: !transformer && km && priced != null && priced > miles + 0.05 ? priced : null,
      work,
      low: it ? Number(it.low) : null,
      high: it ? Number(it.high) : null,
    }
    e.lead = leadKindOf(e)
    return e
  })
}

// ------------------------------------------------------------------ typical time to build
// the slowest thing an option waits for (leadtimes.py _span: the work runs in parallel, so it finishes no sooner than
// its slowest part); every option includes "connect" (the large-load study and the campus's own build)
const RANK = { connect: 0, line: 1, transformer: 2, generation: 3, line_doubled: 4, new_line: 5 }
export function leadOf(opt, elements, lead) {
  const items = lead?.items
  if (!items) return null
  const kinds = new Set(['connect'])
  if (opt.kind === 'onsite') kinds.add('generation')
  for (const e of elements || []) kinds.add(e.lead)
  const ks = [...kinds].filter((k) => items[k])
  if (!ks.length) return null
  const lo = Math.max(...ks.map((k) => items[k].lo))
  const hi = Math.max(...ks.map((k) => items[k].hi))
  const slow = ks.reduce((a, k) => {
    const A = items[a]
    const K = items[k]
    return K.hi > A.hi || (K.hi === A.hi && (K.lo > A.lo || (K.lo === A.lo && (RANK[k] ?? 0) > (RANK[a] ?? 0)))) ? k : a
  })
  const plus = ks.some((k) => items[k].plus && items[k].hi >= hi)
  const it = items[slow]
  const sources = (it.sources || []).map((id) => lead.sources?.[id]).filter(Boolean)
  return { lo, hi, plus, item: slow, label: it.label, basis: it.basis, sources, kinds: ks }
}

// "1.5–2 years", "3–5+ years"
export const yearsText = (t) => {
  const f = (x) => x.toLocaleString('en-US', { maximumFractionDigits: 1 })
  return `${f(t.lo)}–${f(t.hi)}${t.plus ? '+' : ''} years`
}

// ------------------------------------------------------------------ what an option prevents
export function preventsOf(fix, report) {
  const { hit, stillOut } = reportPeople(report)
  const cost = report?.cost || {}
  const r = cost.ranges?.blackout_usd || []
  const low = r[0] != null ? Number(r[0]) : null
  const hi = cost.blackout_high_usd != null ? Number(cost.blackout_high_usd) : r[1] != null ? Number(r[1]) : null
  const oc = fix?.outcome || null
  // what the engine found when it re-ran the case with the fix: a fix that "holds" against a storm can still leave the
  // people the storm itself cut off in the dark (report.bound: only rebuilding the downed lines reaches them)
  const left = Number(oc?.people) || 0
  const leftSteps = Number(oc?.steps) || 0
  return {
    holds: fix?.verdict === 'holds',
    all: left === 0 && leftSteps === 0,
    hit,
    stillOut,
    low,
    high: hi,
    hours: cost.outage_label?.en || null,
    left,
    leftSteps,
    cutOff: Number(report?.bound?.people) > 0,
  }
}

// "Result: no cascade, nobody loses power." / "Result: no cascade; 1,950,000 people still without power (estimate), cut
// off by the storm's damage." / "Result: 3 steps, 45,000 people without power (estimate)."
export function resultWords(p) {
  if (p.all) return 'Result: no cascade, nobody loses power.'
  const who = `${fmt(p.left)} ${p.left === 1 ? 'person' : 'people'}`
  if (!p.leftSteps) return `Result: no cascade; ${who} still without power (estimate)${p.cutOff ? ', cut off by the storm’s damage until the downed lines are rebuilt' : ''}.`
  return `Result: still ${stepsWords(p.leftSteps)} cascade, ${who} without power (estimate).`
}

// "one power-flow solve" / "a full cascade re-run": how the engine checked it
export function checkedWords(fix) {
  const by = String(fix?.detail?.checked_by || '')
  if (/cascade/.test(by)) return 'a full cascade re-run of the case with it'
  if (/per level/.test(by)) return 'a power-flow solve at each hour'
  if (/shrink run/.test(by)) return 'the same verified run as the smaller campus (the grid carries the same load)'
  if (/solve/.test(by)) return 'a power-flow solve of the case with it: no line or transformer over its rating, so nothing trips'
  return 'the engine'
}

// the areas an incident darkens, biggest first: "Naples, Cape Coral, Fort Myers and 3 more areas"
export function areasWords(report, n = 3) {
  const a = (report?.areas || []).filter((x) => x.people > 0)
  if (!a.length) return ''
  const names = a.slice(0, n).map((x) => x.area)
  const more = a.length - names.length
  return more > 0 ? `${names.join(', ')} and ${more} more ${more === 1 ? 'area' : 'areas'}` : joinWords(names)
}

export const people = (n) => `${fmt(n)} ${n === 1 ? 'person' : 'people'}`
