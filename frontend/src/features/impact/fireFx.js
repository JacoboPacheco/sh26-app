// Fire, smoke, sparks and hot spots for the cascade replay. Canvas 2D, drawn in map units by
// shell/CascadeFX.jsx's BlastCanvas (its transform is the camera's). No React, no state: every
// particle is a pure function of the replay clock (`ms`), so pausing, scrubbing, a re-render or a
// late frame can never desync it, and there is nothing to accumulate or leak.
//
// What burns where (built once per run from the schedule, shell/cascadeSchedule.js):
//   - each failed line / transformer: an electric arc and a white flash at the instant it trips, a
//     shower of sparks, then flames with rising embers and a smoke plume drifting downwind. The
//     fire's size follows the element (higher kV = bigger), the people its tier hits, and the
//     incident-relative intensity (features/impact/intensity.js): a small cascade burns at about
//     half the size of the Florida hero's, never at nothing.
//   - lights the blast front passes: a small pop of sparks (the biggest few only)
//   - areas that lose power: a smoldering hot spot that lingers and decays (the biggest few only)
//   - hospitals whose substation goes dark: a pulsing red marker
// Caps keep it near 750 sprites a frame at most (about 60 fps on a laptop GPU, 30+ in software).
// Reduced motion: the same things as static markers (no flicker, no particles).

const TAU = Math.PI * 2
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
const clamp01 = (v) => Math.min(1, Math.max(0, v))
const lerp = (a, b, f) => a + (b - a) * f

export const AFTERGLOW_MS = 7500 // how long the smolder and smoke linger after the replay ends
const IGNITE_MS = 240 // the arc and the flash first; the flames catch a moment later
const CRACK_BURN_MS = 2600 // a crack (no one loses power) burns this long, then dies
const FLAME_TAU = 3400 // after the replay ends the flames die back over this time constant
const MAX_FLAMES = 26
const MAX_BURSTS = 40 // arcs, flashes and spark showers (the flames' own are included)
const MAX_POPS = 44
const MAX_SMOLDER = 64
const MAX_HOSPITALS = 60
// A quality governor: when frames run slow (a software renderer, a small laptop, a storm with dozens of
// fires) the flames and smoke thin out a little at a time and come back when the frames recover.
let Q = 1 // 0.4..1, the share of each fire's particles that are drawn
let lastNow = 0
let dtAvg = 16
function govern() {
  const now = performance.now()
  const dt = lastNow ? Math.min(now - lastNow, 100) : 16
  lastNow = now
  dtAvg = dtAvg * 0.92 + dt * 0.08
  if (dtAvg > 30) Q = Math.max(0.4, Q - 0.02)
  else if (dtAvg < 22) Q = Math.min(1, Q + 0.008)
}

const WIND = [0.8, -0.45] // smoke drifts toward the upper right (screen px per px of rise)

