import { api } from '../../api'
import { CITIES, STATES } from '../../geo'
import { easeSine, itemStart, mapBusy, SPAN_LEAD, spanMs, spanned } from './util'

// The show's map, drawn on a canvas every frame: the states' outlines, the region's synthetic grid, and the
// scene's map layers (lines, points, zones, the storm), under a camera that flies between scene bounds and drifts
// slowly within a scene. Everything is a function of the scene clock `t`, so pausing the clock freezes the map.
// Projection: equirectangular around the camera's own latitude (continuous while the camera flies).

const C = {
  stage: '#03070e',
  land: '#0c1624',
  coast: '#26374f',
  line: '127,152,189',
  lit: '214,226,241',
  loss: '#ff3d5e',
  lossRgb: '255,61,94',
  strain: '#ffb03a',
  gain: '#3fd68a',
  gainRgb: '63,214,138',
  a: '#5ab4ff',
  b: '#c9a6ff',
  tripped: '#7a2a36',
  ink: '#e6edf7',
  ink2: '#9aabc4',
}
const HELD = 1e6 // a layer carried over from the last scene: already fully drawn
const KM_PER_DEG = 111.19
const FONT = '"Archivo Variable", system-ui, sans-serif'

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v))
const ease = (p) => (p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2) // in-out cubic
const easeOut = (p) => 1 - (1 - p) ** 3
const lerp = (a, b, p) => a + (b - a) * p

// ------------------------------------------------------------------------------------------ the grid
const grids = new Map() // code -> {lines: Float32Array [lat, lon, lat, lon]*, hv: Uint8Array, subs: Float32Array [lat, lon]*}
const pending = new Map()
export function regionCodes(region) {
  return (String(region || 'FL').toUpperCase().match(/[A-Z]{2}/g) || []).filter((c) => STATES[c]).slice(0, 2)
}
function loadGrid(code) {
  if (grids.has(code) || pending.has(code)) return
  const p = api(`/api/grid?region=${encodeURIComponent(code)}`)
    .then((g) => {
      const byId = new Map(g.subs.map((s) => [s.id, s]))
      const br = g.branches.filter((b) => b.from_sub !== b.to_sub)
      const lines = new Float32Array(br.length * 4)
      const hv = new Uint8Array(br.length)
      br.forEach((b, i) => {
        const f = byId.get(b.from_sub)
        const t = byId.get(b.to_sub)
        lines.set([f.lat, f.lon, t.lat, t.lon], i * 4)
        hv[i] = b.kv >= 230 ? 1 : 0
      })
      const subs = new Float32Array(g.subs.length * 2)
      g.subs.forEach((s, i) => subs.set([s.lat, s.lon], i * 2))
      grids.set(code, { lines, hv, subs, bornAt: performance.now() })
    })
    .catch(() => grids.set(code, null))
    .finally(() => pending.delete(code))
  pending.set(code, p)
}

// ------------------------------------------------------------------------------------------ helpers
function hatchPattern(ctx) {
  const c = document.createElement('canvas')
  c.width = c.height = 9
  const g = c.getContext('2d')
  g.strokeStyle = `rgba(${C.lossRgb},0.55)`
  g.lineWidth = 1.2
  g.beginPath()
  g.moveTo(-2, 11)
  g.lineTo(11, -2)
  g.stroke()
  return ctx.createPattern(c, 'repeat')
}

// the polyline drawn up to `frac` of its length
function partial(ctx, pts, frac) {
  if (pts.length < 2) return null
  let total = 0
  const seg = []
  for (let i = 1; i < pts.length; i++) {
    const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
    seg.push(d)
    total += d
  }
  let left = total * clamp(frac)
  ctx.moveTo(pts[0][0], pts[0][1])
  let end = pts[0]
  for (let i = 1; i < pts.length; i++) {
    if (left <= 0) break
    const d = seg[i - 1]
    if (d <= left) {
      ctx.lineTo(pts[i][0], pts[i][1])
      end = pts[i]
      left -= d
    } else {
      const f = left / d
      end = [lerp(pts[i - 1][0], pts[i][0], f), lerp(pts[i - 1][1], pts[i][1], f)]
      ctx.lineTo(end[0], end[1])
      left = 0
    }
  }
  return end
}

function midpoint(pts) {
  if (!pts.length) return [0, 0]
  if (pts.length === 1) return pts[0]
  let total = 0
  for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
  let left = total / 2
  for (let i = 1; i < pts.length; i++) {
    const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
    if (d >= left) return [lerp(pts[i - 1][0], pts[i][0], left / d), lerp(pts[i - 1][1], pts[i][1], left / d)]
    left -= d
  }
  return pts[pts.length - 1]
}

// A stable signature for a layer (a layer repeated in the next scene carries on instead of animating in again).
export const layerSig = (l) => JSON.stringify(l)

const MAP_TYPES = new Set(['grid', 'lines', 'points', 'zones', 'storm'])
const RIGHT = new Set(['counter', 'meter', 'bars', 'quote'])

