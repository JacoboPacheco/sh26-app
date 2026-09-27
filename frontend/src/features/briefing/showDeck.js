import { compactItems, groupTrace } from '../ai/trace'
import { fmt } from '../../geo'
import { LABEL, moneyIn, outageText, reportPeople } from '../cost/figures'
import { MUST, OPTION_NAME, S, flexWhen } from './showText'

// The show's data: what the animated slides read, derived from the deck and the engine's
// report. The backend's own fields win (slide.plays, slide.options, report.solutions, fix.kept_mw ...);
// everything else is derived here from the report so the show works with either. Pure functions.

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
const bare = (label) => String(label || '').replace(/^the\s+/i, '')

// A cost as the results panel writes it (one set of numbers, features/cost/figures.js): 1,075,251,158 →
// "$1.08 billion" · 63,952,218 → "$64 million" · 41,000 → "$41,000". `lang` 'es': "$1.08 mil millones".
export function usdCompact(v, lang = 'en') {
  return moneyIn(Number(v) || 0, lang)
}

// the same figure in a narrow cell: "$64M", "$1.08B" (the panel's precision, the unit as a letter)
export function usdShort(v) {
  return moneyIn(Number(v) || 0, 'en').replace(/ billion$/, 'B').replace(/ million$/, 'M')
}

// hours → {h, m}
export function hm(hours) {
  const total = Math.max(0, Math.round((Number(hours) || 0) * 60))
  return { h: Math.floor(total / 60), m: total % 60 }
}

// ------------------------------------------------------------------ the toll
export function tollOf(deck, report) {
  const slide = (deck?.slides || []).find((s) => (s.kind || s.id) === 'toll')
  const cost = slide?.big?.value ?? report?.cost?.blackout_high_usd ?? null
  const hours = slide?.big2?.value ?? report?.cost?.duration_h_assumed ?? null
  const range = report?.cost?.ranges?.blackout_usd || null
  // the panel's two people figures: the backend's own (slide.people) when the deck carries them, else the report's
  const rp = reportPeople(report)
  const hit = Number(slide?.people?.hit) || rp.hit
  const stillOut = Number(slide?.people?.still_out ?? rp.stillOut) || 0
  return {
    cost: cost != null ? Number(cost) : null,
    hours: hours != null ? Number(hours) : null,
    range: range ? { low: Number(range[0]), high: Number(range[1]) } : null,
    people: stillOut || null,
    hit: hit || null,
    stillOut: stillOut || null,
    label: slide?.big2?.display || null,
  }
}

// ------------------------------------------------------------------ Spanish labels (the ticker's weak-point line)
// a label is a string (the timeline, English) or {en, es} (the deck's plays); mid-sentence in Spanish: "línea de A a B"
const lowerFirst = (x) => x.charAt(0).toLowerCase() + x.slice(1)

// "North Fort Myers 6 transformer" → "transformador de North Fort Myers 6"; "A to B line" → "línea de A a B"
function esLabel(en) {
  let m = /^(\d+) (.*) transformers$/.exec(en)
  if (m) return `los ${m[1]} transformadores de ${m[2]}`
  m = /^(.*) transformers?$/.exec(en)
  if (m) return `transformador de ${m[1]}`
  m = /^(.*) to (.*) line$/.exec(en)
  if (m) return `línea de ${m[1]} a ${m[2]}`
  return en
}

