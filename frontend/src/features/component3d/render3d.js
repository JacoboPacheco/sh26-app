// A small hand-written 3D pipeline on a 2D canvas (no library): world points (meters) -> a camera
// that orbits a target -> perspective projection -> lines and filled faces drawn far to near
// (painter's order). The style is an engineering wireframe: thin pale lines on the dark panel,
// amber for heat, red only for the moment the element is lost.
//
// One element per scene (features/component3d/shots.js describes it):
//   line         a span between two towers (the tower by voltage: <200 kV a single pole, 200-299 kV an
//                H-frame, 300 kV+ a lattice tower), three conductors hanging as parabolas, the tree
//                line and the ground. Loading heats the conductors (pale -> amber) and they sag
//                (thermal expansion). 'thermal' (up to RELAY_PCT): the sag closes on a tree, a
//                flashover arcs to it, the breaker opens. 'relay' (far past the rating): the relay
//                reads the surge as a fault and opens the breaker at once; no tree contact.
//   transformer  tank, radiators, conservator, bushings and leads; the thermometer bar fills with
//                the loading, the relay trips it and the bushings go dark. No fire.
//   storm        the same span in the wind: the conductors swing, then a tree falls on the line
//                (or, on a pole line, the pole snaps) and the breaker opens. No heat.
//   shed         the held line runs hot; the feeder breaker opens on purpose, the houses beyond go
//                dark, and the line cools back under its rating. It stays in.
//
// Time is `u`: ms relative to the trip (negative = the approach, 0 = the moment the map's line
// snaps). Everything is a pure function of (element, u, approach length, camera time), so pausing,
// scrubbing or a late frame can't desync it.

import { RELAY_PCT, STATUS_TEXT } from './shots'

const TAU = Math.PI * 2
const clamp01 = (v) => Math.min(1, Math.max(0, v))
const lerp = (a, b, f) => a + (b - a) * f
const easeInOut = (f) => (f < 0.5 ? 2 * f * f : 1 - (-2 * f + 2) ** 2 / 2)
const easeOut = (f) => 1 - (1 - f) ** 3

export const BREAKER_MS = 90 // the relay opens the breaker this long after the trip ("a fraction of a second")
export const LOSS_MS = 700 // the red of the loss moment fades to dead over this
const ARC_MS = 320
const COOL_TAU = 2600 // a de-energized conductor cools and lifts back
const FALL_MS = 520 // a storm's tree or pole comes down over this, landing at the trip
const SHED_COOL_MS = 1300 // after the feeder opens, the held line cools to its new loading

// ------------------------------------------------------------------ physics, told simply
// Conductor temperature rises with the square of the current: rated loading = 100 °C in 35 °C air.
// Sag from thermal expansion of the conductor (aluminum-steel, 19e-6 per °C), on a parabola:
// length = span + 8 D² / (3 span). EXAG exaggerates the expansion so the change reads in a small
// drawing (a drawing convention; the direction and the cause are the physics).
const T_AMB = 35
const T_RATED = 100
const ALPHA = 19e-6
const tempOf = (pct) => T_AMB + (T_RATED - T_AMB) * (Math.max(0, pct) / 100) ** 2
function sagAt(S, D0, T, exag) {
  const L0 = S + (8 * D0 * D0) / (3 * S)
  const L = L0 * (1 + ALPHA * exag * (T - T_AMB))
  return Math.sqrt(Math.max(0, (3 * S * (L - S)) / 8))
}

// ------------------------------------------------------------------ palette (the app's tokens)
function rgbOf(c, fb) {
  const m = /^#([0-9a-f]{6})$/i.exec(String(c || '').trim())
  if (!m) return fb
  const n = parseInt(m[1], 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}
export function readPalette() {
  let cs = null
  try {
    cs = getComputedStyle(document.documentElement)
  } catch {
    cs = null
  }
  const v = (name, fb) => rgbOf(cs?.getPropertyValue(name), fb)
  return {
    bg: v('--surface', [14, 24, 41]),
    ink: v('--ink', [230, 237, 247]),
    ink2: v('--ink-2', [154, 171, 196]),
    lit: v('--lit', [214, 226, 241]),
    strain: v('--strain', [255, 176, 58]),
    loss: v('--overload', [255, 61, 94]),
    dead: [58, 69, 86],
  }
}
const mix = (p, q, f) => [lerp(p[0], q[0], f), lerp(p[1], q[1], f), lerp(p[2], q[2], f)]
const css = (c, a = 1) => `rgba(${c[0] | 0}, ${c[1] | 0}, ${c[2] | 0}, ${a.toFixed(3)})`

// ------------------------------------------------------------------ camera + frame
function camera(target, yaw, pitch, dist, fovY, W, H) {
  const cp = Math.cos(pitch)
  const C = [target[0] + dist * cp * Math.sin(yaw), target[1] + dist * Math.sin(pitch), target[2] - dist * cp * Math.cos(yaw)]
  let F = [target[0] - C[0], target[1] - C[1], target[2] - C[2]]
  const fl = Math.hypot(F[0], F[1], F[2]) || 1
  F = [F[0] / fl, F[1] / fl, F[2] / fl]
  // right = up x forward, up' = forward x right
  let R = [F[2], 0, -F[0]]
  const rl = Math.hypot(R[0], R[2]) || 1
  R = [R[0] / rl, 0, R[2] / rl]
  const U = [F[1] * R[2] - F[2] * R[1], F[2] * R[0] - F[0] * R[2], F[0] * R[1] - F[1] * R[0]]
  return { C, F, R, U, f: H / 2 / Math.tan(fovY / 2), cx: W / 2, cy: H / 2, dist }
}

const NEAR = 0.6

// Collects what one frame draws, projected, with its depth.
function makeFrame(cam) {
  const items = []
  const { C, F, R, U, f, cx, cy } = cam
  const view = (p) => {
    const x = p[0] - C[0]
    const y = p[1] - C[1]
    const z = p[2] - C[2]
    return [x * R[0] + y * R[1] + z * R[2], x * U[0] + y * U[1] + z * U[2], x * F[0] + y * F[1] + z * F[2]]
  }
  const scr = (v) => [cx + (f * v[0]) / v[2], cy - (f * v[1]) / v[2]]
  const frame = {
    items,
    // a straight segment (clipped at the near plane)
    seg(p, q, st, bias = 0) {
      let a = view(p)
      let b = view(q)
      if (a[2] < NEAR && b[2] < NEAR) return
      if (a[2] < NEAR || b[2] < NEAR) {
        const t = (NEAR - a[2]) / (b[2] - a[2])
        const m = [lerp(a[0], b[0], t), lerp(a[1], b[1], t), NEAR]
        if (a[2] < NEAR) a = m
        else b = m
      }
      const s = scr(a)
      const e = scr(b)
      items.push({ d: (a[2] + b[2]) / 2 - bias, x1: s[0], y1: s[1], x2: e[0], y2: e[1], st, poly: null })
    },
    path(pts, st, bias = 0) {
      for (let i = 1; i < pts.length; i++) frame.seg(pts[i - 1], pts[i], st, bias)
    },
    // a filled face with its outline (skipped when any corner is behind the near plane)
    poly(pts, st) {
      const vs = pts.map(view)
      if (vs.some((v) => v[2] < NEAR)) return
      let d = 0
      vs.forEach((v) => (d += v[2]))
      items.push({ d: d / vs.length, poly: vs.map(scr), st })
    },
    // a screen point for a world point (overlays), or null behind the camera
    point(p) {
      const v = view(p)
      return v[2] < NEAR ? null : scr(v)
    },
  }
  return frame
}

// depth cue: far lines fade (quantized so strokes can be batched)
function drawFrame(ctx, frame, cam, sort) {
  const items = frame.items
  if (sort) items.sort((a, b) => b.d - a.d)
  const dNear = cam.dist * 0.55
  const dFar = cam.dist * 2.6
  const level = (d) => Math.round(clamp01((d - dNear) / (dFar - dNear)) * 4)
  let open = null
  const flush = () => {
    if (!open) return
    ctx.stroke()
    open = null
  }
  for (const it of items) {
    const st = it.st
    if (it.poly) {
      flush()
      const k = 1 - 0.13 * level(it.d)
      ctx.beginPath()
      it.poly.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])))
      ctx.closePath()
      if (st.fill) {
        ctx.fillStyle = st.fill
        ctx.fill()
      }
      if (st.color) {
        ctx.strokeStyle = css(st.color, st.alpha * k)
        ctx.lineWidth = st.width
        ctx.stroke()
      }
      continue
    }
    const lv = st.flat ? 0 : level(it.d)
    const key = st.id * 8 + lv
    if (!open || open.key !== key) {
      flush()
      ctx.beginPath()
      ctx.strokeStyle = css(st.color, st.alpha * (1 - 0.14 * lv))
      ctx.lineWidth = st.width
      open = { key }
    }
    ctx.moveTo(it.x1, it.y1)
    ctx.lineTo(it.x2, it.y2)
  }
  flush()
}

