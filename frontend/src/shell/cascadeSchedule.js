// The replay's clock: an atomic bomb in slow motion, built once per run from the engine's steps
// (pure: no React). The store's step clock, the map's effects (CascadeFX), the counter
// (ImpactPanel), the towns feed and the timeline all read this one schedule, so a blast front
// lands on a town at the very moment the number leaps.
//
// A tier is one cascade step (cascade.steps[i]). Per tier:
//   SNAP   the failed line flashes white-hot and dies (the failure point flares)
//   RING   a blast front expands from the failure point in slow motion (fast at first, then
//          slowing); each hit group LANDS when the ring reaches its distance: everyone there is
//          hit at once and the counter LEAPS to the engine's running total — no creep in between
//   WAVES  on a blackout tier the darkness then runs outward along the real lines, wave by wave;
//          substations go dark as it arrives and the "in the dark" number leaps with each wave
//   HOLD   a beat before the next tier
// A tier with no hits is a CRACK (a line fails, the grid strains) or, once an area is dark, an
// AFTERSHOCK. Everything is in ms from the start of playback.

import { incidentScale, leapIntensity } from '../features/impact/intensity'

const LEAD_MS = 400 // from the top: the camera settles on the site before the first snap
const SNAP_MS = 350 // the line snaps before the blast front leaves it
// the blast front: t(km) = RING_REF_MS * (km / RING_REF_KM) ^ RING_POW — ease-out (fast, then slowing)
export const RING_REF_KM = 43
export const RING_REF_MS = 1900
export const RING_POW = 1.3
const RING_OVERSHOOT = 1.12 // the front runs a little past the last town hit, fading
const RING_MIN_KM = 12 // the smallest blast front drawn
const PULSE_KM = 16 // a crack's shock: a small, faint front
const HIT_HOLD = 600 // after a tier's last hit
const WAVE_GAP = 250 // after the last hit, before the darkness starts to run
const WAVE_KM_PER_S = 45
const WAVE_MIN = 300
const WAVE_MAX = 700
const BLACKOUT_HOLD = 650
const CRACK_MS = 650
const AFTER_MS = 450
const STORM_MS = 900
const DARKEN_MS = 450 // dark_subs that no wave reached fade out over this, nearest first
export const MAX_TOTAL = 19000 // a long cascade must not drag: compress to this
const MIN_TOTAL = 7000 // a short one is stretched toward this (at most STRETCH_MAX)
const STRETCH_MAX = 1.5
export const BIG_LEAP = 100000 // a leap this big also shakes the map

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))

/** ms after the blast front leaves the failure point until it reaches `km` (unscaled). */
export const ringMs = (km) => RING_REF_MS * Math.pow(Math.max(km, 0) / RING_REF_KM, RING_POW)

function kmBetween(a, b) {
  const toRad = Math.PI / 180
  const dLat = (b.lat - a.lat) * toRad
  const dLon = (b.lon - a.lon) * toRad * Math.cos(((a.lat + b.lat) / 2) * toRad)
  return 6371 * Math.hypot(dLat, dLon)
}

function pathKm(path, subById) {
  let d = 0
  for (let i = 1; i < path.length; i++) {
    const a = subById.get(path[i - 1])
    const b = subById.get(path[i])
    if (a && b) d += kmBetween(a, b)
  }
  return d
}

// "NAPLES 12" -> "Naples" (the store's townOf; copied to keep this module free of React imports)
export function areaName(s) {
  if (!s) return null
  if (s.area) return s.area
  return String(s.name || '')
    .replace(/\s+\d+$/, '')
    .trim()
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

/** "NORTH FORT MYERS 6" -> "North Fort Myers 6" (the synthetic substations' names are upper case). */
export const titleCase = (name) =>
  String(name || '')
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase())

/** The failure point of a step: the mean of its failed (or held) lines' end substations, as the
 *  engine measures `km` from (backend powerflow.hits). null when none is known. */