// ------------------------------------------------------------------ the options (one beat each)
// DISTINCT OPTIONS (user, Sat 19:12): the backend walks through options of different kinds (kind: 'upgrade',
// 'upgrade_other', 'upgrade_cheaper', 'onsite', 'flexible', 'flexible_deep', 'closest'); the plans that raise the same elements for about
// the same price come as role 'variant' (variant_of = the walked option's fix) and are folded into it here
// (option.variants), never options of their own.
export function optionsOf(report, slide) {
  const fixes = report?.fixes || []
  const orig = Number(report?.case?.mw) || 0
  const flex = slide?.present?.flex || null
  if (Array.isArray(slide?.options) && slide.options.length) {
    const all = slide.options.map((o, k) => ({ ...normOption(o, fixes[o.fix], orig, report, k), flex: flex && o.fix === flex.fix ? flex : null }))
    const out = all.filter((o) => o.role !== 'variant')
    for (const v of all.filter((o) => o.role === 'variant')) {
      const head = out.find((o) => o.fix === v.variant_of)
      if (head) head.variants.push(v)
      else out.push({ ...v, role: 'more' }) // its option is not listed: the plan stands on its own under "More options"
    }
    out.forEach((o, k) => (o.k = k))
    return out
  }
  const order = Array.isArray(report?.solutions) && report.solutions.length ? report.solutions : rankFallback(fixes, orig)
  return order
    .map((i) => ({ i, f: fixes[i] }))
    .filter((x) => x.f && (x.f.verdict === 'holds' || x.f.verdict === 'partly'))
    .slice(0, 3)
    .map((x, k) => normOption({ fix: x.i }, x.f, orig, report, k))
}

// keep the campus's size first, then how much it costs; drop "don't build it" and the fixes that fail
function rankFallback(fixes, orig) {
  const keep = (f) => keptOf(f, orig).mw
  const skip = new Set(['remove', 'time_of_day'])
  return fixes
    .map((f, i) => ({ f, i }))
    .filter(({ f }) => f.verdict === 'holds' && !skip.has(f.family))
    .sort((a, b) => keep(b.f) - keep(a.f) || a.i - b.i)
    .map((x) => x.i)
}

function keptOf(f, orig) {
  if (f.kept_mw != null) return { mw: Number(f.kept_mw), pct: f.kept_pct != null ? Number(f.kept_pct) : orig ? Math.round((Number(f.kept_mw) / orig) * 100) : 100 }
  const d = f.detail || {}
  let mw = orig
  if (f.family === 'shrink' || f.family === 'combo' || f.family === 'flexible') mw = Number(d.mw ?? f.apply?.mw ?? orig)
  else if (f.family === 'remove') mw = 0
  return { mw, pct: orig ? clamp(Math.round((mw / orig) * 100), 0, 100) : 100 }
}

function normOption(o, f = {}, orig, report, k) {
  const fam = o.family || f.family || 'other'
  const d = f.detail || {}
  const kept = keptOf({ ...f, ...o, family: fam }, orig)
  const gen = Number(d.onsite_mw) || 0
  const nameArgs = { upgrade: [fmt(orig)], shrink: [fmt(kept.mw)], flexible: [fmt(kept.mw)], onsite: [fmt(gen)], combo: [fmt(kept.mw)] }[fam] || []
  const by = o.by || f.by || 'engine'
  const raw = o.name || { en: (OPTION_NAME.en[fam] || OPTION_NAME.en.other)(...nameArgs), es: (OPTION_NAME.es[fam] || OPTION_NAME.es.other)(...nameArgs) }
  const { name, sub } = tidyName(raw, by === 'gemini' || fam === 'agentic')
  const lines = (o.lines || d.list || []).map((l) => ({ id: l.id, label: l.label, old_mva: l.old_mva, new_mva: l.new_mva, transformer: !!l.transformer, km: l.km ?? null }))
  let cost = o.cost !== undefined ? o.cost : f.cost !== undefined ? f.cost : null
  if (!cost && (fam === 'upgrade' || fam === 'combo') && report?.cost?.ranges?.upgrade_usd && fam === 'upgrade') {
    const r = report.cost.ranges.upgrade_usd
    cost = { low: Number(r[0]), high: Number(r[1]) }
  }
  return {
    k,
    role: o.role || (k === 0 ? 'lead' : 'alt'),
    fix: o.fix ?? f.fix ?? null,
    family: fam,
    name,
    kept_mw: kept.mw,
    kept_pct: kept.pct,
    must: o.must || f.must || mustOf(fam, f, lines, orig, kept),
    cost,
    sub,
    by,
    verdict: o.verdict || f.verdict || 'holds',
    outcome: o.outcome || f.outcome || { steps: 0, people: 0 },
    lines,
    apply: o.apply || f.apply || null,
    town: o.sites?.[0]?.town || d.sites?.[0]?.town || null,
    site: o.sites?.[0]?.lat != null ? { lat: o.sites[0].lat, lon: o.sites[0].lon } : d.sites?.[0] ? { lat: d.sites[0].lat, lon: d.sites[0].lon } : null,
    from_mw: orig,
    kind: o.kind || null,
    note: o.note || null,
    gen: o.gen || (fam === 'onsite' && gen ? { onsite_mw: gen, net_mw: Number(d.net_mw) || null } : null),
    variant_of: o.variant_of ?? null,
    vs_pct: o.vs_pct ?? null,
    extra: o.extra || [],
    variants_how: o.variants_how || null, // what its variants raise next to it, in the backend's exact words
    variants: [],
    // SOLUTIONS, SIMPLE: the backend's five plain lines {what, where, cost, prevents, time} per language, the typical
    // time to build (leadtimes.py's sourced range) and the cost's source
    plain: o.plain || null,
    time: o.time || null,
    cost_source: o.cost_source || null,
  }
}