// Room the overlays take, so the camera frames the scene's places in the part of the stage you can see.
function padsFor(scene, W, H, capTop) {
  const types = new Set((scene?.layers || []).map((l) => l.type))
  if (types.has('compare') && mapBusy(scene)) types.add('counter') // docked in the right column
  const narrow = W < 720
  const top = narrow ? 64 + (types.has('agent') ? H * 0.3 : [...RIGHT].some((t) => types.has(t)) ? H * 0.2 : 0) : 72
  const cap = capTop > 0 ? capTop : narrow ? 210 : Math.min(H * 0.3, 250)
  const bottom = cap + (types.has('timeline') ? 60 : 0) + (types.has('lower_third') ? 50 : 0)
  const right = !narrow && [...RIGHT].some((t) => types.has(t)) ? Math.min(W * 0.3, 380) : narrow ? 16 : W * 0.05
  // the picker and the loading panel take the left of the stage: the map frames itself beside them
  const left = !narrow && scene?.panel ? Math.min(W * 0.45, 660) : !narrow && types.has('agent') ? Math.min(W * 0.3, 400) : narrow ? 16 : W * 0.05
  return { l: left, r: right, t: top, b: bottom }
}

function fit(bounds, W, H, pad, minKm) {
  const [[s, w], [n, e]] = bounds
  const lat = (s + n) / 2
  const lon = (w + e) / 2
  const k = Math.cos((lat * Math.PI) / 180)
  const aw = Math.max(80, W - pad.l - pad.r)
  const ah = Math.max(80, H - pad.t - pad.b)
  // never closer than about 180 km across (a single line or station keeps its towns around it), unless the scene is a
  // close-up of one line or station and asks for a tighter frame (camera.min_km)
  const span = Math.max(15, minKm || 178) / KM_PER_DEG
  const z = Math.min(aw / Math.max(0.02, (e - w) * k), ah / Math.max(0.02, n - s), ah / span, aw / (span * 1.1))
  return { lat, lon, z: Math.max(4, z), fx: pad.l + aw / 2, fy: pad.t + ah / 2 }
}

export function regionBounds(region) {
  const codes = regionCodes(region)
  if (!codes.length) return [[24.4, -87.7], [31.1, -79.8]]
  let [w, s, e, n] = [180, 90, -180, -90]
  codes.forEach((c) => {
    const [x0, y0, x1, y1] = STATES[c].bbox
    w = Math.min(w, x0)
    s = Math.min(s, y0)
    e = Math.max(e, x1)
    n = Math.max(n, y1)
  })
  return [[s, w], [n, e]]
}