export function failurePoint(st, subById, branchById) {
  const ids = st.tripped?.length ? st.tripped : st.held_line != null ? [st.held_line] : []
  let lat = 0
  let lon = 0
  let n = 0
  ids.forEach((bid) => {
    const b = branchById.get(bid)
    if (!b) return
    ;[b.from_sub, b.to_sub].forEach((sid) => {
      const s = subById.get(sid)
      if (s) {
        lat += s.lat
        lon += s.lon
        n++
      }
    })
  })
  return n ? { lat: lat / n, lon: lon / n } : null
}

/** The area a group of substations belongs to: the one with the most load among them. */
function mainArea(subs, subById) {
  const load = new Map()
  subs.forEach((id) => {
    const s = subById.get(id)
    const a = areaName(s)
    if (a) load.set(a, (load.get(a) || 0) + Math.max(s.load_mw || 0, 0.1))
  })
  let best = null
  load.forEach((v, a) => {
    if (!best || v > best[1]) best = [a, v]
  })
  return best?.[0] || null
}

/** Who each step hits, in the order it hits them: per step, [{area, people (added to people hit),
 *  subs, km, kind: 'hit' | 'wave'}] from the engine's hit groups, plus darkness waves that reached
 *  people not hit before. Shared by the schedule and the towns feed. */
export function hitEvents(cascade, subById) {
  const steps = cascade?.steps || []
  let hit = 0
  return steps.map((st) => {
    const out = []
    ;(st.hits || []).forEach((h) => {
      out.push({ area: h.area, people: Math.max(0, h.people_hit - hit), subs: h.subs, km: h.km, kind: 'hit' })
      hit = Math.max(hit, h.people_hit)
    })
    ;(st.waves || []).forEach((w) => {
      if (w.people_hit != null && w.people_hit > hit) {
        out.push({ area: mainArea(w.subs, subById), people: w.people_hit - hit, subs: w.subs, km: null, kind: 'wave' })
        hit = w.people_hit
      }
    })
    const end = st.people_hit ?? st.people_zone ?? st.people ?? 0
    if (end > hit) {
      const subs = (st.newly_affected || []).map(([id]) => id)
      out.push({ area: mainArea(subs, subById), people: end - hit, subs, km: null, kind: 'wave' })
      hit = end
    }
    return out
  })
}