function mustOf(fam, f, lines, orig, kept) {
  const d = f.detail || {}
  const out = { en: [], es: [] }
  for (const lang of ['en', 'es']) {
    const M = MUST[lang]
    let rows = []
    if (fam === 'upgrade' || fam === 'combo') {
      if (fam === 'combo') rows.push(...M.combo(fmt(kept.mw)))
      const shown = lines.slice(0, 4)
      rows.push(...shown.map((l) => M.upgrade(l)))
      if (lines.length > shown.length) rows.push(lang === 'es' ? `y ${lines.length - shown.length} mejoras más` : `and ${lines.length - shown.length} more upgrades`)
    } else if (fam === 'shrink') rows = M.shrink(fmt(orig), fmt(kept.mw))
    else if (fam === 'move') rows = M.move(d.sites?.[0]?.town || (lang === 'es' ? 'otra ciudad' : 'another town'))
    else if (fam === 'flexible') rows = M.flexible(fmt(kept.mw))
    else if (fam === 'onsite') rows = M.onsite(fmt(d.onsite_mw || 0), fmt(d.net_mw || kept.mw))
    else if (fam === 'remove') rows = M.remove()
    else rows = [f.action || '']
    out[lang] = rows.filter(Boolean)
  }
  return out
}

// An AI plan's name comes as "Title (what it upgrades)", the title cut at 48 characters: keep whole words, and
// show "what it upgrades" as its own line. Every name: megavolt-amperes → MVA.
const STOP = new Set(['and', 'with', 'of', 'the', 'for', 'to', 'a', 'an', 'y', 'con', 'de', 'la', 'el', 'para', '&', '+', '-'])
function tidyName(raw, ai) {
  const name = {}
  const sub = {}
  for (const lang of ['en', 'es']) {
    let t = String(raw?.[lang] || raw?.en || '').replace(/megavolt-amperes|megavoltamperios|megavoltios-amperios/gi, 'MVA')
    let what = null
    const m = ai ? /^(.*?)\s*\(([^()]*)\)\s*$/.exec(t) : null
    if (m) {
      t = m[1]
      what = m[2]
      if (t.length >= 47) {
        const words = t.split(/\s+/)
        words.pop() // the word the cut went through
        while (words.length > 1 && STOP.has(words[words.length - 1].toLowerCase())) words.pop()
        t = words.join(' ')
      }
    }
    name[lang] = t.trim() || (lang === 'es' ? 'Plan de IA' : 'AI plan')
    sub[lang] = what
  }
  return { name, sub: sub.en || sub.es ? sub : null }
}

