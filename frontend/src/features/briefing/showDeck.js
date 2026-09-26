import { compactItems, groupTrace } from '../ai/trace'
import { fmt } from '../../geo'
import { MUST, OPTION_NAME, S } from './showText'
import { people as peopleText } from './text'

// The play-by-play show's data: what the animated slides read, derived from the deck and the engine's
// report. The backend's own fields win (slide.plays, slide.options, report.solutions, fix.kept_mw ...);
// everything else is derived here from the report so the show works with either. Pure functions.

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
const bare = (label) => String(label || '').replace(/^the\s+/i, '')

// 1,075,251,158 → "$1.1B" · 381,210,556 → "$381M" · 18,337,534 → "$18M" · 41,000 → "$41K"
export function usdCompact(v) {
  const x = Number(v) || 0
  if (x >= 1e9) return `$${(x / 1e9).toFixed(1).replace(/\.0$/, '')}B`
  if (x >= 1e7) return `$${Math.round(x / 1e6)}M`
  if (x >= 1e6) return `$${(x / 1e6).toFixed(1).replace(/\.0$/, '')}M`
  if (x >= 1e3) return `$${Math.round(x / 1e3)}K`
  return `$${Math.round(x)}`
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
  const ev = report?.event || {}
  return {
    cost: cost != null ? Number(cost) : null,
    hours: hours != null ? Number(hours) : null,
    range: range ? { low: Number(range[0]), high: Number(range[1]) } : null,
    people: Number(ev.people) || null,
    label: slide?.big2?.display || null,
  }
}

// ------------------------------------------------------------------ the plays (the chain, one card each)
// {n, kind: 'transformer'|'line'|'storm', labels {en, es}, loading_pct, people_hit (added by this play), people_total,
//  mw_total, lines_total, areas [names], hospitals (added), hospitals_total, say {en, es}, more}
// The backend's slide.plays win (their label is {en, es}; people_hit there is the running total and people_delta
// what the play added); the report's timeline fills in the rest. A storm's own damage (step 0) is a play too.
// People are "people hit" (the map's counter: everyone the failed lines were feeding, estimate); each hospital is
// counted once, the first time its area goes dark.
export function playsOf(report, slide, cascade = null) {
  const tl = report?.timeline || []
  const byN = new Map(tl.map((t) => [t.n, t]))
  const steps = report?.replay?.steps || cascade?.steps || []
  const stepByN = new Map(steps.map((st) => [st.n, st]))
  const hosp = new Map((report?.hospitals?.areas || []).map((a) => [String(a.area).toLowerCase(), Number(a.count) || 0]))
  const given = Array.isArray(slide?.plays) && slide.plays.length ? slide.plays : null
  let rows = given ? given.slice() : tl.map((t) => fromTimeline(t))
  // the storm's step (n = 0): the backend's plays skip it, the timeline has it
  const storm = tl.find((t) => t.action === 'storm')
  if (storm && !rows.some((p) => p.n === storm.n)) rows = [fromTimeline(storm), ...rows]
  let prevPeople = 0
  let lines = 0
  const seen = new Set()
  let hospitalsTotal = 0
  return rows.map((p, i) => {
    const t = byN.get(p.n)
    const st = stepByN.get(p.n)
    const areas = (p.areas || (t?.newly_dark || []).map((d) => d.area) || []).filter(Boolean)
    // every area the play reached (the deck lists its first three), for counting hospitals once each
    const reached = [...areas, ...(st?.hits || []).map((h) => h.area), ...(t?.newly_dark || []).map((d) => d.area)].filter(Boolean)
    const hitNow = p.people_total ?? st?.people_hit ?? t?.people_cum
    const total = hitNow != null ? Math.max(prevPeople, Number(hitNow) || 0) : prevPeople
    const kind = p.kind === 'storm' || p.action === 'storm' || t?.action === 'storm' ? 'storm' : p.kind === 'transformer' || t?.lines?.[0]?.transformer ? 'transformer' : 'line'
    lines += kind === 'storm' ? Number(t?.storm_lines?.count || p.count) || 0 : Math.max(1, t?.lines?.length || 1)
    let hAdd = 0
    for (const a of reached) {
      const key = String(a).toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      hAdd += hosp.get(key) || 0
    }
    // the last play leaves the incident's dark areas: every hospital the report counts in them is in by now
    if (i === rows.length - 1) hAdd += Math.max(0, (Number(report?.hospitals?.count) || 0) - hospitalsTotal - hAdd)
    hospitalsTotal += hAdd
    const labels = labelsOf(p.label, t?.lines?.[0]?.label)
    const out = {
      n: p.n ?? i + 1,
      id: p.id ?? t?.lines?.[0]?.id ?? null,
      kind,
      labels,
      label: labels.en,
      loading_pct: p.loading_pct != null ? Number(p.loading_pct) : (t?.lines?.[0]?.pct_before ?? null),
      people_hit: Math.max(0, total - prevPeople),
      people_total: total,
      mw_total: t?.lost_mw_cum != null ? Number(t.lost_mw_cum) : st?.lost_mw != null ? Number(st.lost_mw) : null,
      lines_total: lines,
      areas,
      hospitals: hAdd,
      hospitals_total: hospitalsTotal,
      more: Math.max(0, (t?.lines?.length || 1) - 1),
      count: t?.storm_lines?.count || p.count || 0,
      why: (t?.why || [])[0] || null,
      dark: Number(p.dark) || (st?.dark_subs?.length ?? 0),
    }
    out.say = { en: sayPlay(out, i, 'en'), es: sayPlay(out, i, 'es') }
    prevPeople = total
    return out
  })
}