// styles: {id (for batching), color [r,g,b], alpha, width (device px set per frame), fill?}
let styleSeq = 0
function style(color, alpha, width, extra) {
  return { id: ++styleSeq, color, alpha, width, ...extra }
}

// ------------------------------------------------------------------ geometry
function towerClass(kv) {
  if (kv >= 300) return 'lattice'
  if (kv >= 200) return 'hframe'
  return 'pole'
}

// per class: span S (m, drawn shorter than a real span to fit the panel), sag in still air D0,
// expansion exaggeration, flashover distance to a tree, half-width of the right-of-way
const CLASSES = {
  pole: { S: 100, D0: 1.8, exag: 4, gap: 1.0, row: 14 },
  hframe: { S: 120, D0: 2.6, exag: 3.2, gap: 1.8, row: 20 },
  lattice: { S: 180, D0: 4.0, exag: 2.5, gap: 3.2, row: 28 },
}

// A tower at x = 0: its members, where the three phases hang, and its shield wires.
function towerGeom(cls, s) {
  const segs = []
  const add = (a, b) => segs.push([a, b])
  if (cls === 'pole') {
    // a tapered pole (drawn as its outline, so it reads side-on)
    add([-0.34, 0, 0], [-0.17, 18.4, 0])
    add([0.34, 0, 0], [0.17, 18.4, 0])
    add([-0.17, 18.4, 0], [0.17, 18.4, 0])
    add([0, 15.8, -2.9], [0, 15.8, 2.9])
    add([0, 14.6, 0], [0, 15.8, -1.7])
    add([0, 14.6, 0], [0, 15.8, 1.7])
    add([0, 15.8, -2.4], [0, 16.3, -2.4])
    add([0, 15.8, 2.4], [0, 16.3, 2.4])
    add([0, 18.4, 0], [0, 18.9, 0])
    return { segs, phases: [[-2.4, 16.3], [0, 18.9], [2.4, 16.3]], shields: [], top: 18.9, snapY: 3 }
  }
  if (cls === 'hframe') {
    for (const z of [-4, 4]) add([0, 0, z], [0, 21, z])
    add([0, 18.6, -6.8], [0, 18.6, 6.8])
    add([0, 18.1, -6.8], [0, 18.1, 6.8])
    add([0, 18.1, -4], [0, 12.4, 4])
    add([0, 18.1, 4], [0, 12.4, -4])
    for (const z of [-5.6, 0, 5.6]) {
      add([0, 18.1, z], [0, 16.2, z])
      for (let y = 17.7; y > 16.3; y -= 0.42) add([-0.3, y, z], [0.3, y, z]) // insulator discs
    }
    return { segs, phases: [[-5.6, 16.2], [0, 16.2], [5.6, 16.2]], shields: [[-4, 21], [4, 21]], top: 21, snapY: 3 }
  }
  // lattice
  const a0 = 4.2 * s
  const aw = 1.5 * s
  const yw = 25 * s
  const yt = 28 * s
  const ypk = 33 * s
  const half = (y) => lerp(a0, aw, y / yw)
  const levels = [0, 6, 12, 18, 25].map((v) => v * s)
  const ring = (y) => {
    const h = half(y)
    return [
      [h, y, h],
      [h, y, -h],
      [-h, y, -h],
      [-h, y, h],
    ]
  }
  for (let k = 0; k < 4; k++) add(ring(0)[k], ring(yw)[k])
  for (let i = 0; i < levels.length; i++) {
    const c = ring(levels[i])
    if (i > 0) for (let k = 0; k < 4; k++) add(c[k], c[(k + 1) % 4])
    if (i < levels.length - 1) {
      const d = ring(levels[i + 1])
      for (let k = 0; k < 4; k++) {
        add(c[k], d[(k + 1) % 4])
        add(c[(k + 1) % 4], d[k])
      }
    }
  }
  const zb = 11 * s
  const zt = 8 * s
  const n = 8
  for (const x of [aw, -aw]) {
    add([x, yw, -zb], [x, yw, zb])
    add([x, yt, -zt], [x, yt, zt])
    add([x, yw, -zb], [x, yt, -zt])
    add([x, yw, zb], [x, yt, zt])
    for (let i = 0; i < n; i++) {
      const b0 = [x, yw, lerp(-zb, zb, i / n)]
      const t1 = [x, yt, lerp(-zt, zt, (i + 1) / n)]
      add(b0, [x, yt, lerp(-zt, zt, i / n)])
      add(b0, t1)
    }
  }
  for (const z of [-zb, zb]) add([-aw, yw, z], [aw, yw, z])
  for (const z of [-zt, zt]) add([-aw, yt, z], [aw, yt, z])
  for (const z of [-5 * s, 5 * s]) {
    for (const x of [-aw, aw]) for (const dz of [-1.3 * s, 1.3 * s]) add([x, yt, z + dz], [0, ypk, z])
  }
  const ya = yw - 4.5 * s
  for (const z of [-9.5 * s, 0, 9.5 * s]) {
    add([aw, yw, z], [0, ya, z])
    add([-aw, yw, z], [0, ya, z])
  }
  return { segs, phases: [[-9.5 * s, ya], [0, ya], [9.5 * s, ya]], shields: [[-5 * s, ypk], [5 * s, ypk]], top: ypk, snapY: 3 }
}