// the solutions beat's clock (ms): the headline, then per option its name, its five plain lines one by one while each
// element it upgrades lands on the map with its price, the re-run counting down, then the result, held to be read
export const SOL = { intro: 2400, lineAt: 1000, lineStep: 950, plainStep: 650, run: 1500, read: 4000 }
export const mustRows = (o, lang = 'en') => o.must?.[lang] || o.must?.en || []
// SOLUTIONS, SIMPLE (user, Sat 20:31): an option in five short lines, in this order: what gets built or changed, where,
// the cost range with its source, what it prevents, how long it typically takes. The backend writes them
// (bulletin._plain_rows); an older deck gets what its option says.
const PLAIN_KEYS = ['what', 'where', 'cost', 'prevents', 'time']
export function plainRows(o, lang = 'en') {
  const given = o?.plain?.[lang] || o?.plain?.en
  if (given) return PLAIN_KEYS.filter((k) => given[k]).map((k) => [k, given[k]])
  const s = S[lang]
  const after = Number(o?.outcome?.people) || 0
  return [
    ['what', o?.name?.[lang] || o?.name?.en || ''],
    ['cost', o?.cost?.high ? usdCompact(o.cost.high, lang) : s.costNone],
    ['prevents', after === 0 ? s.zeroOut : s.peopleOutN(fmt(after))],
  ].filter(([, v]) => v)
}
// the rows an option's beat reveals one by one: its five plain lines; an older deck, its priced elements (or the
// operating rule's load levels, or its list)
export const beatRows = (o, lang = 'en') =>
  o.plain
    ? plainRows(o, lang).length
    : o.cost?.items?.length
      ? Math.min(o.cost.items.length, 6)
      : o.flex?.levels?.length
        ? o.flex.levels.length
        : Math.min(mustRows(o, lang).length, 6)
export const rowStep = (o) => (o.plain ? SOL.plainStep : SOL.lineStep)
// when the green reveal starts inside an option's beat
export const greenAt = (o, lang = 'en') => SOL.lineAt + rowStep(o) * beatRows(o, lang) + 300
export const optionBeatMs = (o, lang = 'en') => greenAt(o, lang) + SOL.run + SOL.read
// the options the beats walk through one by one (the lead, then the alternatives); the rest go under "More options"
export const mainOptions = (options) => {
  const m = options.filter((o) => o.role !== 'more' && o.role !== 'variant')
  return m.length ? m : options.slice(0, 1)
}
export const moreOptions = (options) => options.filter((o) => o.role === 'more')
// the plans folded into an option (the same elements for about the same price): their price span, and whether they
// all came from Gemini
export function variantsOf(o) {
  const vs = o?.variants || []
  if (!vs.length) return null
  const highs = vs.map((v) => Number(v.cost?.high) || 0).filter(Boolean)
  const lows = vs.map((v) => Number(v.cost?.low) || 0).filter(Boolean)
  const head = Number(o.cost?.high) || 0
  return {
    n: vs.length,
    ai: vs.every((v) => v.by === 'gemini'),
    low: lows.length ? Math.min(...lows) : null,
    high: highs.length ? Math.max(...highs) : null,
    minHigh: highs.length ? Math.min(...highs) : null,
    pricier: !!head && highs.length === vs.length && highs.every((h) => h >= head),
    list: vs,
  }
}