// One tier, in unscaled ms from its own start (0).
function buildTier(st, j, events, subById, branchById, state) {
  const origin = failurePoint(st, subById, branchById)
  const lines = st.tripped?.length ? st.tripped : st.held_line != null ? [st.held_line] : []
  const tier = { step: j + 1, n: st.n, action: st.action, origin, lines, ring: null, hits: [], waves: [], darken: [], leaps: [] }
  const hits = events.filter((e) => e.kind === 'hit')
  // each leap that adds people is one of this step's events, in order (the towns feed shows them)
  const towns = events.filter((e) => e.people > 0)
  let ti = 0
  const townFor = (delta) => (delta > 0 ? towns[ti++] || null : null)
  let t = SNAP_MS
  let end

  if (hits.length) {
    // the front runs a little past the farthest town it hits, then fades
    const ringKm = Math.max(RING_MIN_KM, Math.max(...hits.map((e) => e.km || 0)) * RING_OVERSHOOT + 4)
    tier.ring = { t0: SNAP_MS, dur: ringMs(ringKm), km: ringKm, faint: false }
    hits.forEach((e) => {
      const land = SNAP_MS + ringMs(e.km || 0)
      state.hit += e.people
      // each light flashes when the front passes it (never before its town lands)
      const subs = e.subs.map((id) => {
        const s = subById.get(id)
        const km = s && origin ? kmBetween(origin, s) : e.km || 0
        return { id, t: Math.max(land, SNAP_MS + ringMs(Math.min(km, ringKm))) }
      })
      tier.hits.push({ t: land, area: e.area, people: e.people, total: state.hit, km: e.km, subs })
      tier.leaps.push({ t: land, hit: state.hit, zone: state.zone, delta: e.people, zoneDelta: 0, label: e.area, step: j + 1, town: townFor(e.people) })
      t = Math.max(t, land)
    })
    end = t + HIT_HOLD
  }

  const waves = st.waves || []
  if (waves.length) {
    let w0 = hits.length ? t + WAVE_GAP : SNAP_MS
    waves.forEach((w) => {
      const km = Math.max(0, ...w.paths.map((p) => pathKm(p, subById)))
      const dur = clamp((km / WAVE_KM_PER_S) * 1000, WAVE_MIN, WAVE_MAX)
      const zone = Math.max(state.zone, w.people_zone ?? state.zone + (w.people || 0))
      const hitNow = Math.max(state.hit, w.people_hit ?? state.hit)
      const delta = hitNow - state.hit
      const zoneDelta = zone - state.zone
      tier.waves.push({ t0: w0, t1: w0 + dur, paths: w.paths, subs: w.subs, zone, hit: hitNow, hitDelta: delta, zoneDelta })
      if (delta > 0 || zoneDelta > 0) {
        const town = townFor(delta)
        tier.leaps.push({ t: w0 + dur, hit: hitNow, zone, delta, zoneDelta, label: town?.area || mainArea(w.subs, subById), step: j + 1, town })
      }
      state.hit = hitNow
      state.zone = zone
      w0 += dur
    })
    end = w0 + BLACKOUT_HOLD
  }

  if (end === undefined) {
    // no town hit: a crack (the grid strains and reroutes), an aftershock once an area is dark,
    // or a storm's own step; a faint shock ring marks where
    tier.kind = st.action === 'storm' ? 'storm' : state.zone > 0 ? 'aftershock' : 'crack'
    end = tier.kind === 'storm' ? STORM_MS : tier.kind === 'crack' ? CRACK_MS : AFTER_MS
    if (origin) tier.ring = { t0: 120, dur: end * 0.85, km: PULSE_KM, faint: true }
  } else {
    tier.kind = waves.length ? 'blackout' : 'blast'
  }

  // dark substations no wave reached (a region cut off whole): they fade out, nearest first
  const waved = new Set(waves.flatMap((w) => w.subs))
  const dark = (st.dark_subs || []).filter((id) => !waved.has(id) && !state.darkened.has(id))
  if (dark.length) {
    const pts = dark
      .map((id) => ({ id, d: origin && subById.get(id) ? kmBetween(origin, subById.get(id)) : 0 }))
      .sort((a, b) => a.d - b.d)
    const t0 = hits.length || waves.length ? end - HIT_HOLD / 2 : SNAP_MS
    pts.forEach((p, i) => tier.darken.push({ id: p.id, t: t0 + (i / pts.length) * DARKEN_MS }))
    end = Math.max(end, t0 + DARKEN_MS + 250)
  }
  waves.forEach((w) => w.subs.forEach((id) => state.darkened.add(id)))
  dark.forEach((id) => state.darkened.add(id))

  // the step's own totals win if its groups didn't account for all of it
  const hitEnd = Math.max(state.hit, st.people_hit ?? st.people_zone ?? st.people ?? 0)
  const zoneEnd = Math.max(state.zone, st.people_zone ?? st.people ?? 0)
  if (hitEnd > state.hit || zoneEnd > state.zone) {
    const town = townFor(hitEnd - state.hit)
    tier.leaps.push({ t: end - 120, hit: hitEnd, zone: zoneEnd, delta: hitEnd - state.hit, zoneDelta: zoneEnd - state.zone, label: town?.area || null, step: j + 1, town })
    state.hit = hitEnd
    state.zone = zoneEnd
  }
  tier.dur = end
  return tier
}

/**
 * The replay from step `from` (0 = the top; resuming mid-way schedules only what's ahead).
 * → {from, total, scale, start: {hit, zone}, tiers, leaps, incident}
 *   tiers[i]: {step (the store's step once it ends), kind, t0, t1, origin {lat, lon}, lines [branch ids],
 *              ring {t0, dur, km, faint}, hits [{t, area, people, total, km, subs [{id, t}]}],
 *              waves [{t0, t1, paths, subs, zone, hit, hitDelta, zoneDelta}], darken [{id, t}]}
 *   tiers[i] also: {intensity 0..1 (how hard this tier should hit, relative to the incident: features/impact/intensity.js), people}
 *   leaps[i]: {t, hit (people hit after it), zone (people in the dark after it), delta, zoneDelta, label, step, big,
 *              town (the hitEvents entry it lands, when it adds people), share (delta / the incident's final people hit),
 *              intensity (0..1 from the leap's share and the incident's size)}
 *   incident: {hit, zone (where the whole cascade ends, also when the replay resumes mid-way), scale (0.36..1)}
 * The time scale is set by the whole cascade (compressed past MAX_TOTAL, stretched when short), so a
 * replay resumed mid-way runs at the same speed as one from the top.
 */