// a conductor hanging from P to Q with sag D at the middle (parabola), n points; `push` shifts
// points down near x = at.x by at.dy (a tree lying on it)
function hang(P, Q, D, n, dz = 0, dyUp = 0, push = null) {
  const pts = []
  for (let i = 0; i <= n; i++) {
    const t = i / n
    const w = 4 * t * (1 - t)
    let y = lerp(P[1], Q[1], t) - D * w + dyUp * w
    const x = lerp(P[0], Q[0], t)
    if (push) {
      const span = x < push.x ? (x - P[0]) / (push.x - P[0] || 1) : (Q[0] - x) / (Q[0] - push.x || 1)
      y -= push.dy * clamp01(span)
    }
    pts.push([x, Math.max(y, 0.12), lerp(P[2], Q[2], t) + dz * w])
  }
  return pts
}

// a tree line along z = zRow (a sawtooth crown silhouette), seeded
function treeLine(x0, x1, zRow, seed) {
  const r = rng(seed)
  const tops = []
  for (let x = x0; x <= x1; x += 5 + r() * 4) {
    const h = 8 + r() * 6
    tops.push([x, h])
    tops.push([x + 2.5 + r() * 1.5, h * (0.55 + r() * 0.15)])
  }
  return tops.map(([x, y]) => [x, y, zRow])
}