// "Watch the AI work": after the options, a beat of the AI proposer's own run (the fix slide's agentic.trace), a few
// of its steps: a plan that failed, the engine's findings going back, the revision that held. Only once the run is done.
export const AI_BEAT = { intro: 1300, step: 950, hold: 2600, items: 7 }
// the run behind the fix slide (its own copy, else the deck's), once done and with a trace
export const agenticOf = (slide, deck) => {
  const ag = slide?.agentic?.trace?.length ? slide.agentic : deck?.agentic
  return ag?.status === 'done' && ag.trace?.length > 1 ? ag : null
}
// the show's beat plays only when Gemini did propose plans (a run where it didn't answer stays in the paused view)
export const aiTraceOf = (slide, deck) => {
  const ag = agenticOf(slide, deck)
  return ag && ag.asked > 0 ? ag : null
}
export const aiItemsOf = (ag) => (ag ? compactItems(groupTrace(ag.trace), AI_BEAT.items) : [])
export const aiBeatMs = (ag) => {
  const n = aiItemsOf(ag).length
  return n ? AI_BEAT.intro + n * AI_BEAT.step + AI_BEAT.hold : 0
}

// ------------------------------------------------------------------ the deck, with the show's own beats
const SYN = (id, kind, en, es, extra = {}) => ({
  id,
  kind,
  synthetic: true,
  headline: { en, es },
  lines: { en: [], es: [] },
  big: null,
  camera: { type: 'region', points: [], center: null, sub_ids: [], line_ids: [] },
  map: { mode: 'final', step_from: 0, step_to: 0, highlight_lines: [], apply: null, wave: null },
  narration: { en: [], es: [] },
  facts_used: [],
  written_by: { en: 'template', es: 'template' },
  est_s: { en: 4, es: 4 },
  ...extra,
})

// Adds the beats the deck lacks: the chain (from the report's timeline) right after the toll,
// and "the problem" slate right before the fixes (or the no-fix verdict). The backend's own slides win.
export function withShow(deck, report) {
  if (!deck?.slides?.length) return deck
  const slides = deck.slides.slice()
  const short = deck.short ? deck.short.slice() : null
  const has = (id) => slides.some((s) => s.id === id)
  const put = (slide, afterId, beforeId) => {
    let at = -1
    if (afterId) at = slides.findIndex((s) => s.id === afterId) + 1
    if (at <= 0 && beforeId) at = slides.findIndex((s) => s.id === beforeId)
    if (at < 0) return
    slides.splice(at, 0, slide)
    if (short) {
      let sAt = -1
      if (afterId && short.includes(afterId)) sAt = short.indexOf(afterId) + 1
      else if (beforeId && short.includes(beforeId)) sAt = short.indexOf(beforeId)
      if (sAt >= 0) short.splice(sAt, 0, slide.id)
    }
  }
  if (!has('chain') && (report?.timeline?.length || 0) >= 2 && has('toll')) {
    const tl = report.timeline
    const map = { mode: 'replay', step_from: tl[0].n, step_to: tl[tl.length - 1].n, highlight_lines: [], apply: null, wave: null }
    put(SYN('chain', 'chain', 'How it spread', 'Cómo se propagó', { map }), 'toll', null)
  }
  // the pause names a problem only when there is one (a calm case's "fix" slide is the room left at the site)
  const lost = deck.verdict !== 'nothing_happened' && (report ? Number(report.event?.people) > 0 || !!report.no_fix : true)
  // the weak point (the cause slide) is the pause between what happened and what fixes it: no slate of repeated totals
  if (!has('problem') && !has('cause') && lost && (has('fix') || has('no_fix'))) {
    put(SYN('problem', 'problem', S.en.theProblem, S.es.theProblem), null, has('fix') ? 'fix' : 'no_fix')
  }
  return { ...deck, slides, short }
}