// ------------------------------------------------------------------------------------------ the map
export function createMap(canvas, { reduced }) {
  const ctx = canvas.getContext('2d')
  let W = 1
  let H = 1
  let dpr = 1
  let vignette = null
  let hatch = null
  let cam = null // the camera now: {lat, lon, z, fx, fy}
  let flight = null // {from, start, dur}
  let runSeen = -1
  let prevLayers = [] // the last scene's map layers, fading out: {layer, t}
  let prevAt = 0
  let lastLayers = []
  let lastT = 0
  let cutAt = -1e9 // reduced motion: when the last cut happened (a fade from black)
  let flowClock = 0
  let capTop = 0
  let lastNow = performance.now()
  const gridAlpha = { v: 0 }

  function resize() {
    const r = canvas.getBoundingClientRect()
    dpr = Math.min(window.devicePixelRatio || 1, 2)
    W = Math.max(1, r.width)
    H = Math.max(1, r.height)
    canvas.width = Math.round(W * dpr)
    canvas.height = Math.round(H * dpr)
    vignette = null
  }
  resize()
  const ro = new ResizeObserver(resize)
  ro.observe(canvas)

  function target(scene, idx, t, est, region) {
    const bounds = scene?.camera?.bounds || lastBounds || regionBounds(region)
    const base = fit(bounds, W, H, padsFor(scene, W, H, capTop), scene?.camera?.min_km)
    if (reduced) return base
    // the drift: a slow push in and a small sideways pan across the scene
    const p = clamp(t / Math.max(est, 4000))
    const e = p * (2 - p) * 0.5 + p * 0.5
    const dir = idx % 2 ? 1 : -1
    const spanLon = W / (base.z * Math.cos((base.lat * Math.PI) / 180))
    return { ...base, z: base.z * (1 + 0.06 * e), lon: base.lon + dir * spanLon * 0.018 * e, lat: base.lat + 0.004 * spanLon * e }
  }
  let lastBounds = null

  function camera(now, tgt) {
    if (!cam) {
      cam = tgt
      return cam
    }
    if (flight) {
      const p = clamp((now - flight.start) / flight.dur)
      const e = ease(p)
      const f = flight.from
      const lz0 = Math.log(f.z)
      const lz1 = Math.log(tgt.z)
      cam = {
        lat: lerp(f.lat, tgt.lat, e),
        lon: lerp(f.lon, tgt.lon, e),
        z: Math.exp(lerp(lz0, lz1, e) - flight.bump * Math.sin(Math.PI * e)),
        fx: lerp(f.fx, tgt.fx, e),
        fy: lerp(f.fy, tgt.fy, e),
      }
      if (p >= 1) flight = null
      return cam
    }
    cam = tgt
    return cam
  }

  function startFlight(now, tgt) {
    if (!cam || reduced) {
      if (cam && reduced) cutAt = now
      cam = tgt
      flight = null
      return
    }
    // how far, in screens at the wider of the two views, and how much the zoom changes
    const zMin = Math.min(cam.z, tgt.z)
    const k = Math.cos((cam.lat * Math.PI) / 180)
    const dx = ((tgt.lon - cam.lon) * k * zMin) / W
    const dy = ((tgt.lat - cam.lat) * zMin) / H
    const dist = Math.hypot(dx, dy)
    const dz = Math.abs(Math.log2(tgt.z / cam.z))
    if (dist < 0.004 && dz < 0.02) return
    const dur = clamp(1100 + 520 * dz + 700 * Math.min(dist, 2.5), 1100, 3400)
    flight = { from: { ...cam }, start: now, dur, bump: Math.min(1.1, Math.log1p(dist) * 0.9) }
  }

  // ------------------------------------------------------------------ one frame
  function draw({ show, scene, idx, run, t, est, now }) {
    const region = show?.region || 'FL'
    regionCodes(region).forEach(loadGrid)
    const layers = (scene?.layers || []).filter((l) => MAP_TYPES.has(l.type))
    if (run !== runSeen) {
      // a new scene: the old layers fade out, the ones it repeats carry on
      if (runSeen !== -1) {
        prevLayers = lastLayers.map((layer) => ({ layer, t: lastT }))
        prevAt = now
      }
      const keep = new Set(layers.map(layerSig))
      prevLayers = prevLayers.filter((p) => !keep.has(layerSig(p.layer)))
      heldSigs = new Set(lastLayers.map(layerSig).filter((s) => keep.has(s)))
      if (scene?.camera?.bounds) lastBounds = scene.camera.bounds
      // where the captions begin (the player measures it): the map frames its places above them
      capTop = parseFloat(canvas.parentElement?.style.getPropertyValue('--sh-bottom')) || 0
      runSeen = run
      startFlight(now, target(scene, idx, t, est, region))
    }
    lastLayers = layers
    const tBefore = lastT
    lastT = t
    const P = cameraProjector(camera(now, target(scene, idx, t, est, region)))

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.fillStyle = C.stage
    ctx.fillRect(0, 0, W, H)
    drawLand(P)

    // the grid's brightness eases toward what the scene asks for
    const gl = layers.find((l) => l.type === 'grid')
    const want = !gl ? (scene ? 0.35 : 0.5) : gl.mode === 'hidden' ? 0 : gl.mode === 'dim' ? 0.42 : 1
    gridAlpha.v += (want - gridAlpha.v) * 0.06

    const labels = []
    const fading = clamp(1 - (now - prevAt) / 450)
    const all = [
      ...(fading > 0 ? prevLayers.map((p) => ({ layer: p.layer, lt: p.t + 1e5, alpha: fading })) : []),
      ...layers.map((layer) => ({ layer, lt: heldSigs.has(layerSig(layer)) ? t + HELD : t, alpha: 1 })),
    ]
    // blackout and restored circles as they are right now (the grid's lights answer them)
    const dark = []
    const lit = []
    all.forEach(({ layer, lt }) => {
      if (layer.type !== 'zones' || (layer.style !== 'blackout' && layer.style !== 'restored')) return
      zoneGeom(layer, lt, P, est).forEach((z) => z.r > 0.5 && (layer.style === 'blackout' ? dark : lit).push(z))
    })

    // the flow clock runs with the show (a paused scene holds still) and freely while picking or loading
    if (t !== tBefore || !scene?.lines) flowClock += Math.min(100, Math.max(0, now - lastNow))
    lastNow = now
    const gridBorn = drawGrid(P, region, gridAlpha.v, now, dark)
    all.filter((a) => a.layer.type === 'storm').forEach((a) => drawStorm(a.layer, a.lt, a.alpha, P, est, now))
    all.filter((a) => a.layer.type === 'zones').forEach((a) => drawZones(a.layer, a.lt, a.alpha, P, labels, est))
    drawSubs(P, region, gridAlpha.v * gridBorn, dark, lit)
    all.filter((a) => a.layer.type === 'lines').forEach((a) => drawLines(a.layer, a.lt, a.alpha, P, labels, est, now))
    // a station layer's own style colors it (over: red, stress: amber, upgrade: green); an older show without one:
    // a scene that builds upgrades draws its stations in the fix's green
    const upgrading = layers.some((l) => l.type === 'lines' && l.style === 'upgrade')
    all.filter((a) => a.layer.type === 'points').forEach((a) => drawPoints(a.layer, a.lt, a.alpha, P, labels, now, upgrading, est))
    if (regionCodes(region).includes('FL')) CITIES.forEach((c) => labels.push({ at: P(c.lat, c.lon), text: c.name, prio: 0, color: C.ink2, size: 11, dot: true, alpha: 0.75 }))
    // two states on screen (a border story): their names, quietly, where they sit
    const codes = regionCodes(region)
    if (codes.length > 1)
      codes.forEach((c) => {
        const [x0, y0, x1, y1] = STATES[c].bbox
        const a = P(y1, x0)
        const b = P(y0, x1)
        if (b[0] - a[0] < 160) return
        labels.push({ at: P((y0 + y1) / 2, (x0 + x1) / 2), text: STATES[c].name, prio: 0.5, color: C.ink2, size: 15, alpha: 0.55, center: true })
      })
    placeLabels(labels)

    if (!vignette) {
      vignette = ctx.createRadialGradient(W / 2, H * 0.45, Math.min(W, H) * 0.35, W / 2, H * 0.45, Math.hypot(W, H) * 0.62)
      vignette.addColorStop(0, 'rgba(0,0,0,0)')
      vignette.addColorStop(1, 'rgba(0,0,0,0.6)')
    }
    ctx.fillStyle = vignette
    ctx.fillRect(0, 0, W, H)
    if (reduced && now - cutAt < 420) {
      ctx.fillStyle = `rgba(3,7,14,${1 - (now - cutAt) / 420})`
      ctx.fillRect(0, 0, W, H)
    }
  }
  let heldSigs = new Set()

  function cameraProjector(c) {
    const k = Math.cos((c.lat * Math.PI) / 180) * c.z
    const P = (lat, lon) => [c.fx + (lon - c.lon) * k, c.fy - (lat - c.lat) * c.z]
    P.z = c.z
    P.km = c.z / KM_PER_DEG
    return P
  }

  function drawLand(P) {
    ctx.beginPath()
    for (const st of Object.values(STATES)) {
      const [x0, y0, x1, y1] = st.bbox
      const a = P(y1, x0)
      const b = P(y0, x1)
      if (b[0] < -50 || a[0] > W + 50 || b[1] < -50 || a[1] > H + 50) continue
      for (const ring of st.rings) {
        ring.forEach(([lon, lat], i) => {
          const [x, y] = P(lat, lon)
          if (i) ctx.lineTo(x, y)
          else ctx.moveTo(x, y)
        })
        ctx.closePath()
      }
    }
    ctx.fillStyle = C.land
    ctx.fill('evenodd')
    ctx.strokeStyle = C.coast
    ctx.lineWidth = 1
    ctx.stroke()
  }

  function drawGrid(P, region, alpha, now, dark) {
    let born = 1
    const inside = (x, y) => dark.some((z) => (x - z.x) ** 2 + (y - z.y) ** 2 < z.r * z.r)
    regionCodes(region).forEach((code) => {
      const g = grids.get(code)
      if (!g) return
      born = Math.min(born, clamp((now - g.bornAt) / 900))
      const a = alpha * born
      if (a < 0.01) return
      const flow = [] // the high-voltage lines on screen: power moving along them
      for (const pass of [0, 1]) {
        ctx.beginPath()
        for (let i = 0; i < g.hv.length; i++) {
          if (g.hv[i] !== pass) continue
          const [x1, y1] = P(g.lines[i * 4], g.lines[i * 4 + 1])
          const [x2, y2] = P(g.lines[i * 4 + 2], g.lines[i * 4 + 3])
          if ((x1 < 0 && x2 < 0) || (x1 > W && x2 > W) || (y1 < 0 && y2 < 0) || (y1 > H && y2 > H)) continue
          ctx.moveTo(x1, y1)
          ctx.lineTo(x2, y2)
          if (pass && flow.length < 3600) flow.push(x1, y1, x2, y2, i)
        }
        ctx.strokeStyle = `rgba(${C.line},${(pass ? 0.34 : 0.2) * a})`
        ctx.lineWidth = pass ? 1.05 : 0.6
        ctx.stroke()
      }
      // one pale pulse travelling each line (not with reduced motion; never inside a blackout)
      if (reduced || a < 0.2) return
      ctx.fillStyle = `rgba(${C.lit},${0.5 * a})`
      const secs = flowClock / 1000
      for (let k = 0; k < flow.length; k += 5) {
        const [x1, y1, x2, y2, i] = [flow[k], flow[k + 1], flow[k + 2], flow[k + 3], flow[k + 4]]
        const len = Math.hypot(x2 - x1, y2 - y1)
        if (len < 14) continue
        const f = (secs * (46 / len) + i * 0.618) % 1
        const x = x1 + (x2 - x1) * f
        const y = y1 + (y2 - y1) * f
        if (x < 0 || y < 0 || x > W || y > H || (dark.length && inside(x, y))) continue
        ctx.fillRect(x - 0.9, y - 0.9, 1.8, 1.8)
      }
    })
    return born
  }

  // the substations: small pale lights; out inside a blackout, brighter inside a restored zone
  function drawSubs(P, region, alpha, dark, lit) {
    if (alpha < 0.01) return
    const inside = (x, y, list) => list.some((z) => (x - z.x) ** 2 + (y - z.y) ** 2 < z.r * z.r)
    regionCodes(region).forEach((code) => {
      const g = grids.get(code)
      if (!g) return
      const s = P.z > 400 ? 2.2 : 1.5
      ctx.fillStyle = `rgba(${C.lit},${0.55 * alpha})`
      const glowing = []
      for (let i = 0; i < g.subs.length; i += 2) {
        const [x, y] = P(g.subs[i], g.subs[i + 1])
        if (x < -4 || y < -4 || x > W + 4 || y > H + 4) continue
        if (dark.length && inside(x, y, dark)) continue
        if (lit.length && inside(x, y, lit)) glowing.push(x, y)
        else ctx.fillRect(x - s / 2, y - s / 2, s, s)
      }
      if (glowing.length) {
        ctx.fillStyle = `rgba(216,255,233,${0.95 * alpha})`
        for (let i = 0; i < glowing.length; i += 2) ctx.fillRect(glowing[i] - s * 0.8, glowing[i + 1] - s * 0.8, s * 1.6, s * 1.6)
      }
    })
  }

  // ------------------------------------------------------------------ zones
  function zoneGeom(layer, lt, P, est) {
    const items = layer.items || []
    const stagger = layer.animate === 'spread' ? 550 : 250
    return items.map((z, i) => {
      const [x, y] = P(z.lat, z.lon)
      const full = Math.max(2, (z.radius_km || 10) * P.km)
      const local = lt - itemStart(layer, z, i, est, stagger, 250)
      let f = 1
      if (layer.animate === 'spread') f = easeOut(clamp(local / 1500))
      else if (layer.animate === 'appear') f = local < 0 ? 0 : 1
      return { x, y, r: full * f, full, f, a: layer.animate === 'appear' ? clamp(local / 500) : f > 0 ? 1 : 0, item: z, local }
    })
  }

  function drawZones(layer, lt, alpha, P, labels, est) {
    const geo = zoneGeom(layer, lt, P, est).filter((z) => z.r > 0.5)
    if (!geo.length) return
    const style = layer.style
    ctx.save()
    ctx.globalAlpha = alpha
    const path = () => {
      ctx.beginPath()
      geo.forEach((z) => {
        ctx.moveTo(z.x + z.r, z.y)
        ctx.arc(z.x, z.y, z.r, 0, Math.PI * 2)
      })
    }
    if (style === 'blackout') {
      path()
      ctx.fillStyle = 'rgba(0,0,0,0.78)'
      ctx.fill('nonzero')
      if (!hatch) hatch = hatchPattern(ctx)
      ctx.fillStyle = hatch
      ctx.globalAlpha = alpha * 0.8
      ctx.fill('nonzero')
      ctx.globalAlpha = alpha
      geo.forEach((z) => {
        ctx.beginPath()
        ctx.arc(z.x, z.y, z.r, 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(${C.lossRgb},${0.25 + 0.5 * (1 - z.f) + 0.2})`
        ctx.lineWidth = z.f < 1 ? 1.6 : 1
        ctx.stroke()
      })
    } else if (style === 'restored') {
      path()
      ctx.fillStyle = `rgba(${C.gainRgb},0.07)`
      ctx.fill('nonzero')
      geo.forEach((z) => {
        ctx.beginPath()
        ctx.arc(z.x, z.y, z.r, 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(${C.gainRgb},${0.35 + 0.5 * (1 - z.f)})`
        ctx.lineWidth = 1.4
        ctx.stroke()
      })
    } else {
      // storm: the area it covers
      geo.forEach((z) => {
        ctx.globalAlpha = alpha * z.a
        ctx.beginPath()
        ctx.arc(z.x, z.y, z.full, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(${C.lit},0.07)`
        ctx.fill()
        ctx.setLineDash([4, 5])
        ctx.strokeStyle = `rgba(${C.lit},0.4)`
        ctx.lineWidth = 1
        ctx.stroke()
        ctx.setLineDash([])
      })
    }
    ctx.restore()
    // the town's name only: a zone's weight is the population its substations serve, not the people without power
    // there (those are the bars' figures), so it is never printed
    geo.forEach((z) => {
      if (z.f < 0.98 || !z.item.label) return
      labels.push({
        at: [z.x, z.y],
        text: z.item.label,
        sub: null,
        prio: 2 + (z.item.weight || z.item.people || 0) / 1e7,
        color: style === 'blackout' ? C.ink : style === 'restored' ? C.gain : C.ink2,
        subColor: style === 'blackout' ? C.loss : C.ink2,
        size: 12,
        alpha,
        center: true,
      })
    })
  }

  // ------------------------------------------------------------------ lines
  const LINE = {
    stress: { color: C.strain, width: 2 },
    over: { color: C.loss, width: 2.6 },
    trip: { color: C.loss, width: 2.6 },
    upgrade: { color: C.gain, width: 3.2 },
    project_a: { color: C.a, width: 3.2 },
    project_b: { color: C.b, width: 3.2 },
    corridor: { color: C.ink, width: 1.2 },
  }
  function drawLines(layer, lt, alpha, P, labels, est, now) {
    const st = LINE[layer.style] || LINE.stress
    const items = layer.items || []
    const anim = layer.animate || 'draw'
    const stagger = anim === 'draw' ? 140 : anim === 'flash' ? 320 : 120
    const labelled = items.filter((it) => it.label).length <= 6
    // a few lines: each gets its break ring and a slow locator pulse; a storm's hundreds: every k-th ring only
    const ringEvery = Math.max(1, Math.ceil(items.length / 14))
    const locate = items.length <= 2 && (layer.style === 'over' || layer.style === 'trip' || layer.style === 'stress') && anim !== 'none'
    const heavy = layer.emphasis ? 1.2 : 0 // a focal line, or the whole state's overloads at once: drawn heavier
    items.forEach((it, i) => {
      const pts = (it.path || []).map(([la, lo]) => P(la, lo))
      if (pts.length < 2) return
      const local = lt - itemStart(layer, it, i, est, stagger, 200)
      if (local < 0 && anim !== 'none') return
      // a pair that meets at one point (the same station): a ring there, drawn round as it is ranked
      if (layer.style === 'corridor' && Math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]) < 3) {
        const f = anim === 'draw' ? ease(clamp(local / 950)) : 1
        ctx.save()
        ctx.globalAlpha = alpha
        ctx.setLineDash([4, 4])
        ctx.beginPath()
        ctx.arc(pts[0][0], pts[0][1], 16, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * f)
        ctx.strokeStyle = C.ink
        ctx.lineWidth = 1.4
        ctx.stroke()
        ctx.setLineDash([])
        if (!reduced && f >= 1) {
          const p = (now % 2400) / 2400
          ctx.beginPath()
          ctx.arc(pts[0][0], pts[0][1], 16 + 18 * easeOut(p), 0, Math.PI * 2)
          ctx.strokeStyle = `rgba(${C.lit},${0.45 * (1 - p)})`
          ctx.lineWidth = 1
          ctx.stroke()
        }
        ctx.restore()
        if (it.label && f >= 1) labels.push({ at: [pts[0][0] + 12, pts[0][1] - 12], text: it.label, prio: 3.5, color: C.ink, size: 11.5, alpha, offset: 8 })
        return
      }
      let frac = 1
      let a = 1
      let width = (locate ? st.width + 1 : st.width) + heavy
      let color = st.color
      let dash = null
      if (anim === 'draw') frac = ease(clamp(local / 950))
      else if (anim === 'fade') a = clamp(local / 800)
      else if (anim === 'flash') {
        // three hard blinks, then it holds
        if (local < 540) a = Math.floor(local / 90) % 2 ? 0.15 : 1
        width = (local < 540 ? st.width + 1.4 : st.width) + heavy
      }
      if (layer.style === 'trip' && anim !== 'none' && local > 900) {
        // tripped: the line is out, drawn dark and broken
        color = C.tripped
        dash = [5, 4]
        width = 2
      } else if (layer.style === 'trip' && anim === 'none') {
        color = C.tripped
        dash = [5, 4]
        width = 2
      }
      ctx.save()
      ctx.globalAlpha = alpha * a
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      if (layer.style === 'corridor') {
        ctx.beginPath()
        partial(ctx, pts, frac)
        ctx.strokeStyle = `rgba(${C.lit},0.1)`
        ctx.lineWidth = 16
        ctx.stroke()
        dash = [6, 6]
      }
      if (dash) ctx.setLineDash(dash)
      ctx.beginPath()
      const end = partial(ctx, pts, frac)
      ctx.strokeStyle = color
      ctx.lineWidth = width
      ctx.stroke()
      ctx.setLineDash([])
      // the drawing head: a small bright point while the line is being traced
      if (anim === 'draw' && frac < 1 && end) {
        ctx.beginPath()
        ctx.arc(end[0], end[1], width + 1.2, 0, Math.PI * 2)
        ctx.fillStyle = C.ink
        ctx.fill()
      }
      // the moment a line trips: one ring from where it broke
      if (locate && local > 600) {
        const m = midpoint(pts)
        const p = reduced ? 0.5 : ((local - 600) % 2200) / 2200
        ctx.beginPath()
        ctx.arc(m[0], m[1], 10 + 30 * easeOut(p), 0, Math.PI * 2)
        ctx.strokeStyle = layer.style === 'stress' ? `rgba(255,176,58,${0.55 * (1 - p)})` : `rgba(${C.lossRgb},${0.55 * (1 - p)})`
        ctx.lineWidth = 1.2
        ctx.stroke()
      }
      if (layer.style === 'trip' && anim !== 'none' && local < 1400 && (i % ringEvery === 0 || (spanned(layer) && items.length <= 40))) {
        const m = midpoint(pts)
        const p = clamp(local / 1400)
        ctx.beginPath()
        ctx.arc(m[0], m[1], 4 + 40 * easeOut(p), 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(${C.lossRgb},${0.8 * (1 - p)})`
        ctx.lineWidth = 1.5
        ctx.stroke()
      }
      ctx.restore()
      if (it.label && labelled && frac >= 1 && (layer.style !== 'stress' || layer.emphasis)) {
        const m = midpoint(pts)
        const value =
          typeof it.value === 'string' ? it.value : it.value != null && (layer.style === 'over' || layer.style === 'stress') ? `${Math.round(it.value)} % of rating` : null
        labels.push({ at: m, text: it.label, sub: value, prio: 3, color: layer.style === 'trip' ? C.loss : color === C.tripped ? C.loss : color, subColor: C.ink2, size: 11.5, alpha: alpha * a })
      }
    })
  }

  // ------------------------------------------------------------------ points
  // a point layer's color: its style (over red, stress amber, upgrade green, a utility's blue or lilac), else the ink
  const POINT_TONE = { over: C.loss, stress: C.strain, upgrade: C.gain, project_a: C.a, project_b: C.b }
  const TONE_RGB = { [C.loss]: C.lossRgb, [C.strain]: '255,176,58', [C.gain]: C.gainRgb, [C.a]: '90,180,255', [C.b]: '201,166,255' }
  function drawPoints(layer, lt, alpha, P, labels, now, upgrading, est) {
    const items = layer.items || []
    const anim = layer.animate || 'appear'
    const stagger = anim === 'drop' ? 450 : 160
    const kind = layer.kind
    const tone = POINT_TONE[layer.style] || (kind === 'project' ? C.a : upgrading && kind === 'station' ? C.gain : null)
    items.forEach((it, i) => {
      const [x, y] = P(it.lat, it.lon)
      const local = lt - itemStart(layer, it, i, est, stagger, 250)
      if (local < 0 && anim !== 'none') return
      let a = 1
      let dy = 0
      let sc = 1
      if (anim === 'drop') {
        const p = clamp(local / 420)
        dy = -46 * (1 - p * p)
        a = clamp(local / 160)
      } else if (anim === 'appear' || anim === 'pulse') {
        const p = clamp(local / 380)
        a = p
        sc = 0.6 + 0.4 * easeOut(p)
      }
      ctx.save()
      ctx.globalAlpha = alpha * a
      ctx.translate(x, y + dy)
      ctx.scale(sc, sc)
      drawMark(kind, tone)
      ctx.restore()
      // an upgrade going in (green), an overload found (red), a weak point (amber): one ring as it lands
      if (tone && kind === 'station' && anim !== 'none' && local > 0 && local < 1600) {
        const p = clamp(local / 1600)
        ctx.beginPath()
        ctx.arc(x, y, 8 + 36 * easeOut(p), 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(${TONE_RGB[tone] || C.lit},${0.8 * (1 - p) * alpha})`
        ctx.lineWidth = 1.6
        ctx.stroke()
      }
      // one ring where it lands
      if (anim === 'drop' && local > 420 && local < 1500) {
        const p = clamp((local - 420) / 1080)
        ctx.beginPath()
        ctx.arc(x, y, 7 + 34 * easeOut(p), 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(${C.lit},${0.7 * (1 - p) * alpha})`
        ctx.lineWidth = 1.3
        ctx.stroke()
      }
      if (anim === 'pulse' && local > 0) {
        const p = ((now % 1800) / 1800) * 1
        ctx.beginPath()
        ctx.arc(x, y, 8 + 22 * easeOut(p), 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(${kind === 'hospital' ? '255,176,58' : TONE_RGB[tone] || C.lit},${0.6 * (1 - p) * alpha * a})`
        ctx.lineWidth = 1.2
        ctx.stroke()
      }
      if ((it.label || it.sub) && (anim !== 'drop' || local > 420)) {
        labels.push({
          at: [x, y],
          text: it.label || it.sub,
          sub: it.label ? it.sub : null,
          prio: kind === 'campus' ? 5 : kind === 'hospital' ? 4 : kind === 'town' ? 1 : 3,
          color: tone || C.ink,
          subColor: kind === 'hospital' ? C.strain : tone && kind === 'station' ? tone : C.ink2,
          size: kind === 'campus' ? 12.5 : 11.5,
          alpha: alpha * a,
          offset: kind === 'town' ? 6 : 12,
        })
      }
    })
  }

  function drawMark(kind, tone) {
    ctx.lineWidth = 1.5
    if (kind === 'campus') {
      ctx.beginPath()
      ctx.arc(0, 0, 9.5, 0, Math.PI * 2)
      ctx.strokeStyle = 'rgba(230,237,247,0.55)'
      ctx.stroke()
      ctx.beginPath()
      ctx.arc(0, 0, 5.2, 0, Math.PI * 2)
      ctx.fillStyle = C.ink
      ctx.fill()
      ctx.strokeStyle = C.stage
      ctx.stroke()
    } else if (kind === 'town') {
      ctx.beginPath()
      ctx.arc(0, 0, 2.6, 0, Math.PI * 2)
      ctx.fillStyle = C.ink2
      ctx.fill()
    } else if (kind === 'station') {
      ctx.beginPath()
      ctx.arc(0, 0, 6, 0, Math.PI * 2)
      ctx.fillStyle = C.stage
      ctx.fill()
      ctx.strokeStyle = tone || C.ink
      ctx.lineWidth = 2
      ctx.stroke()
      ctx.beginPath()
      ctx.arc(0, 0, 2, 0, Math.PI * 2)
      ctx.fillStyle = tone || C.ink
      ctx.fill()
    } else if (kind === 'plant') {
      ctx.beginPath()
      ctx.moveTo(0, -6.5)
      ctx.lineTo(6.5, 0)
      ctx.lineTo(0, 6.5)
      ctx.lineTo(-6.5, 0)
      ctx.closePath()
      ctx.fillStyle = C.stage
      ctx.fill()
      ctx.strokeStyle = C.ink2
      ctx.stroke()
    } else if (kind === 'project') {
      ctx.beginPath()
      ctx.arc(0, 0, 5.5, 0, Math.PI * 2)
      ctx.fillStyle = C.stage
      ctx.fill()
      ctx.strokeStyle = tone || C.a
      ctx.lineWidth = 2
      ctx.stroke()
    } else if (kind === 'hospital') {
      ctx.beginPath()
      ctx.arc(0, 0, 7.5, 0, Math.PI * 2)
      ctx.fillStyle = C.ink
      ctx.fill()
      ctx.fillStyle = C.stage
      ctx.fillRect(-1.3, -4.5, 2.6, 9)
      ctx.fillRect(-4.5, -1.3, 9, 2.6)
    } else {
      ctx.beginPath()
      ctx.arc(0, 0, 4, 0, Math.PI * 2)
      ctx.fillStyle = C.ink
      ctx.fill()
    }
  }

  // ------------------------------------------------------------------ the storm
  function drawStorm(layer, lt, alpha, P, est, now) {
    const pts = (layer.track || []).map(([la, lo]) => P(la, lo))
    if (pts.length < 2) return
    // a spanned storm travels on the scene's own clock, the one its downed lines wait for (sync)
    const p = layer.animate === 'none' ? 1 : spanned(layer) ? easeSine((lt - SPAN_LEAD) / spanMs(est)) : ease(clamp((lt - 300) / Math.max(3500, est * 0.82)))
    const r = Math.max(10, (layer.radius_km || 60) * P.km)
    ctx.save()
    ctx.globalAlpha = alpha
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    // the path ahead
    ctx.setLineDash([3, 7])
    ctx.beginPath()
    partial(ctx, pts, 1)
    ctx.strokeStyle = `rgba(${C.lit},0.3)`
    ctx.lineWidth = 1.2
    ctx.stroke()
    ctx.setLineDash([])
    // the corridor it has crossed
    ctx.beginPath()
    const eye = partial(ctx, pts, p) || pts[0]
    ctx.strokeStyle = `rgba(${C.lit},0.075)`
    ctx.lineWidth = r * 2
    ctx.stroke()
    // the storm: a soft disc, bands turning counterclockwise, the eye
    ctx.beginPath()
    ctx.arc(eye[0], eye[1], r, 0, Math.PI * 2)
    ctx.fillStyle = `rgba(${C.lit},0.08)`
    ctx.fill()
    const rot = reduced ? 0 : -now / 700
    for (let k = 0; k < 4; k++) {
      ctx.beginPath()
      const rr = r * (0.3 + 0.2 * k)
      const a0 = rot + k * 1.7
      ctx.arc(eye[0], eye[1], rr, a0, a0 + 1.9 - k * 0.2)
      ctx.strokeStyle = `rgba(${C.lit},${0.55 - k * 0.1})`
      ctx.lineWidth = Math.max(1.2, r * 0.07)
      ctx.stroke()
    }
    ctx.beginPath()
    ctx.arc(eye[0], eye[1], Math.max(3, r * 0.1), 0, Math.PI * 2)
    ctx.fillStyle = C.stage
    ctx.fill()
    ctx.strokeStyle = C.ink
    ctx.lineWidth = 1.5
    ctx.stroke()
    ctx.restore()
  }

  // ------------------------------------------------------------------ labels
  function placeLabels(list) {
    const boxes = []
    list.sort((a, b) => b.prio - a.prio)
    // a city already named by a scene's own label (a campus in Tampa, a town going dark) isn't named twice
    const named = list.filter((L) => L.prio > 0 && L.text).map((L) => L.text.toLowerCase())
    const placed = []
    for (const L of list) {
      if (!L.text || L.alpha < 0.05) continue
      if (L.prio === 0 && named.some((t) => t.startsWith(L.text.toLowerCase()))) continue
      // the same name twice in one spot (two circuits, two transformers at one station): once
      if (placed.some((o) => o.text === L.text && Math.abs(o.at[0] - L.at[0]) < 60 && Math.abs(o.at[1] - L.at[1]) < 40)) continue
      const [x0, y0] = L.at
      if (x0 < -20 || y0 < -20 || x0 > W + 20 || y0 > H + 20) continue
      const size = L.size || 12
      ctx.font = `600 ${size}px ${FONT}`
      const w1 = ctx.measureText(L.text).width
      ctx.font = `500 ${size - 1}px ${FONT}`
      const w2 = L.sub ? ctx.measureText(L.sub).width : 0
      const w = Math.max(w1, w2)
      const h = L.sub ? size * 2.3 : size * 1.25
      const off = L.offset ?? 10
      // try right, left, below, above
      const tries = L.center ? [[-w / 2, -h / 2], [-w / 2, off], [-w / 2, -h - off]] : [[off, -h / 2], [-off - w, -h / 2], [-w / 2, off], [-w / 2, -h - off]]
      let spot = null
      for (const [dx, dy] of tries) {
        const b = { x: x0 + dx - 3, y: y0 + dy - 2, w: w + 6, h: h + 4 }
        if (b.x < 4 || b.y < 50 || b.x + b.w > W - 4 || b.y + b.h > H - 4) continue
        if (boxes.some((o) => b.x < o.x + o.w && b.x + b.w > o.x && b.y < o.y + o.h && b.y + b.h > o.y)) continue
        spot = { b, x: x0 + dx, y: y0 + dy }
        break
      }
      if (!spot) continue
      boxes.push(spot.b)
      placed.push(L)
      ctx.save()
      ctx.globalAlpha = clamp(L.alpha)
      ctx.textBaseline = 'top'
      ctx.lineJoin = 'round'
      ctx.lineWidth = 3.5
      ctx.strokeStyle = 'rgba(3,7,14,0.85)'
      ctx.font = `600 ${size}px ${FONT}`
      ctx.strokeText(L.text, spot.x, spot.y)
      ctx.fillStyle = L.color || C.ink
      ctx.fillText(L.text, spot.x, spot.y)
      if (L.sub) {
        ctx.font = `500 ${size - 1}px ${FONT}`
        ctx.strokeText(L.sub, spot.x, spot.y + size * 1.2)
        ctx.fillStyle = L.subColor || C.ink2
        ctx.fillText(L.sub, spot.x, spot.y + size * 1.2)
      }
      ctx.restore()
    }
  }

  return {
    draw,
    destroy: () => ro.disconnect(),
  }
}