function rng(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// a broadleaf tree at (x, z) of height h: trunk and a round crown as three rings
function tree(x, z, h) {
  const segs = []
  const cyc = h * 0.68
  const r = h * 0.3
  segs.push([[x, 0, z], [x, cyc - r * 0.7, z]])
  const N = 12
  for (let i = 0; i < N; i++) {
    const a = (i / N) * TAU
    const b = ((i + 1) / N) * TAU
    segs.push([[x + r * Math.cos(a), cyc, z + r * Math.sin(a)], [x + r * Math.cos(b), cyc, z + r * Math.sin(b)]])
    segs.push([[x + r * Math.cos(a), cyc + r * Math.sin(a), z], [x + r * Math.cos(b), cyc + r * Math.sin(b), z]])
    segs.push([[x, cyc + r * Math.sin(a), z + r * Math.cos(a)], [x, cyc + r * Math.sin(b), z + r * Math.cos(b)]])
  }
  return { segs, top: cyc + r }
}

// a pine leaning from its base by angle th toward the horizontal direction (ux, uz): trunk and a
// cone crown along its axis
function pine(x, z, h, th, ux = 0, uz = 1) {
  const sn = Math.sin(th)
  const cs = Math.cos(th)
  const ax = [ux * sn, cs, uz * sn]
  const b1 = [uz, 0, -ux]
  const b2 = [ux * cs, -sn, uz * cs]
  const at = (s, u = 0, v = 0) => [x + s * ax[0] + u * b1[0] + v * b2[0], s * ax[1] + v * b2[1], z + s * ax[2] + u * b1[2] + v * b2[2]]
  const segs = [[at(0), at(h)]]
  const N = 6
  const base = h * 0.5
  const r = h * 0.16
  const ringPts = []
  for (let i = 0; i < N; i++) {
    const a = (i / N) * TAU
    ringPts.push(at(base, r * Math.cos(a), r * Math.sin(a)))
  }
  for (let i = 0; i < N; i++) {
    segs.push([ringPts[i], ringPts[(i + 1) % N]])
    segs.push([ringPts[i], at(h)])
  }
  return segs
}

function ground(x0, x1, row, stepX) {
  const segs = []
  for (const z of [-row, -row / 2, 0, row / 2, row]) segs.push([[x0, 0, z], [x1, 0, z]])
  for (let x = Math.ceil(x0 / stepX) * stepX; x <= x1; x += stepX) segs.push([[x, 0, -row * 1.25], [x, 0, row * 1.25]])
  return segs
}

function house(x, z, w, d, h, roof) {
  const segs = []
  const c = (sx, y, sz) => [x + (sx * w) / 2, y, z + (sz * d) / 2]
  const box = [
    [-1, -1],
    [1, -1],
    [1, 1],
    [-1, 1],
  ]
  for (let k = 0; k < 4; k++) {
    const [ax, az] = box[k]
    const [bx, bz] = box[(k + 1) % 4]
    segs.push([c(ax, 0, az), c(bx, 0, bz)])
    segs.push([c(ax, h, az), c(bx, h, bz)])
    segs.push([c(ax, 0, az), c(ax, h, az)])
  }
  for (const sx of [-1, 1]) {
    segs.push([c(sx, h, -1), c(sx, h + roof, 0)])
    segs.push([c(sx, h, 1), c(sx, h + roof, 0)])
  }
  segs.push([c(-1, h + roof, 0), c(1, h + roof, 0)])
  // two windows on the side facing -z
  const wins = [-0.45, 0.45].map((f) => {
    const cx = x + (f * w) / 2
    const zz = z - d / 2 - 0.02
    return [
      [cx - 0.7, 1.4, zz],
      [cx + 0.7, 1.4, zz],
      [cx + 0.7, 2.6, zz],
      [cx - 0.7, 2.6, zz],
    ]
  })
  return { segs, wins }
}

// ------------------------------------------------------------------ scenes
const sceneCache = new Map()

/** The scene for an element (cached by its key). */
export function sceneFor(el) {
  const key = `${el.key}|${el.kind}|${el.mode}|${el.kv}|${Math.round(el.at)}|${Math.round(el.before)}`
  let sc = sceneCache.get(key)
  if (!sc) {
    sc = el.kind === 'transformer' ? transformerScene() : lineScene(el)
    if (sceneCache.size > 60) sceneCache.clear()
    sceneCache.set(key, sc)
  }
  return sc
}

function lineScene(el) {
  const cls = towerClass(el.kv)
  const s = cls === 'lattice' ? (el.kv >= 700 ? 1.2 : 1) : 1
  const P = CLASSES[cls]
  const S = P.S * s
  const row = P.row * s
  const tw = towerGeom(cls, s)
  const sag = (pct) => Math.min(sagAt(S, P.D0 * s, tempOf(Math.min(pct, 160)), P.exag), tw.phases[0][1] * 0.62)
  const sAmb = sag(0)
  const relay = el.kind === 'line' && el.mode === 'relay'
  const sBefore = sag(el.kind === 'storm' ? el.at : el.before)
  const sTrip = el.kind === 'storm' ? sBefore : relay ? lerp(sBefore, sag(el.at), 0.6) : sag(el.at)
  const sAfterShed = sag(el.after ?? el.at)
  // the phase nearest the camera (most negative z) and the tree under it
  const out = tw.phases.reduce((m, p) => (p[0] < m[0] ? p : m), tw.phases[0])
  const xT = 0.05 * S
  const w = 1 - ((2 * xT) / S) ** 2
  const ycTrip = out[1] - sTrip * w
  // thermal: the tree the sagging line reaches at the trip; relay or a held line: a tree it never reaches
  const treeTop = Math.max(3, relay || el.kind === 'shed' ? ycTrip - P.gap - out[1] * 0.28 : ycTrip - P.gap)
  const statics = { towers: [], shields: [], ground: ground(-0.95 * S, 0.95 * S, row, S / 4), trees: [], tree: null }
  for (const x0 of [-S / 2, S / 2]) statics.towers.push(tw.segs.map(([a, b]) => [[a[0] + x0, a[1], a[2]], [b[0] + x0, b[1], b[2]]]))
  // the tree lines on both edges of the right-of-way (the shed scene keeps only the near one: its houses are beyond)
  const lines = [treeLine(-0.95 * S, 0.95 * S, -row, 7 + el.kv)]
  if (el.kind !== 'shed') lines.push(treeLine(-0.95 * S, 0.95 * S, row, 11 + el.kv))
  statics.treeLines = lines
  if (el.kind === 'line' || el.kind === 'shed') {
    // the tree under the near phase: tall enough to reach the sagging line (thermal), clear of it otherwise
    const hT = treeTop / (0.68 + 0.3)
    statics.tree = tree(xT, out[0] - 0.4, hT)
  }
  const houses = []
  if (el.kind === 'shed') {
    for (let i = 0; i < 4; i++) houses.push(house(lerp(-0.42 * S, 0.12 * S, i / 3), row + 16 * s + (i % 2) * 5, 9, 7, 4.2, 2.4))
  }
  // storm: a pole line loses its far pole; anything bigger gets a tree across it
  const storm = el.kind === 'storm' ? (cls === 'pole' ? { type: 'pole' } : { type: 'tree' }) : null
  if (storm?.type === 'tree') {
    // it falls across the line at a slant (so the fall reads side-on), meeting the near phase at xT
    const zT = -row + 3
    const yc = out[1] - sBefore * w
    const dz = out[0] - zT
    storm.ux = 0.62
    storm.uz = 0.78
    storm.x = xT - (dz * storm.ux) / storm.uz
    storm.z = zT
    storm.dz = dz
    storm.h = Math.hypot(dz / storm.uz, yc) * 1.18
    storm.thC = Math.atan2(dz, storm.uz * yc) // the angle at which it meets the near phase
    storm.yc = yc
  }
  const yAtt = out[1]
  return {
    kind: el.kind,
    cls,
    S,
    s,
    row,
    tw,
    out,
    xT,
    w,
    sag: { amb: sAmb, before: sBefore, trip: sTrip, afterShed: sAfterShed },
    treeTop,
    relay,
    statics,
    houses,
    storm,
    // nearly broadside, a little above the conductors' low point: both towers in frame, the sag
    // against the sky, the tree below it; the slow orbit swings between the towers
    cam: {
      target: [-0.04 * S, yAtt * 0.52, 0],
      dist: S * 0.64,
      pitch: 0.12,
      yaw0: -0.07,
      amp: 0.11,
      fov: (40 * Math.PI) / 180,
    },
  }
}

function transformerScene() {
  const segs = { ground: [], fence: [], tank: [], rad: [], cons: [], hv: [], lv: [], hvLeads: [], lvLeads: [], gantry: [] }
  for (let v = -12; v <= 12; v += 3) {
    segs.ground.push([[-12, 0, v], [12, 0, v]])
    segs.ground.push([[v, 0, -12], [v, 0, 12]])
  }
  const F = 11
  const corners = [
    [-F, -F],
    [F, -F],
    [F, F],
    [-F, F],
  ]
  for (let k = 0; k < 4; k++) {
    const [ax, az] = corners[k]
    const [bx, bz] = corners[(k + 1) % 4]
    segs.fence.push([[ax, 2.2, az], [bx, 2.2, bz]])
    const n = 8
    for (let i = 0; i < n; i++) {
      const x = lerp(ax, bx, i / n)
      const z = lerp(az, bz, i / n)
      segs.fence.push([[x, 0, z], [x, 2.2, z]])
    }
  }
  // the tank (faces, filled so what is behind them is hidden)
  const bx = (x0, x1, y0, y1, z0, z1) => {
    const p = (x, y, z) => [x, y, z]
    return [
      [p(x0, y0, z0), p(x1, y0, z0), p(x1, y1, z0), p(x0, y1, z0)],
      [p(x0, y0, z1), p(x1, y0, z1), p(x1, y1, z1), p(x0, y1, z1)],
      [p(x0, y0, z0), p(x0, y0, z1), p(x0, y1, z1), p(x0, y1, z0)],
      [p(x1, y0, z0), p(x1, y0, z1), p(x1, y1, z1), p(x1, y1, z0)],
      [p(x0, y1, z0), p(x1, y1, z0), p(x1, y1, z1), p(x0, y1, z1)],
    ]
  }
  const faces = { pad: bx(-4.2, 4.2, 0, 0.3, -3.3, 3.3), tank: bx(-3.4, 3.4, 0.3, 4.6, -1.9, 1.9), fins: [] }
  for (const side of [-1, 1]) {
    for (let i = 0; i < 8; i++) {
      const x = -2.8 + i * 0.8
      const z0 = side * 1.95
      const z1 = side * 3.05
      faces.fins.push([
        [x, 0.9, z0],
        [x, 0.9, z1],
        [x, 4.1, z1],
        [x, 4.1, z0],
      ])
    }
    segs.rad.push([[-2.95, 4.1, side * 2.5], [2.95, 4.1, side * 2.5]])
    segs.rad.push([[-2.95, 0.9, side * 2.5], [2.95, 0.9, side * 2.5]])
  }
  // conservator: a drum on top at one end
  const N = 10
  for (const z of [-1.6, 1.6]) {
    for (let i = 0; i < N; i++) {
      const a = (i / N) * TAU
      const b = ((i + 1) / N) * TAU
      segs.cons.push([[2.6 + 0.5 * Math.cos(a), 6.1 + 0.5 * Math.sin(a), z], [2.6 + 0.5 * Math.cos(b), 6.1 + 0.5 * Math.sin(b), z]])
    }
  }
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * TAU + 0.4
    segs.cons.push([[2.6 + 0.5 * Math.cos(a), 6.1 + 0.5 * Math.sin(a), -1.6], [2.6 + 0.5 * Math.cos(a), 6.1 + 0.5 * Math.sin(a), 1.6]])
  }
  for (const z of [-1.2, 1.2]) segs.cons.push([[2.6, 4.6, z], [2.6, 5.6, z]])
  segs.cons.push([[2.6, 5.6, 0], [2.0, 4.6, 0]])
  // bushings: a column with sheds; high voltage tall on one side, low voltage short on the other
  const bushing = (x, z, y0, y1, sheds, r, list) => {
    list.push([[x - r * 0.7, y0, z], [x - r * 0.7, y1, z]])
    list.push([[x + r * 0.7, y0, z], [x + r * 0.7, y1, z]])
    for (let k = 0; k < sheds; k++) {
      const y = lerp(y0 + 0.3, y1 - 0.2, k / Math.max(1, sheds - 1))
      const rr = k % 2 ? r * 0.8 : r
      for (let i = 0; i < 8; i++) {
        const a = (i / 8) * TAU
        const b = ((i + 1) / 8) * TAU
        list.push([[x + rr * Math.cos(a), y, z + rr * Math.sin(a)], [x + rr * Math.cos(b), y, z + rr * Math.sin(b)]])
      }
    }
    list.push([[x, y1, z], [x, y1 + 0.35, z]])
  }
  const bxs = [-2.2, -0.9, 0.4]
  bxs.forEach((x) => bushing(x, -1.0, 4.6, 8.4, 8, 0.34, segs.hv))
  bxs.forEach((x) => bushing(x, 1.0, 4.6, 6.2, 4, 0.26, segs.lv))
  // leads: up to the gantry on one side, to a low bus on the other
  for (const x of [-4.2, 2.4]) {
    segs.gantry.push([[x, 0, -8], [x, 11, -8]])
    segs.gantry.push([[x + 0.4, 0, -8], [x + 0.4, 11, -8]])
    for (let y = 1; y < 11; y += 2) segs.gantry.push([[x, y, -8], [x + 0.4, y + 1, -8]])
  }
  segs.gantry.push([[-4.4, 10.6, -8], [2.9, 10.6, -8]])
  segs.gantry.push([[-4.4, 10.1, -8], [2.9, 10.1, -8]])
  for (let x = -4.2; x < 2.6; x += 0.9) segs.gantry.push([[x, 10.1, -8], [x + 0.45, 10.6, -8]])
  bxs.forEach((x) => {
    segs.hvLeads.push(hang([x, 8.75, -1.0], [x, 9.4, -8], 0.6, 10))
    segs.gantry.push([[x, 10.1, -8], [x, 9.4, -8]])
    segs.lvLeads.push(hang([x, 6.55, 1.0], [x, 5.6, 6.5], 0.35, 8))
  })
  segs.lvLeads.push([[-3.2, 5.6, 6.5], [1.2, 5.6, 6.5]])
  segs.gantry.push([[-3.0, 0, 6.5], [-3.0, 5.6, 6.5]])
  segs.gantry.push([[1.0, 0, 6.5], [1.0, 5.6, 6.5]])
  return {
    kind: 'transformer',
    segs,
    faces,
    cam: { target: [-0.4, 4.6, 0], dist: 23, pitch: 0.2, yaw0: -0.72, amp: 0.22, fov: (40 * Math.PI) / 180 },
  }
}