// ------------------------------------------------------------------ pacing: the least time a slide stays up (ms)
// the areas beat: the camera visits each named area as it is outlined, one after another
export const AREA = { intro: 700, step: 1700, max: 5 }
// the weak point: the camera settles, the gauge shows today's loading, the new load's flow arrives and fills it past
// the rating, then the frame holds to be read
export const WEAK = { at: 900, surge: 2600, fill: 2600, total: 9800 }
// the toll: the blackout reaches outward from where it began, the counter leaping as it reaches each area
export const SPREAD_MS = 4200
// `options` = optionsOf(report, the deck's fix slide): the beats the fix slide and the bottom line share.
export function dwellMs(slide, report, lang = 'en', options = []) {
  const kind = slide?.kind || slide?.id
  if (kind === 'toll') return 9500
  if (kind === 'chain') return slide?.arc?.length ? 4000 + slide.arc.length * 1500 : 3800 // the map's replay sets its own pace (holdFor)
  if (kind === 'problem') return 4600
  if (kind === 'fix') return options.length ? SOL.intro + mainOptions(options).reduce((n, o) => n + optionBeatMs(o, lang), 0) + aiBeatMs(aiTraceOf(slide)) : 0
  if (kind === 'no_fix') return 8000
  if (kind === 'bottom_line') return options.length ? 5200 + mainOptions(options).length * 1400 : 8500
  if (kind === 'areas') return AREA.intro + Math.min(AREA.max, (report?.areas || []).filter((a) => Number(a.people) > 0).length) * AREA.step + 1200
  if (kind === 'cause') return WEAK.total
  if (kind === 'event' || kind === 'hospitals' || kind === 'cost') return 6500
  return 0
}

