import { fmt } from '../../geo'
import { reportPeople } from '../cost/figures'
import { T } from './text'

// Small helpers the stage, the review card and the route share.

// The hero case: a 1,500 MW data center at Fort Myers (it snaps to the FORT MYERS 3 substation).
export const HERO = { region: 'FL', lat: 26.64, lon: -81.87, mw: 1500 }

// The store's step index that shows cascade step `n` (a storm's step 0 is the first entry).
export function stepIndexOf(cascade, n) {
  if (!cascade?.steps) return null
  const j = cascade.steps.findIndex((s) => s.n === Number(n))
  return j >= 0 ? j + 1 : null
}

// The best fix's case change, only when it was verified to hold.
export function bestApply(report) {
  if (!report || report.best_fix == null) return null
  const f = report.fixes?.[report.best_fix]
  return f && f.verdict === 'holds' ? f.apply || null : null
}

// Everything a cascade touches, as [lon, lat] points the camera can fit: the sites, the tripped lines' ends,
// the dark substations and the towns the blast reaches (the store frames a run the same way when it starts).
export function extentPoints(cascade, branchById, subPos) {
  const pts = (cascade?.sites || []).map((s) => [s.sub_lon, s.sub_lat])
  ;(cascade?.steps || []).forEach((st) => {
    ;(st.tripped || []).forEach((bid) => {
      const b = branchById.get(bid)
      if (b) pts.push(subPos(b.from_sub), subPos(b.to_sub))
    })
    ;(st.dark_subs || []).forEach((sid) => pts.push(subPos(sid)))
    ;(st.newly_affected || []).forEach(([sid]) => pts.push(subPos(sid)))
    ;(st.hits || []).forEach((h) => h.subs.forEach((sid) => pts.push(subPos(sid))))
  })
  return pts.filter(Boolean)
}

// a restoration wave's lines: all rebuilt so far (the people back are counted that way too)
export const linesOf = (w) => w.lines_total ?? (Array.isArray(w.lines) ? w.lines.length : (w.lines_count ?? w.line_count))

// every word of the deck as text: the accessible version of the captions, and the .txt download
// The case a fix is applied to: the body itself, or for a preset the case the engine expanded it into
// (its replay). null when the map can't run it (a catastrophe knocks out more lines than the grid API takes).
const MAX_TRIPS = 400 // backend/grid.py
export function applyBase(body, report) {
  let base
  if (!body?.preset) base = { ...body }
  else {
    const r = report?.replay
    if (!r) return null
    const [main, ...more] = r.sites || []
    base = {
      region: r.region || body.region,
      load_factor: r.load_factor ?? 1,
      trip: r.trip || [],
      upgrades: r.upgrades || {},
      sites: more.map((s) => ({ lat: s.sub_lat, lon: s.sub_lon, mw: s.mw })),
      ...(main ? { lat: main.sub_lat, lon: main.sub_lon, mw: main.mw } : {}),
    }
  }
  delete base.preset
  return (base.trip || []).length > MAX_TRIPS ? null : base
}

// Make `full` (a CaseIn) the map's case through the store's setters. A "don't build it here" fix
// (lat: null) removes the campus.
export function setStoreCase(O, full, delta = {}) {
  if (full.mw != null) O.setMw(full.mw)
  if (full.lat != null && full.lon != null) O.place(full.lat, full.lon)
  else if ('lat' in delta && delta.lat == null) O.clearSite()
  O.setLoadFactor(full.load_factor ?? 1)
  O.setTrip(full.trip || [])
  O.setUpgrades(full.upgrades || {})
  O.setExtraSites((full.sites || []).map((s, i) => ({ id: `brief-${i}`, ...s })))
  O.setFirm(!!full.firm)
}

// The incident's headline with the results panel's figures. The deck's event slide names them ("…: 1,266,110
// people hit (estimate)."); the engine's own headline counts only the people still out ("an estimated 783,883
// people lost power"), so where it stands in, that clause is rewritten with both figures, precisely labeled.
export function headlineOf(report, deck) {
  const ev = (deck?.slides || []).find((s) => (s.kind || s.id) === 'event')?.headline?.en
  if (ev) return ev
  const text = report?.headline?.text || ''
  const { hit, stillOut } = reportPeople(report)
  if (!text || !(hit > 0)) return text
  const both = hit > stillOut ? `${fmt(hit)} people hit, ${fmt(stillOut)} still without power when it settled (estimates)` : `${fmt(hit)} people hit (estimate)`
  return text.replace(/an estimated [\d,.]+(?: million)? people (?:lost power|without power)/, both)
}

// the deck's banner / credit / disclaimer in this language (the writer puts the Spanish ones in deck.local)
export const loc = (deck, key, lang) => deck?.local?.[lang]?.[key] || deck?.[key] || ''

// A segment's words as the transcript keeps them: REVIEW-1 (c) — the voice and the captions say "far past its limit" for
// a loading past ~300 % (a re-solve artefact), and the `raw` cue at that spot carries the model's figure, which the
// transcript prints: "far past its limit (1,344 % in the model)".
export function transcriptSeg(g, lang) {
  const raws = (g?.cues || []).filter((c) => c.name === 'raw').sort((a, b) => b.char - a.char)
  let text = g?.text || ''
  for (const c of raws) {
    const v = Number(c.value).toLocaleString('en-US')
    text = `${text.slice(0, c.char)} (${lang === 'es' ? `${v} % en el modelo` : `${v}% in the model`})${text.slice(c.char)}`
  }
  return text
}

export function transcriptText(deck, lang) {
  const out = [loc(deck, 'banner', lang), '', deck.title?.[lang] || '', '']
  deck.slides.forEach((s, i) => {
    out.push(`${i + 1}. ${s.headline?.[lang] || ''}`)
    ;(s.narration?.[lang] || []).forEach((g) => out.push(`${g.role === 'analyst' ? T[lang].analyst : T[lang].presenter}: ${transcriptSeg(g, lang)}`))
    out.push('')
  })
  out.push(loc(deck, 'credit', lang), loc(deck, 'disclaimer', lang))
  return out.join('\n')
}

// The case the cascade actually ran with: the store's case, with the fields the cascade response
// echoes back taken from the response (a mode may override them for one run, e.g. a storm's trip list).
export function bodyFor(cascade, caseBody, cascadeBody) {
  if (cascadeBody) return cascadeBody
  const body = {
    ...caseBody,
    region: cascade.region ?? caseBody.region,
    load_factor: cascade.load_factor ?? caseBody.load_factor,
    trip: cascade.trip ?? caseBody.trip,
    upgrades: cascade.upgrades ?? caseBody.upgrades,
  }
  const count = (caseBody.lat != null ? 1 : 0) + (caseBody.sites?.length || 0)
  if (Array.isArray(cascade.sites) && cascade.sites.length !== count) {
    // the run overrode the data centers: use the substations the backend snapped them to
    delete body.lat
    delete body.lon
    delete body.mw
    body.sites = cascade.sites.map((s) => ({ lat: s.sub_lat, lon: s.sub_lon, mw: s.mw }))
  }
  return body
}