export function buildSchedule(cascade, subById, branchById, from = 0) {
  const steps = cascade?.steps || []
  const events = hitEvents(cascade, subById)
  const state = { hit: 0, zone: 0, darkened: new Set() }
  const raw = steps.map((st, j) => buildTier(st, j, events[j], subById, branchById, state))
  const incident = { hit: state.hit, zone: state.zone, scale: incidentScale(state.hit) }
  const whole = LEAD_MS + raw.reduce((s, x) => s + x.dur, 0)
  const scale = whole > MAX_TOTAL ? MAX_TOTAL / whole : whole < MIN_TOTAL && whole > 0 ? Math.min(MIN_TOTAL / whole, STRETCH_MAX) : 1

  const f = clamp(from, 0, steps.length)
  const prev = f > 0 ? steps[f - 1] : null
  const start = { hit: prev ? (prev.people_hit ?? prev.people_zone ?? prev.people ?? 0) : 0, zone: prev ? (prev.people_zone ?? prev.people ?? 0) : 0 }
  const tiers = []
  const leaps = []
  let t = f === 0 ? LEAD_MS * scale : 0
  raw.slice(f).forEach((x) => {
    const at = (v) => t + v * scale
    const tier = {
      ...x,
      t0: t,
      t1: at(x.dur),
      ring: x.ring && { ...x.ring, t0: at(x.ring.t0), dur: x.ring.dur * scale },
      hits: x.hits.map((h) => ({ ...h, t: at(h.t), subs: h.subs.map((s) => ({ id: s.id, t: at(s.t) })) })),
      waves: x.waves.map((w) => ({ ...w, t0: at(w.t0), t1: at(w.t1) })),
      darken: x.darken.map((d) => ({ id: d.id, t: at(d.t) })),
    }
    delete tier.leaps
    delete tier.dur
    let people = 0
    let hardest = 0
    x.leaps.forEach((l) => {
      const intensity = leapIntensity(l.delta, incident.hit)
      people += Math.max(0, l.delta)
      if (l.delta > 0) hardest = Math.max(hardest, intensity)
      leaps.push({ ...l, t: at(l.t), big: l.delta >= BIG_LEAP, share: incident.hit > 0 ? Math.min(1, l.delta / incident.hit) : 1, intensity })
    })
    tier.people = people
    // a tier that hits people plays at its hardest leap; a crack or aftershock at half the incident's size
    tier.intensity = hardest || Math.max(0.28, incident.scale * 0.5)
    tiers.push(tier)
    t = tier.t1
  })
  return { from: f, total: t, scale, start, tiers, leaps, incident }
}

/** Index of the last leap at or before `ms` (-1 before the first). */
export function leapIndexAt(schedule, ms) {
  const L = schedule.leaps
  let lo = 0
  let hi = L.length - 1
  let ans = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (L[mid].t <= ms) {
      ans = mid
      lo = mid + 1
    } else hi = mid - 1
  }
  return ans
}

/** The counter `ms` into playback: the value of the last leap at or before it — it only leaps. */
export function counterAt(schedule, ms) {
  const i = leapIndexAt(schedule, ms)
  return i < 0 ? schedule.start.hit : schedule.leaps[i].hit
}

/** People in the areas that lost power, `ms` into playback (leaps with the darkness waves). */
export function zoneAt(schedule, ms) {
  const i = leapIndexAt(schedule, ms)
  return i < 0 ? schedule.start.zone : schedule.leaps[i].zone
}