// a label is a string (the timeline, English) or {en, es} (the deck's plays); on a card it stands alone:
// "North Fort Myers 6 transformer", "Transformador de North Fort Myers 6"
const bareEs = (label) => {
  const x = String(label || '').replace(/^(la|el|los|las)\s+/i, '')
  return x.charAt(0).toUpperCase() + x.slice(1)
}
function labelsOf(given, fallback) {
  if (given && typeof given === 'object') return { en: bare(given.en || given.es || fallback), es: bareEs(given.es || esLabel(bare(given.en || fallback))) }
  const en = bare(given || fallback)
  return { en, es: bareEs(esLabel(en)) }
}
// mid-sentence in Spanish: "línea de A a B"
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

function fromTimeline(t) {
  const l = t.lines?.[0]
  return { n: t.n, kind: t.action === 'storm' ? 'storm' : l?.transformer ? 'transformer' : 'line', label: l?.label, count: t.storm_lines?.count }
}

function sayPlay(p, i, lang) {
  const es = lang === 'es'
  const pct = p.loading_pct != null ? Math.round(p.loading_pct) : null
  const label = es ? lowerFirst(p.labels.es || p.labels.en) : p.labels.en
  if (p.kind === 'storm') {
    let s = es ? `La tormenta corta ${fmt(p.count)} líneas antes de que falle cualquier otra cosa.` : `The storm cuts ${fmt(p.count)} lines before anything else fails.`
    if (p.people_hit > 0) s += es ? ` ${peopleText(p.people_hit, lang)} personas afectadas (estimación).` : ` ${peopleText(p.people_hit, lang)} people hit (estimate).`
    return s
  }
  const first = i === 0 || (i === 1 && p.n === 1)
  const what = p.kind === 'transformer' ? (es ? 'Transformador caído' : 'Transformer down') : es ? 'Línea caída' : 'Line down'
  let s = es
    ? `${first ? 'Primero en caer' : what}: ${label}${pct ? ` al ${pct}% de su límite` : ''}.`
    : `${first ? 'First to go' : what}: ${label}${pct ? ` at ${pct}% of its limit` : ''}.`
  if (p.why) {
    const onto = esLabel(bare(p.why.label))
    const to = onto.startsWith('los ') ? `a ${onto}` : onto.startsWith('transformador') ? `al ${onto}` : `a la ${onto}`
    s += es ? ` Su carga pasa ${to} (${Math.round(p.why.pct_after)}%).` : ` Its load moves onto ${bare(p.why.label)} (${Math.round(p.why.pct_after)}%).`
  }
  if (p.people_hit > 0 && p.areas.length) {
    const rest = p.areas.length - 1
    s += es
      ? ` ${p.areas[0]}${rest > 0 ? ` y ${rest} ${rest === 1 ? 'zona más' : 'zonas más'}` : ''}: +${fmt(p.people_hit)} personas afectadas.`
      : ` ${p.areas[0]}${rest > 0 ? ` and ${rest} more ${rest === 1 ? 'area' : 'areas'}` : ''}: +${fmt(p.people_hit)} people hit.`
  }
  if (p.dark > 0) s += es ? ` ${p.dark} ${p.dark === 1 ? 'subestación queda' : 'subestaciones quedan'} a oscuras.` : ` ${p.dark} ${p.dark === 1 ? 'substation goes' : 'substations go'} dark.`
  if (p.hospitals > 0) s += es ? ` ${p.hospitals} ${p.hospitals === 1 ? 'hospital pasa' : 'hospitales pasan'} a energía de respaldo.` : ` ${p.hospitals} ${p.hospitals === 1 ? 'hospital goes' : 'hospitals go'} to backup power.`
  return s
}

