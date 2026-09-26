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

// the deck's banner / credit / disclaimer in this language (the writer puts the Spanish ones in deck.local)
export const loc = (deck, key, lang) => deck?.local?.[lang]?.[key] || deck?.[key] || ''

export function transcriptText(deck, lang) {
  const out = [loc(deck, 'banner', lang), '', deck.title?.[lang] || '', '']
  deck.slides.forEach((s, i) => {
    out.push(`${i + 1}. ${s.headline?.[lang] || ''}`)
    ;(s.narration?.[lang] || []).forEach((g) => out.push(`${g.role === 'analyst' ? T[lang].analyst : T[lang].presenter}: ${g.text}`))
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