// ------------------------------------------------------------------ state at a time
/**
 * What the element is doing `u` ms from its trip, with an approach of `A` ms before it.
 * -> {L (the loading shown, %), p (approach 0..1), heat 0..1, live (energized), loss (red 0..1),
 *     open 0..1 (breaker), status}
 */
export function stateAt(el, u, A) {
  const p = u >= 0 ? 1 : A > 0 ? clamp01((u + A) / A) : 1
  const e = easeInOut(p)
  const heatOf = (L) => clamp01((L - 92) / Math.max(22, Math.min(el.at, RELAY_PCT) - 92))
  const open = clamp01((u - BREAKER_MS) / 110)
  const loss = u >= BREAKER_MS ? 1 - clamp01((u - BREAKER_MS) / LOSS_MS) : 0
  if (el.kind === 'storm') {
    return { L: el.at, p, heat: 0, live: u < BREAKER_MS, loss, open, status: u >= BREAKER_MS ? 'down' : 'wind' }
  }
  if (el.kind === 'shed') {
    const L = u < 0 ? lerp(el.before, el.at, e) : u < BREAKER_MS ? el.at : lerp(el.at, el.after ?? el.at, easeOut(clamp01((u - BREAKER_MS) / SHED_COOL_MS)))
    return { L, p, heat: heatOf(L), live: true, loss, open, status: u >= BREAKER_MS ? 'cut' : L > 100 ? 'over' : 'rising' }
  }
  const L = u < 0 ? lerp(el.before, el.at, e) : el.at
  return { L, p, heat: u < BREAKER_MS ? heatOf(L) : 0, live: u < BREAKER_MS, loss, open, status: u >= BREAKER_MS ? 'tripped' : L > 100 ? 'over' : 'rising' }
}

// ------------------------------------------------------------------ drawing
/**
 * Draw `el` at `u` ms from its trip into a canvas of W x H device px. `tSec` drives the camera's
 * slow orbit and the wind; `alpha` fades a scene in. Returns stateAt(...).
 */