// the scoreboard after play `k` (k plays have landed; 0 = before any)
export function scoreAt(plays, k) {
  const p = k > 0 ? plays[Math.min(k, plays.length) - 1] : null
  return {
    people: p ? p.people_total : 0,
    mw: p ? (p.mw_total ?? 0) : 0,
    lines: p ? p.lines_total : 0,
    hospitals: p ? p.hospitals_total : 0,
  }
}

// ------------------------------------------------------------------ the options (one beat each)
export function optionsOf(report, slide) {
  const fixes = report?.fixes || []
  const orig = Number(report?.case?.mw) || 0
  if (Array.isArray(slide?.options) && slide.options.length) return slide.options.map((o, k) => normOption(o, fixes[o.fix], orig, report, k))
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
  const lines = (o.lines || d.list || []).map((l) => ({ id: l.id, label: l.label, old_mva: l.old_mva, new_mva: l.new_mva, transformer: !!l.transformer }))
  let cost = o.cost !== undefined ? o.cost : f.cost !== undefined ? f.cost : null
  if (!cost && (fam === 'upgrade' || fam === 'combo') && report?.cost?.ranges?.upgrade_usd && fam === 'upgrade') {
    const r = report.cost.ranges.upgrade_usd
    cost = { low: Number(r[0]), high: Number(r[1]) }
  }
  return {
    k,
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
    town: d.sites?.[0]?.town || null,
    site: d.sites?.[0] ? { lat: d.sites[0].lat, lon: d.sites[0].lon } : null,
    from_mw: orig,
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

// how long one option's beat runs (ms): the headline, the "you have to" lines one by one, the green reveal
// the solutions beat's clock (ms): the headline and the options at a glance, then per option its name, the
// "you have to" lines one by one, the re-run counting down, the result (cost, kept, verified) held to be read
export const SOL = { intro: 2600, lineAt: 1100, lineStep: 600, run: 1500, read: 1700 }
export const mustRows = (o, lang = 'en') => o.must?.[lang] || o.must?.en || []
// when the green reveal starts inside an option's beat
export const greenAt = (o, lang = 'en') => SOL.lineAt + SOL.lineStep * Math.min(mustRows(o, lang).length, 7) + 300
export const optionBeatMs = (o, lang = 'en') => greenAt(o, lang) + SOL.run + SOL.read

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

// Adds the play-by-play beats the deck lacks: the chain (from the report's timeline) right after the toll,
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
    put(SYN('chain', 'chain', 'How it unfolded, play by play', 'Cómo ocurrió, jugada a jugada', { map }), 'toll', null)
  }
  // the pause names a problem only when there is one (a calm case's "fix" slide is the room left at the site)
  const lost = deck.verdict !== 'nothing_happened' && (report ? Number(report.event?.people) > 0 || !!report.no_fix : true)
  if (!has('problem') && lost && (has('fix') || has('no_fix'))) {
    put(SYN('problem', 'problem', S.en.theProblem, S.es.theProblem), null, has('fix') ? 'fix' : 'no_fix')
  }
  return { ...deck, slides, short }
}