// ------------------------------------------------------------------ the ticker
export function tickerItems(report, deck, lang) {
  const s = S[lang]
  const es = lang === 'es'
  const ev = report?.event || {}
  const items = [s.simTicker]
  const L = LABEL[lang] || LABEL.en
  // the panel's two figures, with the panel's labels (the peak is not one of them: it is not shown)
  const { hit, stillOut } = reportPeople(report)
  if (hit) items.push(es ? `${L.hit.replace(/ \((estimate|estimación)\)$/, '')}: un estimado de ${fmt(hit)}` : `${L.hit.replace(/ \((estimate|estimación)\)$/, '')}: an estimated ${fmt(hit)}`)
  if (stillOut && stillOut < hit) items.push(es ? `${L.stillOutK.replace(/ \((estimate|estimación)\)$/, '')}: un estimado de ${fmt(stillOut)}` : `${L.stillOutK.replace(/ \((estimate|estimación)\)$/, '')}: an estimated ${fmt(stillOut)}`)
  if (ev.lost_mw) items.push(es ? `Un estimado de ${fmt(ev.lost_mw)} MW de hogares y negocios sin luz` : `An estimated ${fmt(ev.lost_mw)} MW of homes and businesses lost`)
  if (ev.steps) items.push(es ? `${ev.steps} pasos de cascada` : `${ev.steps} cascade steps`)
  const rc = report?.root_cause
  // the grid's weak point first: how loaded it already was, then with the new load; the share of any new load there
  // that takes that path (a property of the grid, not of the campus)
  if (rc?.line && rc.pct_with) {
    const before = rc.pct_without != null && (rc.cause === 'campus' || rc.cause === 'last_straw') ? Math.round(rc.pct_without) : null
    items.push(
      es
        ? `Punto débil: ${lowerFirst(esLabel(bare(rc.line.label)))}${before != null ? `, al ${before}% antes de cualquier carga nueva` : ''}, al ${Math.round(rc.pct_with)}% con ella`
        : `Weak point: ${bare(rc.line.label)}${before != null ? `, ${before}% of its limit before any new load` : ''}, ${Math.round(rc.pct_with)}% with it`,
    )
  }
  if (rc?.path_share_pct >= 5) items.push(es ? `El ${Math.round(rc.path_share_pct)}% de cualquier carga nueva aquí pasa por el punto débil` : `${Math.round(rc.path_share_pct)}% of any new load here flows through the weak point`)
  const hos = report?.hospitals
  if (hos?.count) items.push(es ? `Un estimado de ${hos.count} hospitales en zonas sin luz (se supone que con respaldo)` : `An estimated ${hos.count} hospitals in dark areas (assumed on backup)`)
  const areas = (report?.areas || []).slice(0, 3)
  // the areas' figures are the people still without power (not the people hit): say so, or they read as a third number
  if (areas.length) items.push((es ? 'Aún sin luz (un estimado): ' : 'Still without power (an estimated): ') + areas.map((a) => `${a.area} ${fmt(a.people)}`).join(' · ') + (es ? ' personas' : ' people'))
  const c = report?.cost
  const hi = Number(c?.blackout_high_usd) || Number(c?.ranges?.blackout_usd?.[1]) || 0
  if (hi) {
    const lo = Number(c?.ranges?.blackout_usd?.[0]) || 0
    const range = lo && lo < hi ? (es ? `; rango ${usdCompact(lo, lang)} a ${usdCompact(hi, lang)}` : `; range ${usdCompact(lo, lang)} to ${usdCompact(hi, lang)}`) : ''
    items.push(es ? `Costo esperado: un estimado de ${usdCompact(hi, lang)} (extremo alto${range})` : `Expected cost: an estimated ${usdCompact(hi, lang)} (high end${range})`)
  }
  if (c?.duration_h_assumed > 0) items.push(es ? `Tiempo sin luz: un estimado de ${outageText(c.duration_h_assumed, 'es')}` : `Time without power: an estimated ${outageText(c.duration_h_assumed, 'en')}`)
  const fixSlide = (deck?.slides || []).find((x) => (x.kind || x.id) === 'fix')
  for (const o of report?.no_fix ? [] : mainOptions(optionsOf(report, fixSlide)).slice(0, 3)) {
    const tag = o.by === 'gemini' ? s.verifiedAI : s.verifiedEngine
    const cost = o.cost?.high ? ` · ${es ? 'hasta' : 'up to'} ${usdCompact(o.cost.high, lang)}` : ''
    // what it keeps, as the options say it: on-site power runs the full campus (its kept_mw is the grid's share); an
    // operating rule's size is this hour's
    const keep =
      o.family === 'onsite' && o.gen?.net_mw != null
        ? es
          ? `campus completo, la red aporta ${fmt(o.gen.net_mw)} MW`
          : `full campus, the grid supplies ${fmt(o.gen.net_mw)} MW`
        : o.kept_pct >= 99.5
          ? s.fullSize.toLowerCase()
          : o.family === 'flexible'
            ? es
              ? `al ${Math.round(o.kept_pct)}% a esta hora`
              : `${Math.round(o.kept_pct)}% at this hour`
            : `${Math.round(o.kept_pct)}% ${s.kept}`
    items.push(`${o.name[lang]} · ${keep}${cost} · ${tag}`)
  }
  const ag = deck?.agentic
  if (ag?.status === 'done' && ag.asked > 0) items.push(s.aiFound(ag.asked, ag.verified ?? ag.added ?? 0))
  if (deck?.credit) items.push(deck.local?.[lang]?.credit || deck.credit)
  return items
}

// ------------------------------------------------------------------ the solutions' headline and lines
// "To build 1,500 MW at Fort Myers, you have to do one of these"
export function haveTo(report, lang = 'en') {
  const s = S[lang]
  const c = report?.case || {}
  const sites = Array.isArray(c.sites) ? c.sites : []
  const mw = Number(c.mw) || sites.reduce((n, x) => n + (Number(x.mw) || 0), 0)
  if (!mw) return s.haveToKeep
  if (sites.length > 1) return s.haveToMulti(sites.length, fmt(mw))
  return s.haveTo(fmt(mw), c.sub_area || null)
}