export function drawScene(ctx, W, H, dpr, el, u, A, tSec, { alpha = 1, pal } = {}) {
  const P = pal || readPalette()
  const sc = sceneFor(el)
  const st = stateAt(el, u, A)
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.globalAlpha = 1
  // on the map (the float dock) the drawing sits straight on the map: no panel color behind it
  if (P.clear) ctx.clearRect(0, 0, W, H)
  else {
    ctx.fillStyle = css(P.bg, 1)
    ctx.fillRect(0, 0, W, H)
  }
  ctx.globalAlpha = alpha
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  const c = sc.cam
  const yaw = c.yaw0 + c.amp * Math.sin((tSec * TAU) / 19)
  const pitch = c.pitch + 0.03 * Math.sin((tSec * TAU) / 23 + 1)
  // the camera sits lower in the frame's middle so the ground reads; offset target up a little on wide canvases
  const cam = camera(c.target, yaw, pitch, c.dist, c.fov, W, H)
  const fr = makeFrame(cam)
  const px = dpr
  if (sc.kind === 'transformer') drawTransformer(fr, sc, st, P, px)
  else drawLine(fr, sc, el, st, u, tSec, P, px)
  drawFrame(ctx, fr, cam, sc.kind === 'transformer')
  overlays(ctx, W, H, dpr, sc, el, st, u, fr, P)
  ctx.globalAlpha = 1
  return st
}

function conductorColor(P, st) {
  if (!st.live) return mix(P.loss, P.dead, 1 - st.loss)
  return mix(mix(P.lit, P.ink2, 0.15), P.strain, st.heat)
}

function drawLine(fr, sc, el, st, u, tSec, P, px) {
  const S = sc.S
  const sG = style(P.ink2, 0.13, 1 * px)
  const sTreeLine = style(P.ink2, 0.32, 1 * px)
  const sTower = style(P.ink2, sc.cls === 'pole' ? 0.8 : 0.62, (sc.cls === 'pole' ? 1.2 : 1) * px)
  const sShield = style(P.ink2, 0.35, 1 * px)
  const sTree = style(P.ink2, 0.55, 1 * px)
  const col = conductorColor(P, st)
  const sCond = style(col, st.live ? 0.95 : 0.9, (st.live ? 1.5 : 1.3) * px)
  const sCondFar = style(col, 0.55, 1.2 * px)
  sc.statics.ground.forEach(([a, b]) => fr.seg(a, b, sG))
  // tree lines; in the wind their crowns sway
  const wind = sc.kind === 'storm'
  sc.statics.treeLines.forEach((pts, li) => {
    const moved = wind ? pts.map((p, i) => (i % 2 ? p : [p[0], p[1], p[2] + 0.7 * Math.sin(tSec * 5.1 + p[0] * 0.09 + li)])) : pts
    fr.path(moved, sTreeLine)
  })
  if (sc.statics.tree) sc.statics.tree.segs.forEach(([a, b]) => fr.seg(a, b, sTree))

  // towers: the far one may snap (a storm on a pole line)
  let snap = null
  if (sc.storm?.type === 'pole') {
    const f = clamp01((u + FALL_MS) / (FALL_MS + 260))
    snap = { phi: 1.32 * f * f, y: sc.tw.snapY, x: S / 2 }
  }
  const rot = (p) => {
    if (!snap || p[0] < S / 2 - 1 || p[1] <= snap.y) return p
    const dy = p[1] - snap.y
    const c = Math.cos(snap.phi)
    const s = Math.sin(snap.phi)
    return [p[0], snap.y + dy * c + p[2] * s, p[2] * c - dy * s]
  }
  sc.statics.towers.forEach((segs) => segs.forEach(([a, b]) => fr.seg(rot(a), rot(b), sTower)))

  // sag now
  let D
  if (sc.kind === 'storm') D = sc.sag.before
  else if (sc.kind === 'shed') D = u < 0 ? lerp(sc.sag.before, sc.sag.trip, easeInOut(st.p)) : u < BREAKER_MS ? sc.sag.trip : lerp(sc.sag.trip, sc.sag.afterShed, easeOut(clamp01((u - BREAKER_MS) / SHED_COOL_MS)))
  else if (u < 0) D = lerp(sc.sag.before, sc.sag.trip, easeInOut(st.p))
  else if (u < BREAKER_MS) D = sc.sag.trip
  else D = sc.sag.amb + (sc.sag.trip - sc.sag.amb) * Math.exp(-(u - BREAKER_MS) / COOL_TAU)

  // the storm's tree
  let push = null
  if (sc.storm?.type === 'tree') {
    const g = sc.storm
    const f = clamp01((u + FALL_MS) / FALL_MS)
    const sway = 0.05 + 0.04 * Math.sin(tSec * 4.2)
    let th = u < -FALL_MS ? sway : lerp(sway, g.thC, f * f)
    let dy = 0
    if (u > 0) {
      const k = easeOut(clamp01(u / 520))
      dy = (g.yc - 2.2) * k
      th = Math.atan2(g.dz, g.uz * (g.yc - dy))
    }
    pine(g.x, g.z, g.h, th, g.ux, g.uz).forEach(([a, b]) => fr.seg(a, b, sTree))
    if (dy > 0) push = { x: sc.xT, dy }
  }

  // conductors: this span and half of each neighbor, per phase; the wind swings them (blowout)
  const n = 26
  sc.tw.phases.forEach(([z, y], k) => {
    const A = rot([-S / 2, y, z])
    const B = rot([S / 2, y, z])
    let dz = 0
    let up = 0
    if (wind) {
      const psi = 0.32 + 0.16 * Math.sin(tSec * 3.3 + k * 0.5)
      dz = D * Math.sin(psi)
      up = D * (1 - Math.cos(psi))
    }
    const main = hang(A, B, D, n, dz, up, push && z === sc.out[0] ? push : null)
    fr.path(main, sCond)
    fr.path(hang([-1.5 * S, y, z], A, D, n, dz, up).slice(n / 2), sCondFar)
    fr.path(hang(B, [1.5 * S, y, z], D, n, dz, up).slice(0, n / 2 + 1), sCondFar)
  })
  sc.tw.shields.forEach(([z, y]) => {
    const A = [-S / 2, y, z]
    const B = rot([S / 2, y, z])
    fr.path(hang(A, B, sc.sag.amb * 0.85, 20), sShield)
  })
  // houses beyond the held line: their windows go dark when the feeder opens
  if (sc.houses.length) {
    const sHouse = style(P.ink2, 0.5, 1 * px)
    const lit = st.status !== 'cut'
    const winSt = lit ? { fill: css(P.lit, 0.55), color: P.lit, alpha: 0.7, width: 1 * px } : { fill: css(P.bg, 1), color: P.dead, alpha: 0.9, width: 1 * px }
    sc.houses.forEach((h) => {
      h.segs.forEach(([a, b]) => fr.seg(a, b, sHouse))
      h.wins.forEach((w) => fr.poly(w, winSt))
    })
  }
  // where the arc lands, for the overlay
  sc.frameArc = null
  if (sc.kind === 'line' && !sc.relay && sc.statics.tree) {
    const yc = sc.out[1] - D * sc.w
    sc.frameArc = { a: fr.point([sc.xT, yc, sc.out[0]]), b: fr.point([sc.xT, sc.treeTop, sc.out[0] - 0.4]) }
  } else if (sc.storm?.type === 'tree') {
    const yc = sc.storm.yc
    sc.frameArc = { a: fr.point([sc.xT, yc + 0.8, sc.out[0]]), b: fr.point([sc.xT, yc - 0.8, sc.out[0]]) }
  } else if (sc.storm?.type === 'pole') {
    sc.frameArc = { a: fr.point([S / 2, 1.5, -1.5]), b: fr.point([S / 2 - 3, 0, -2]) }
  }
  sc.frameBreaker = fr.point([-S / 2, 0.5, sc.out[0]])
}