// a small seeded generator, so a run always looks the same
function rng(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
const hash = (n) => {
  const s = Math.sin(n * 12.9898) * 43758.5453
  return s - Math.floor(s)
}

/** The size of a fire (0.4..1.8; a 345 kV line in the Florida hero's biggest blow is ~1.2): the
 *  element's voltage, the people its tier hits, and the incident-relative intensity. */
export function fireSize({ kv, people, intensity, crack = false, transformer = false }) {
  const kvN = clamp01((Math.log(Math.max(kv || 100, 69)) - Math.log(69)) / (Math.log(765) - Math.log(69)))
  const pf = clamp01((Math.log10(Math.max(people || 0, 1)) - 3.5) / 2.6)
  return clamp((0.55 + 0.55 * kvN) * (0.7 + 0.5 * pf) * (0.55 + 0.6 * intensity) * (crack ? 0.7 : 1) * (transformer ? 1.05 : 1), 0.4, 1.8)
}

/**
 * Everything that burns in this replay: {emitters, pops, smolders, hospitals, flashes, any}.
 * `hospitals` (optional) is the region's list from features/hospitals ([{lat, lon, sub, in_model}]).
 */
export function buildFires({ schedule, subById, branchById, project, hospitals }) {
  const xy = (id) => {
    const s = subById.get(id)
    return s ? project(s.lon, s.lat) : null
  }
  const load = (id) => subById.get(id)?.load_mw || 0
  const cand = []
  const flashes = []
  const popCand = []
  const dark = new Map() // sub id -> the time the darkness reaches it
  let seed = 101

  schedule.tiers.forEach((tier) => {
    const I = tier.intensity ?? 0.5
    const crack = tier.kind === 'crack' || tier.kind === 'aftershock'
    if (tier.lines.length) flashes.push({ t: tier.t0, I: crack ? I * 0.4 : I })
    tier.lines.forEach((bid) => {
      const b = branchById.get(bid)
      const a = b && xy(b.from_sub)
      const z = b && xy(b.to_sub)
      if (!a || !z) return
      const transformer = b.from_sub === b.to_sub
      const kv = transformer ? subById.get(b.from_sub)?.kv_max || b.kv : b.kv
      // a storm's many lines share the tier's people, so each fire is smaller and the cluster doesn't merge into one blob
      const size = fireSize({ kv, people: (tier.people || 0) / Math.max(1, tier.lines.length), intensity: I, crack, transformer })
      cand.push({
        x: (a[0] + z[0]) / 2,
        y: (a[1] + z[1]) / 2,
        a,
        z,
        transformer,
        size,
        t0: tier.t0,
        crack,
        // a shed is a held line with customers cut, on purpose: sparks and a flash, no fire
        canBurn: tier.action !== 'shed',
        seed: (seed += 7919),
      })
    })
    tier.hits.forEach((h) =>
      h.subs.forEach((s) => {
        const p = xy(s.id)
        if (p) popCand.push({ x: p[0], y: p[1], t: s.t, load: load(s.id), I })
      }),
    )
    tier.waves.forEach((w) => w.subs.forEach((id) => !dark.has(id) && dark.set(id, w.t1)))
    tier.darken.forEach((d) => !dark.has(d.id) && dark.set(d.id, d.t))
  })

  // the biggest elements burn; the rest still arc and flash
  cand.sort((p, q) => q.size * (q.canBurn ? 1 : 0.2) - p.size * (p.canBurn ? 1 : 0.2))
  const emitters = cand.slice(0, MAX_BURSTS).map((c, i) => {
    const r = rng(c.seed)
    const flame = c.canBurn && i < MAX_FLAMES
    const n = flame ? Math.round(11 + 8 * c.size) : 0
    const arr = (len, f) => Float32Array.from({ length: len }, f)
    return {
      ...c,
      vs: Math.pow(c.size, 0.7), // what the eye sees: sizes are compressed, so a small fire is about half a big one's, not a third
      flame,
      n,
      L: arr(n, () => 420 + r() * 480),
      ph: arr(n, r),
      r1: arr(n, r),
      r2: arr(n, r),
      r3: arr(n, r),
      ne: flame ? 5 : 0,
      eL: arr(5, () => 900 + r() * 1000),
      ep: arr(5, r),
      er: arr(5, r),
      nm: flame ? 6 : 0,
      mL: arr(6, () => 2800 + r() * 1400),
      mp: arr(6, r),
      mr: arr(6, r),
      ns: Math.round(20 + 14 * c.size),
      sa: arr(44, () => r() * TAU),
      sv: arr(44, () => 90 + r() * 260),
      sl: arr(44, () => 500 + r() * 800),
      life: c.crack ? CRACK_BURN_MS : null,
      dir: c.transformer ? r() * TAU : 0,
    }
  })

  const pops = popCand
    .sort((p, q) => q.load - p.load)
    .slice(0, MAX_POPS)
    .map((p, i) => ({ ...p, seed: 900 + i * 13 }))

  const smolders = [...dark.entries()]
    .map(([id, t]) => {
      const p = xy(id)
      return p ? { x: p[0], y: p[1], t, load: load(id) } : null
    })
    .filter(Boolean)
    .sort((p, q) => q.load - p.load)
    .slice(0, MAX_SMOLDER)
    .map((s, i) => ({ ...s, size: clamp(0.55 + Math.log10(Math.max(s.load, 1)) * 0.3, 0.55, 1.4), seed: 500 + i * 17 }))

  const hosp = []
  ;(hospitals || []).forEach((h) => {
    if (!h.in_model || !dark.has(h.sub) || hosp.length >= MAX_HOSPITALS) return
    const p = project(h.lon, h.lat)
    hosp.push({ x: p[0], y: p[1], t: dark.get(h.sub) + 150, seed: hosp.length * 29 })
  })

  return { emitters, pops, smolders, hospitals: hosp, flashes, any: emitters.length + smolders.length + hosp.length > 0 }
}

// ------------------------------------------------------------------ sprites (drawn once)
let SPR = null
function sprites() {
  if (SPR) return SPR
  const make = (size, paint) => {
    const c = document.createElement('canvas')
    c.width = c.height = size
    paint(c.getContext('2d'), size)
    return c
  }
  const radial = (rgb, stops) => (g, s) => {
    const r = s / 2
    const gr = g.createRadialGradient(r, r, 0, r, r, r)
    stops.forEach(([o, a]) => gr.addColorStop(o, `rgba(${rgb}, ${a})`))
    g.fillStyle = gr
    g.fillRect(0, 0, s, s)
  }
  const mix = (p, q, f) => p.map((v, i) => Math.round(lerp(v, q[i], f)))
  const flame = Array.from({ length: 12 }, (_, i) => {
    const f = i / 11
    const c = f < 0.2 ? mix([255, 208, 104], [255, 138, 36], f / 0.2) : mix([255, 138, 36], [150, 22, 8], (f - 0.2) / 0.8)
    return make(48, radial(c.join(','), [[0, 1], [0.35, 0.55], [1, 0]]))
  })
  SPR = {
    flame,
    glow: make(96, radial('255,98,24', [[0, 0.9], [0.3, 0.42], [1, 0]])),
    white: make(96, radial('255,255,255', [[0, 1], [0.25, 0.65], [1, 0]])),
    core: make(48, radial('255,206,110', [[0, 1], [0.45, 0.5], [1, 0]])),
    ember: make(16, radial('255,214,120', [[0, 1], [0.4, 0.75], [1, 0]])),
    smoke: make(64, radial('96,88,92', [[0, 0.5], [0.5, 0.28], [1, 0]])),
    smokeWarm: make(64, radial('132,84,56', [[0, 0.5], [0.5, 0.28], [1, 0]])),
  }
  return SPR
}

function put(ctx, img, x, y, r, rx = 1, ry = 1) {
  ctx.drawImage(img, x - r * rx, y - r * ry, 2 * r * rx, 2 * r * ry)
}

/**
 * One frame of fire. `px` = map units per screen px; `o` = {total (the replay's length, ms), end
 * (when the canvas stops, ms), reduced, cw, ch (canvas size in device px), cull {a,b,c,d,e,f,w,h}
 * (the camera's matrix in css px relative to the canvas, for skipping what is off screen)}.
 */
export function drawFires(ctx, ms, px, f, o) {
  if (!f?.any || window.__noFire) return
  govern()
  const fade = clamp01((o.end - ms) / 1200)
  if (fade <= 0) return
  const S = sprites()
  const after = Math.max(0, ms - o.total)
  const burn = after > 0 ? Math.exp(-after / FLAME_TAU) : 1
  const vis = (x, y, m) => {
    const c = o.cull
    if (!c) return true
    const sx = c.a * x + c.c * y + c.e
    const sy = c.b * x + c.d * y + c.f
    return sx > -m && sx < c.w + m && sy > -m && sy < c.h + m
  }
  const still = !!o.reduced
  ctx.save()

  // smoldering hot spots: the lights that went dark, glowing dull red-orange, slowly dying
  for (const s of f.smolders) {
    const age = ms - s.t
    if (age < 0 || !vis(s.x, s.y, 40)) continue
    const a = clamp01(age / 700) * Math.exp(-age / 16000)
    const pulse = still ? 1 : 0.78 + 0.22 * Math.sin(ms * 0.0042 + s.seed)
    ctx.globalCompositeOperation = 'lighter'
    ctx.globalAlpha = 0.5 * a * pulse * fade
    put(ctx, S.glow, s.x, s.y, (9 + 9 * s.size) * px)
    ctx.globalAlpha = 0.85 * a * pulse * fade
    put(ctx, S.ember, s.x, s.y, 2.8 * px)
  }

  // smoke first (under the flames), normal blending so it darkens and dims what's under it
  if (!still) {
    ctx.globalCompositeOperation = 'source-over'
    for (const e of f.emitters) {
      if (!e.flame || !vis(e.x, e.y, 120)) continue
      const A = ampOf(e, ms, burn) * fade
      const age = ms - e.t0 - IGNITE_MS - 350
      if (A <= 0.01 || age <= 0) continue
      for (let m = 0, nm = Math.ceil(e.nm * Q); m < nm; m++) {
        const q = (age / e.mL[m] + e.mp[m]) % 1
        const drift = q * (110 + 70 * e.vs)
        const x = e.x + (WIND[0] * drift + (e.mr[m] - 0.5) * 18) * px
        const y = e.y + (WIND[1] * drift - 16 * e.vs - q * (86 * e.vs + 16)) * px
        const r = (9 + 34 * q) * Math.pow(e.vs, 0.8) * px
        ctx.globalAlpha = 0.62 * A * Math.pow(Math.sin(Math.PI * q), 0.9)
        put(ctx, q < 0.28 ? S.smokeWarm : S.smoke, x, y, r)
      }
    }
  }

  ctx.globalCompositeOperation = 'lighter'

  // flames: a heat glow on the ground (additive), then narrow tongues that rise, lick sideways, shrink and
  // cool from yellow through orange to dark red. The tongues are drawn with normal blending, the old ones
  // (cool, red) first and the young ones (hot, yellow) on top, so the colors stay separate instead of
  // adding up to white; a small yellow core and the embers are additive again.
  for (const e of f.emitters) {
    if (!e.flame || !vis(e.x, e.y, 120)) continue
    const A0 = ampOf(e, ms, burn) * fade
    const age = ms - e.t0 - IGNITE_MS
    if (A0 <= 0.01 || age <= 0) continue
    const A = A0 * clamp01(age / 800) // it catches over 0.8 s
    const sz = e.vs
    const H = 128 * sz * (0.55 + 0.45 * A) // how high the tongues reach (screen px)
    const W = 11 * sz + 2 // half the width at the base
    const fl = still ? 1 : 0.86 + 0.14 * Math.sin(ms * 0.021 + e.seed) * Math.sin(ms * 0.0093 + e.seed * 1.7)
    ctx.globalCompositeOperation = 'lighter'
    ctx.globalAlpha = clamp01(0.3 * A * fl)
    put(ctx, S.glow, e.x, e.y - 16 * sz * px, 82 * sz * px * (0.9 + 0.2 * fl))
    if (still) {
      ctx.globalCompositeOperation = 'source-over'
      ctx.globalAlpha = 0.9 * A
      put(ctx, S.flame[3], e.x, e.y - 26 * sz * px, 20 * sz * px, 0.7, 1.6)
      continue
    }
    ctx.globalCompositeOperation = 'source-over'
    for (let pass = 0; pass < 2; pass++) {
      for (let i = 0, n = Math.max(5, Math.ceil(e.n * Q)); i < n; i++) {
        const q = (age / e.L[i] + e.ph[i]) % 1
        if (q >= 0.45 !== (pass === 0)) continue
        const rise = q * H * (0.55 + 0.45 * e.r1[i])
        const sway = Math.sin(age * 0.0075 * (0.8 + e.r2[i]) + e.ph[i] * TAU) * W * 0.95 * q
        const x = e.x + ((e.r3[i] - 0.5) * 2 * W * (1 - 0.7 * q) + sway) * px
        const y = e.y - rise * px
        const r = (W * 0.8 + 3 * sz) * Math.pow(1 - q, 0.7) * (0.75 + 0.5 * e.r2[i]) * px
        ctx.globalAlpha = clamp01(A * Math.pow(1 - q, 0.9) * 0.62 * fl)
        put(ctx, S.flame[Math.min(11, (q * 12) | 0)], x, y, r, 0.68, 1.75)
      }
    }
    ctx.globalCompositeOperation = 'lighter'
    // the hot base
    ctx.globalAlpha = clamp01(0.4 * A * fl)
    put(ctx, S.core, e.x, e.y - 5 * sz * px, (8 + 5 * sz) * px, 1, 0.85)
    // embers: bright specks that lift off and drift downwind
    for (let j = 0; j < e.ne; j++) {
      const q = (age / e.eL[j] + e.ep[j]) % 1
      const x = e.x + ((e.er[j] - 0.5) * 30 * sz + WIND[0] * q * 100 * sz) * px
      const y = e.y - (14 * sz + q * (70 + 60 * e.er[j]) * sz) * px
      ctx.globalAlpha = clamp01((1 - q) * 0.95 * A)
      put(ctx, S.ember, x, y, (2.4 + 1.8 * e.er[j]) * (1 - q * 0.5) * px)
    }
  }
  ctx.globalCompositeOperation = 'lighter'

  if (!still) {
    // the instant a line trips: a flash, a jagged electric arc, a shower of sparks
    for (const e of f.emitters) {
      const age = ms - e.t0
      if (age < 0 || age > 1200 || !vis(e.x, e.y, 160)) continue
      if (age < 380) {
        const q = age / 380
        ctx.globalAlpha = 0.9 * (1 - q) * fade
        put(ctx, S.white, e.x, e.y, 84 * e.vs * px * lerp(0.4, 1, 1 - (1 - q) ** 3))
      }
      if (age < 560) {
        const q = age / 560
        const step = Math.floor(age / 45)
        const ux = e.transformer ? Math.cos(e.dir) : e.z[0] - e.a[0]
        const uy = e.transformer ? Math.sin(e.dir) : e.z[1] - e.a[1]
        const len = Math.hypot(ux, uy) || 1
        const dx = ux / len
        const dy = uy / len
        const half = e.transformer ? 60 * e.vs * px : Math.min(len / 2, (100 * e.vs + 10) * px)
        ctx.beginPath()
        const K = 10
        for (let i = 0; i <= K; i++) {
          const off = (hash(e.seed + step * 31 + i * 7) - 0.5) * 2 * 17 * e.vs * px * Math.sin((Math.PI * i) / K)
          const t = (i / K - 0.5) * 2 * half
          const x = e.x + dx * t - dy * off
          const y = e.y + dy * t + dx * off
          if (i) ctx.lineTo(x, y)
          else ctx.moveTo(x, y)
        }
        ctx.lineJoin = 'round'
        ctx.lineCap = 'round'
        ctx.globalAlpha = 1
        ctx.strokeStyle = `rgba(255, 210, 140, ${0.5 * (1 - q) * fade})`
        ctx.lineWidth = 9 * px
        ctx.stroke()
        ctx.strokeStyle = `rgba(255, 255, 255, ${0.95 * (1 - q) * fade})`
        ctx.lineWidth = 2.4 * px
        ctx.stroke()
      }
      ctx.lineCap = 'round'
      ctx.lineWidth = 2.2 * px
      for (let j = 0; j < e.ns; j++) {
        const life = e.sl[j]
        if (age >= life) continue
        const q = age / life
        const draw = (t) => [e.x + Math.cos(e.sa[j]) * e.sv[j] * t * px, e.y + (Math.sin(e.sa[j]) * e.sv[j] * t + 340 * t * t) * px]
        const [x, y] = draw(age / 1000)
        const [x0, y0] = draw(Math.max(0, age / 1000 - 0.05))
        ctx.beginPath()
        ctx.moveTo(x0, y0)
        ctx.lineTo(x, y)
        ctx.globalAlpha = 1
        ctx.strokeStyle = q < 0.25 ? `rgba(255, 255, 255, ${(1 - q) * fade})` : `rgba(255, ${Math.round(lerp(210, 90, q))}, ${Math.round(lerp(120, 24, q))}, ${(1 - q) * fade})`
        ctx.stroke()
      }
    }
    // a light the front passes pops
    for (const p of f.pops) {
      const age = ms - p.t
      if (age < 0 || age > 700 || !vis(p.x, p.y, 40)) continue
      const q = age / 700
      ctx.globalAlpha = 0.75 * (1 - q) * fade
      put(ctx, S.glow, p.x, p.y, (13 + 14 * p.I) * px * lerp(0.5, 1, q))
      for (let j = 0; j < 4; j++) {
        const a = hash(p.seed + j * 5) * TAU
        const d = (7 + 24 * q) * (0.6 + 0.6 * hash(p.seed + j)) * px
        ctx.globalAlpha = (1 - q) * 0.9 * fade
        put(ctx, S.ember, p.x + Math.cos(a) * d, p.y + Math.sin(a) * d + 8 * q * q * px, 2.2 * px)
      }
    }
  }

  // hospitals that lose power: a red cross with a ring pulsing out of it
  ctx.globalCompositeOperation = 'source-over'
  for (const h of f.hospitals) {
    const age = ms - h.t
    if (age < 0 || !vis(h.x, h.y, 30)) continue
    const on = clamp01(age / 300) * fade
    if (!still) {
      for (let k = 0; k < 2; k++) {
        const ph = ((age / 1300) + k * 0.5) % 1
        ctx.globalAlpha = 0.75 * (1 - ph) * on
        ctx.beginPath()
        ctx.arc(h.x, h.y, (5 + 17 * ph) * px, 0, TAU)
        ctx.lineWidth = 1.6 * px
        ctx.strokeStyle = '#ff3d5e'
        ctx.stroke()
      }
    }
    ctx.globalAlpha = on
    ctx.lineWidth = 3.6 * px
    ctx.strokeStyle = 'rgba(8, 4, 8, 0.9)'
    const arm = 4.4 * px
    ctx.beginPath()
    ctx.moveTo(h.x - arm, h.y)
    ctx.lineTo(h.x + arm, h.y)
    ctx.moveTo(h.x, h.y - arm)
    ctx.lineTo(h.x, h.y + arm)
    ctx.stroke()
    ctx.lineWidth = 1.9 * px
    ctx.strokeStyle = '#ff3d5e'
    ctx.stroke()
  }

  // an exposure flash over the whole map at each trip, decaying fast
  if (!still) {
    for (const fl of f.flashes) {
      const age = ms - fl.t
      if (age < 0 || age > 300) continue
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.globalCompositeOperation = 'source-over'
      ctx.globalAlpha = 0.17 * fl.I * (1 - age / 300) ** 2 * fade
      ctx.fillStyle = '#fff2ec'
      ctx.fillRect(0, 0, o.cw, o.ch)
    }
  }
  ctx.restore()
}

// a flame's strength now: 1 while it burns, dying back after the replay ends; a crack burns out
function ampOf(e, ms, burn) {
  if (ms < e.t0) return 0
  if (e.life) {
    const last = e.t0 + IGNITE_MS + e.life
    if (ms > last) return Math.max(0, 1 - (ms - last) / 900)
    return 1
  }
  return burn
}