// what the play-by-play line says for an option beat (the captions when the show runs without a voice): the plan
// first, then, once the re-run has counted down, the result (never before the reveal)
export function optionSay(o, k, n, lang = 'en', done = false) {
  const s = S[lang]
  const of = o.from_mw ? fmt(o.from_mw) : null
  // what it keeps, as the card, the ticker and the voice say it (keepsText, tickerItems): on-site power runs the full
  // campus (its kept_mw is the grid's share); an operating rule's size is this hour's, and a deep one steps down at
  // other hours too (said once: the backend's name for it already says so)
  const deepSaid = /other hours|otras horas/i.test(o.name?.[lang] || '')
  const keep = o.flex?.peak_only
    ? s.sayFlexKeep(fmt(o.from_mw), flexWhen(o.flex, lang))
    : o.family === 'onsite' && o.gen?.net_mw != null
      ? s.sayOnsiteKeep(of, fmt(o.gen.net_mw))
      : o.family === 'flexible' && (o.kept_pct ?? 100) < 99.5
        ? (o.kind === 'flexible_deep' && !deepSaid ? s.sayDeepKeep : s.sayHourKeep)(fmt(o.kept_mw), of)
        : o.kept_pct >= 99.5
          ? s.sayKeepAll(fmt(o.kept_mw))
          : s.sayKeep(fmt(o.kept_mw), fmt(o.from_mw))
  if (!done) return s.sayPlan(k + 1, n, o.name[lang], keep)
  const cost = o.cost?.high ? `${s.costHigh(usdCompact(o.cost.high, lang))}.` : ''
  const after = Number(o.outcome?.people) || 0
  const result = after === 0 ? s.sayResult0 : s.sayResultN(fmt(after))
  return `${result} ${cost} ${o.by === 'gemini' ? s.verifiedAI : s.verifiedEngine}.`.replace(/\s+/g, ' ')
}

// ------------------------------------------------------------------ the show without a voice
// With sound off the captions are read, not heard: the chain and the solutions caption themselves, play by play
// and option by option, as they land on the map's clock and the show's own clock, instead of a long read-out of
// what the cards already show (the transcript keeps every word). With sound on the deck plays as written.
const QUIET = new Set(['chain', 'fix'])
export function quietDeck(deck) {
  if (!deck?.slides?.length) return deck
  let changed = false
  const slides = deck.slides.map((sl) => {
    if (!QUIET.has(sl.kind || sl.id) || !sl.narration || !Object.values(sl.narration).some((x) => x?.length)) return sl
    changed = true
    return { ...sl, narration: Object.fromEntries(Object.keys(sl.narration).map((lang) => [lang, []])), est_s: undefined }
  })
  return changed ? { ...deck, slides } : deck
}

// ------------------------------------------------------------------ Gemini's deck, arriving after playback started
// Its slides replace the ones not played yet (same ids, so the order never shifts); a slide already entered, and the
// show's own synthetic beats, stay as they are. The deck-level `ai` block (numbers checked) and key come with it.
export function swapUnplayed(deck, ai, played) {
  if (!deck?.slides?.length || !ai?.slides?.length) return deck
  const byId = new Map(ai.slides.map((x) => [x.id, x]))
  return {
    ...deck,
    ai: ai.ai ?? deck.ai,
    deck_key: ai.deck_key ?? deck.deck_key,
    slides: deck.slides.map((x) => (x.synthetic || played?.has(x.id) ? x : byId.get(x.id) || x)),
  }
}

// ------------------------------------------------------------------ the AI proposer's late plans
// A deck fetched again once the AI proposer is done carries its verified plans: its solutions slides replace the
// ones being shown (same ids, so the show's slide order never shifts).
const LATE = ['fix', 'bottom_line']
export function mergeSolutions(deck, fresh) {
  if (!deck?.slides?.length || !fresh?.slides?.length) return deck
  const byId = new Map(fresh.slides.filter((x) => LATE.includes(x.id)).map((x) => [x.id, x]))
  if (!byId.size) return deck
  return { ...deck, agentic: fresh.agentic ?? deck.agentic, slides: deck.slides.map((x) => byId.get(x.id) || x) }
}