function drawTransformer(fr, sc, st, P, px) {
  const g = sc.segs
  const sG = style(P.ink2, 0.12, 1 * px)
  const sFence = style(P.ink2, 0.3, 1 * px)
  const hot = mix(P.ink2, P.strain, st.heat)
  const face = { fill: css(P.bg, 1), color: hot, alpha: 0.8, width: 1 * px }
  const fin = { fill: css(P.bg, 1), color: hot, alpha: 0.6, width: 1 * px }
  const padF = { fill: css(P.bg, 1), color: P.ink2, alpha: 0.4, width: 1 * px }
  const sRad = style(hot, 0.6, 1 * px)
  const sCons = style(P.ink2, 0.6, 1 * px)
  const liveCol = st.live ? mix(P.lit, P.ink2, 0.1) : mix(P.loss, P.dead, 1 - st.loss)
  const sBush = style(liveCol, st.live ? 0.9 : 0.85, 1 * px)
  const sLead = style(liveCol, st.live ? 0.9 : 0.8, 1.4 * px)
  const sGantry = style(P.ink2, 0.5, 1 * px)
  g.ground.forEach(([a, b]) => fr.seg(a, b, sG, -1))
  g.fence.forEach(([a, b]) => fr.seg(a, b, sFence))
  sc.faces.pad.forEach((f) => fr.poly(f, padF))
  sc.faces.tank.forEach((f) => fr.poly(f, face))
  sc.faces.fins.forEach((f) => fr.poly(f, fin))
  g.rad.forEach(([a, b]) => fr.seg(a, b, sRad, 0.05))
  g.cons.forEach(([a, b]) => fr.seg(a, b, sCons, 0.05))
  g.gantry.forEach(([a, b]) => fr.seg(a, b, sGantry))
  g.hv.forEach(([a, b]) => fr.seg(a, b, sBush, 0.05))
  g.lv.forEach(([a, b]) => fr.seg(a, b, sBush, 0.05))
  g.hvLeads.forEach((pts) => fr.path(pts, sLead, 0.05))
  g.lvLeads.forEach((pts) => fr.path(pts, sLead, 0.05))
  sc.frameBreaker = fr.point([-1.0, 10.4, -8])
  sc.frameArc = null
}

// 2D overlays in screen space: the flashover arc, the clearance dimension, the breaker, the thermometer
function overlays(ctx, W, H, dpr, sc, el, st, u, fr, P) {
  const px = dpr
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.lineCap = 'round'
  // the clearance closing (thermal line, during the approach)
  if (sc.kind === 'line' && !sc.relay && u < 0 && sc.frameArc?.a && sc.frameArc?.b) {
    const { a, b } = sc.frameArc
    const x = a[0] + 10 * px
    if (b[1] - a[1] > 3 * px) {
      ctx.strokeStyle = css(P.ink2, 0.7)
      ctx.lineWidth = 1 * px
      ctx.beginPath()
      ctx.moveTo(x - 3 * px, a[1])
      ctx.lineTo(x + 3 * px, a[1])
      ctx.moveTo(x - 3 * px, b[1])
      ctx.lineTo(x + 3 * px, b[1])
      ctx.moveTo(x, a[1])
      ctx.lineTo(x, b[1])
      ctx.stroke()
      ctx.fillStyle = css(P.ink2, 0.85)
      ctx.font = `${10 * px}px "Archivo Variable", system-ui, sans-serif`
      ctx.textBaseline = 'middle'
      ctx.fillText('clearance', x + 5 * px, (a[1] + b[1]) / 2)
    }
  }
  // the flashover arc (thermal line: conductor to tree; storm: where it comes down)
  const arcOn = (sc.kind === 'line' && !sc.relay) || sc.kind === 'storm'
  if (arcOn && u > -30 && u < ARC_MS && sc.frameArc?.a && sc.frameArc?.b) {
    const { a, b } = sc.frameArc
    const q = clamp01((u + 30) / (ARC_MS + 30))
    const seed = Math.floor(u / 40)
    ctx.beginPath()
    const K = 7
    for (let i = 0; i <= K; i++) {
      const t = i / K
      const jitter = i === 0 || i === K ? 0 : (hash(seed * 13 + i) - 0.5) * 9 * px
      const x = lerp(a[0], b[0], t) + jitter
      const y = lerp(a[1], b[1], t)
      if (i) ctx.lineTo(x, y)
      else ctx.moveTo(x, y)
    }
    ctx.strokeStyle = css(P.strain, 0.55 * (1 - q))
    ctx.lineWidth = 3 * px
    ctx.stroke()
    ctx.strokeStyle = css([255, 250, 240], 0.95 * (1 - q * 0.8))
    ctx.lineWidth = 1.2 * px
    ctx.stroke()
    // a few short spark ticks where it strikes
    ctx.beginPath()
    for (let k = 0; k < 5; k++) {
      const ang = hash(seed * 7 + k) * TAU
      const r0 = (2 + 3 * q) * px
      const r1 = r0 + (4 + 5 * hash(seed + k * 3)) * px
      ctx.moveTo(b[0] + Math.cos(ang) * r0, b[1] + Math.sin(ang) * r0)
      ctx.lineTo(b[0] + Math.cos(ang) * r1, b[1] + Math.sin(ang) * r1)
    }
    ctx.strokeStyle = css(P.strain, 0.8 * (1 - q))
    ctx.lineWidth = 1 * px
    ctx.stroke()
  }
  breakerGlyph(ctx, W, H, dpr, sc, el, st, u, P)
  if (sc.kind === 'transformer') thermometer(ctx, W, H, dpr, el, st, P)
}