// ------------------------------------------------------------------ pacing: the least time a slide stays up (ms)
// `options` = optionsOf(report, the deck's fix slide): the beats the fix slide and the bottom line share.
export function dwellMs(slide, report, lang = 'en', options = []) {
  const kind = slide?.kind || slide?.id
  if (kind === 'toll') return 9500
  if (kind === 'chain') return 3800 + playsOf(report, slide).length * 1550
  if (kind === 'problem') return 4600
  if (kind === 'fix') return options.length ? SOL.intro + options.reduce((n, o) => n + optionBeatMs(o, lang), 0) + aiBeatMs(aiTraceOf(slide)) : 0
  if (kind === 'no_fix') return 8000
  if (kind === 'bottom_line') return options.length ? 4200 + options.length * 1200 : 8500
  if (kind === 'areas') return 6500
  if (kind === 'cause') return 7000
  return 0
}

// ------------------------------------------------------------------ the ticker
export function tickerItems(report, deck, lang) {
  const s = S[lang]
  const es = lang === 'es'
  const ev = report?.event || {}
  const items = [s.simTicker]
  const p = (n) => peopleText(n, lang)
  if (ev.people) items.push(es ? `${p(ev.people)} personas sin luz al final (estimación)` : `${p(ev.people)} people without power at the end (estimate)`)
  if (ev.peak_people && ev.peak_people > ev.people) items.push(es ? `Pico: ${p(ev.peak_people)} personas en el peor paso (estimación)` : `Peak: ${p(ev.peak_people)} people at the worst step (estimate)`)
  if (ev.lost_mw) items.push(es ? `${fmt(ev.lost_mw)} MW de carga perdidos (estimación)` : `${fmt(ev.lost_mw)} MW of load lost (estimate)`)
  if (ev.steps) items.push(es ? `${ev.steps} pasos de cascada` : `${ev.steps} cascade steps`)
  const rc = report?.root_cause
  if (rc?.line && rc.pct_with) items.push(es ? `Primera en fallar: ${bare(rc.line.label)} al ${Math.round(rc.pct_with)}% de su límite` : `First to fail: ${bare(rc.line.label)} at ${Math.round(rc.pct_with)}% of its limit`)
  if (rc?.campus_share_pct) items.push(es ? `El centro de datos es el ${Math.round(rc.campus_share_pct)}% del flujo de esa línea` : `The data center is ${Math.round(rc.campus_share_pct)}% of the flow on that line`)
  const hos = report?.hospitals
  if (hos?.count) items.push(es ? `${hos.count} hospitales en zonas sin luz (estimación, energía de respaldo)` : `${hos.count} hospitals in the dark areas (estimate; on backup power)`)
  const areas = (report?.areas || []).slice(0, 3)
  if (areas.length) items.push(areas.map((a) => `${a.area} ${fmt(a.people)}`).join(' · ') + (es ? ' personas (estimación)' : ' people (estimate)'))
  const c = report?.cost
  if (c?.ranges?.blackout_usd) items.push(es ? `Costo esperado: ${usdCompact(c.ranges.blackout_usd[0])} a ${usdCompact(c.ranges.blackout_usd[1])} (estimación)` : `Expected cost: ${usdCompact(c.ranges.blackout_usd[0])} to ${usdCompact(c.ranges.blackout_usd[1])} (estimate)`)
  if (c?.outage_label?.[lang]) items.push(es ? `Tiempo sin luz: ${c.outage_label.es} (estimación)` : `Time without power: ${c.outage_label.en} (estimate)`)
  const fixSlide = (deck?.slides || []).find((x) => (x.kind || x.id) === 'fix')
  for (const o of report?.no_fix ? [] : optionsOf(report, fixSlide).slice(0, 4)) {
    const tag = o.by === 'gemini' ? s.verifiedAI : s.verifiedEngine
    const cost = o.cost?.high ? ` · ${es ? 'hasta' : 'up to'} ${usdCompact(o.cost.high)}` : ''
    items.push(`${o.name[lang]} · ${o.kept_pct >= 99.5 ? s.fullSize.toLowerCase() : `${Math.round(o.kept_pct)}% ${s.kept}`}${cost} · ${tag}`)
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
  const keep = o.kept_pct >= 99.5 ? s.sayKeepAll(fmt(o.kept_mw)) : s.sayKeep(fmt(o.kept_mw), fmt(o.from_mw))
  if (!done) return s.sayPlan(k + 1, n, o.name[lang], keep)
  const cost = o.cost?.high ? `${s.costHigh(usdCompact(o.cost.high))}.` : ''
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