// the breaker as a schematic switch, in the lower left, with a hairline to where it sits
function breakerGlyph(ctx, W, H, dpr, sc, el, st, u, P) {
  const px = dpr
  const x = 14 * px
  const y = H - 16 * px
  const len = 26 * px
  const shed = el.kind === 'shed'
  const open = st.open
  const red = open > 0 ? 1 - clamp01((u - BREAKER_MS - 900) / 700) : 0
  const col = open > 0 ? mix(P.ink2, P.loss, red) : P.ink2
  if (sc.frameBreaker) {
    ctx.strokeStyle = css(P.ink2, 0.28)
    ctx.lineWidth = 1 * px
    ctx.setLineDash([2 * px, 3 * px])
    ctx.beginPath()
    ctx.moveTo(x + len / 2, y - 5 * px)
    ctx.lineTo(sc.frameBreaker[0], sc.frameBreaker[1])
    ctx.stroke()
    ctx.setLineDash([])
  }
  ctx.strokeStyle = css(col, 0.95)
  ctx.fillStyle = css(col, 0.95)
  ctx.lineWidth = 1.3 * px
  ctx.beginPath()
  ctx.moveTo(x, y)
  ctx.lineTo(x + 7 * px, y)
  ctx.moveTo(x + len - 7 * px, y)
  ctx.lineTo(x + len, y)
  ctx.stroke()
  // the blade: hinged at the left contact, lifting open
  const ang = -0.62 * easeOut(open)
  const bl = len - 14 * px
  ctx.beginPath()
  ctx.moveTo(x + 7 * px, y)
  ctx.lineTo(x + 7 * px + bl * Math.cos(ang), y + bl * Math.sin(ang))
  ctx.stroke()
  for (const cx of [x + 7 * px, x + len - 7 * px]) {
    ctx.beginPath()
    ctx.arc(cx, y, 1.8 * px, 0, TAU)
    ctx.fill()
  }
  // the relay's switching arc at the contacts (relay trips, far past the rating)
  if (sc.relay && u > BREAKER_MS - 10 && u < BREAKER_MS + 160) {
    const q = (u - BREAKER_MS + 10) / 170
    ctx.strokeStyle = css([255, 250, 240], 0.9 * (1 - q))
    ctx.lineWidth = 1 * px
    ctx.beginPath()
    ctx.moveTo(x + len - 7 * px, y)
    ctx.lineTo(x + len - 10 * px, y - 4 * px)
    ctx.lineTo(x + len - 12 * px, y - 2 * px)
    ctx.stroke()
  }
  ctx.font = `${10 * px}px "Archivo Variable", system-ui, sans-serif`
  ctx.textBaseline = 'middle'
  ctx.fillStyle = css(open > 0 ? col : P.ink2, 0.9)
  const name = shed ? 'Feeder breaker' : 'Breaker'
  const state = open > 0.5 ? (shed ? 'opened on purpose' : 'open') : 'closed'
  ctx.fillText(`${name} · ${state}`, x + len + 6 * px, y)
}

// the transformer's temperature, as a bar filled by its loading, with the rating marked
function thermometer(ctx, W, H, dpr, el, st, P) {
  const px = dpr
  const x = W - 18 * px
  const top = 44 * px
  const bot = H - 26 * px
  const max = Math.max(150, Math.ceil((Math.max(el.at, 100) * 1.08) / 25) * 25)
  const yOf = (pct) => bot - (bot - top) * clamp01(pct / max)
  ctx.strokeStyle = css(P.ink2, 0.6)
  ctx.lineWidth = 1 * px
  ctx.strokeRect(x - 3 * px, top, 6 * px, bot - top)
  const L = st.status === 'tripped' ? el.at : st.L
  const fillCol = L > 100 ? P.strain : P.lit
  ctx.fillStyle = css(st.status === 'tripped' ? mix(fillCol, P.dead, 0.55) : fillCol, 0.85)
  ctx.fillRect(x - 2 * px, yOf(L), 4 * px, bot - yOf(L))
  const y100 = yOf(100)
  ctx.beginPath()
  ctx.moveTo(x - 8 * px, y100)
  ctx.lineTo(x + 5 * px, y100)
  ctx.stroke()
  ctx.font = `${10 * px}px "Archivo Variable", system-ui, sans-serif`
  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = css(P.ink2, 0.9)
  ctx.fillText('rating', x - 11 * px, y100)
  ctx.fillText('heat', x + 4 * px, bot + 12 * px)
  ctx.textAlign = 'left'
}

// ------------------------------------------------------------------ DOM helpers for the panel
/** The canvas's backing store sized to its CSS box times the device pixel ratio (at most 2). */
export function fitCanvas(cv) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const W = Math.max(1, Math.round(cv.clientWidth * dpr))
  const H = Math.max(1, Math.round(cv.clientHeight * dpr))
  if (cv.width !== W || cv.height !== H) {
    cv.width = W
    cv.height = H
  }
  return { W, H, dpr }
}

/** The loading figure and the status tag, written straight to the DOM (no React render per frame). */
export function readout(fig, tag, st) {
  if (fig) {
    // a held line relieved to 99.5 % reads as under its rating, not "100 %"
    // past ~300 % a DC-flow re-solve after an island is an artefact, not a reading: "over 300 %" (REVIEW-1)
    const v = st.L > 99 && st.L < 100 ? 99 : Math.round(st.L)
    const text = v > 300 ? 'over 300 %' : `${v.toLocaleString('en-US')} %`
    if (fig.textContent !== text) fig.textContent = text
    const over = String(st.L > 100 && st.live)
    if (fig.dataset.over !== over) fig.dataset.over = over
  }
  if (tag) {
    const text = STATUS_TEXT[st.status] || ''
    if (tag.textContent !== text) tag.textContent = text
    if (tag.dataset.state !== st.status) tag.dataset.state = st.status
  }
}

const hash = (n) => {
  const s = Math.sin(n * 12.9898) * 43758.5453
  return s - Math.floor(s)
}
