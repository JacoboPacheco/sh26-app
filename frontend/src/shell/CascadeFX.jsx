import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useReducedMotion } from '../features/impact/towns'
import { useMapView } from '../GridMap'
import { HEIGHT, WIDTH, fmt, muPerKm } from '../geo'
import { useOverload } from '../store'
import './bomb.css'
import { RING_POW, titleCase } from './cascadeSchedule'

// The blast, in slow motion. While a cascade plays, the schedule (the store's `fx.schedule`,
// shell/cascadeSchedule.js) is drawn without a React render per frame, in two layers:
//   SVG, inside the map (below the labels): every effect laid down ONCE as a CSS animation whose
//     delay is its time in the schedule — the failed line snaps white-hot and dies; every light the
//     blast front passes flashes white-hot and settles to an ember; as the darkness arrives, the land
//     around each light goes black over it. Kept few: each one repaints the heavy map under it.
//   Canvas, over the map (BlastCanvas): what moves across large areas — the blast front expanding in
//     slow motion, the failure point's flare, the sparks running along the real lines at the head of
//     the darkness, and the floating
//     "+people" over each town the front lands on. Drawn per frame from the same clock as the
//     counter; on a canvas they don't make the browser repaint the heavy map under them (an SVG
//     front forced a re-raster of the ~1,300 lights and ~3,300 lines under it on every frame).
// Paused or scrubbed, both are off and the map shows the exact step (the store's view).
// Sizes: SVG stroke widths are screen px (non-scaling strokes); radii and text are divided by the zoom.

const CROWD_MIN = 5000 // a call-out under this many people is dropped when there's no room for it
const calloutPx = (p) => Math.min(30, 14 + 5.5 * Math.max(0, Math.log10(Math.max(p, 1)) - 3.7)) // bigger for bigger hits
const HIT_LIFE = 1800 // a hit's call-out pops, drifts and fades over this…
const KEEP = 3 // …except each tier's biggest few, which stay faintly until the tier ends
const LINE_LIFE = 2400
const WAVE_LIFE = 1400
const nameOf = (s) => titleCase(s?.name || '')

export default function CascadeFX() {
  const { fx, playing, subById, branchById, cascade } = useOverload()
  const { k, project } = useMapView()
  const [aim, setAim] = useState(null) // a line the impact panel points at before the run (hover)
  const reduced = useReducedMotion()
  // the FX group (its screen matrix is the camera's) and the map box the canvas goes in
  const anchor = useRef(null)
  const [host, setHost] = useState(null)
  const attach = useCallback((el) => {
    anchor.current = el
    setHost(el?.ownerSVGElement?.parentElement || null)
  }, [])

  useEffect(() => {
    const on = (e) => setAim(e.detail?.id ?? null)
    window.addEventListener('overload:aim-line', on)
    return () => window.removeEventListener('overload:aim-line', on)
  }, [])

  // the time between play and this layer's first paint, so the CSS clock and the counter's rAF clock
  // agree; read once per run (a later re-render must not move a running animation's delay)
  // eslint-disable-next-line react/purity -- read once per run, on purpose
  const lag = useMemo(() => (fx ? performance.now() - fx.startedAt : 0), [fx])

  // everything the replay will draw, positioned in map units, with its time — once per run
  // (and again if the zoom changes: sizes and the call-outs' spots depend on it)
  const plan = useMemo(() => {
    if (!fx) return null
    const at = (t) => `${Math.round(t - lag)}ms`
    const mu = muPerKm()
    const xy = (id) => {
      const s = subById.get(id)
      return s ? project(s.lon, s.lat) : null
    }
    // Call-outs must not land on each other: each is nudged up or down from its spot until it's
    // clear of the ones on screen at the same time. Sizes in real screen px: the map box fits the
    // view (WIDTH x HEIGHT map units), then the zoom.
    const base = host ? Math.min(host.clientWidth / WIDTH, host.clientHeight / HEIGHT) || 1 : 1
    const upx = 1 / (k * base)
    const alive = []
    const place = (c) => {
      const w = Math.max(...c.lines.map((l) => l.text.length * l.px * 0.52)) * upx + 10 * upx
      const h = c.lines.reduce((a, l) => a + l.px * 1.15, 0) * upx + 6 * upx
      const t0 = c.t
      const t1 = Math.max(c.t + c.life, c.keep || 0)
      const box = (y) => ({ x0: c.x - w / 2, x1: c.x + w / 2, y0: y - h - 22 * upx, y1: y + 4 * upx, t0, t1 })
      const free = (b) => !alive.some((a) => a.t0 < b.t1 && b.t0 < a.t1 && a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1)
      for (const n of [0, -1, 1, -2, 2, -3, 3, -4]) {
        const y = c.y + n * (h + 4 * upx)
        const b = box(y)
        if (free(b)) {
          alive.push(b)
          return { ...c, y, lead: n !== 0 ? c.y : null }
        }
      }
      if (c.people < CROWD_MIN) return null
      alive.push(box(c.y))
      return c
    }
    const snaps = []
    const hits = []
    const darks = []
    const rings = []
    const flares = []
    const sparks = []
    const callouts = []
    const lineFlares = []
    fx.schedule.tiers.forEach((tier) => {
      const key = `s${tier.step}`
      const st = cascade?.steps?.[tier.step - 1]
      const o = tier.origin && project(tier.origin.lon, tier.origin.lat)
      // SNAP: the failed line flashes, with its call-out at its midpoint
      tier.lines.forEach((bid, i) => {
        const b = branchById.get(bid)
        const a = b && xy(b.from_sub)
        const z = b && xy(b.to_sub)
        if (!a || !z) return
        if (b.from_sub !== b.to_sub) snaps.push({ key: `${key}-${bid}`, x1: a[0], y1: a[1], x2: z[0], y2: z[1], delay: at(tier.t0) })
        if (i > 0) return // a storm's many lines: one call-out
        const from = subById.get(b.from_sub)
        const what = b.from_sub === b.to_sub ? `${nameOf(from)} transformer` : `${nameOf(from)} → ${nameOf(subById.get(b.to_sub))}`
        const mw = st?.carried?.[0]?.mw
        const more = tier.lines.length > 1 ? ` + ${tier.lines.length - 1} more lines` : ''
        const verb = tier.action === 'shed' ? 'held · customers cut' : tier.action === 'storm' ? 'knocked out' : 'failed'
        const c = place({
          x: (a[0] + z[0]) / 2,
          y: (a[1] + z[1]) / 2 - 10 * upx,
          t: tier.t0,
          life: LINE_LIFE,
          people: Infinity,
          kind: 'line',
          pop: 1.15,
          lines: [{ text: `${what}${more} ${verb}${mw ? ` · ${fmt(mw)} MW` : ''}`, px: 12.5, weight: 700, color: 'line' }],
        })
        if (c) callouts.push(c)
      })
      if (o) {
        flares.push({ x: o[0], y: o[1], t: tier.t0, big: tier.kind === 'blast' || tier.kind === 'blackout' })
        if (tier.ring) rings.push({ x: o[0], y: o[1], r: tier.ring.km * mu, t0: tier.ring.t0, dur: tier.ring.dur, faint: tier.ring.faint })
      }
      // the lines overloaded at this step flare red as the front passes over them
      if (o && tier.ring && !tier.ring.faint && st?.hot) {
        const R = tier.ring.km * mu
        st.hot.forEach((hl) => {
          if (hl.pct < 100 || tier.lines.includes(hl.id)) return
          const b = branchById.get(hl.id)
          const a = b && xy(b.from_sub)
          const z = b && xy(b.to_sub)
          if (!a || !z || b.from_sub === b.to_sub) return
          const d = Math.hypot((a[0] + z[0]) / 2 - o[0], (a[1] + z[1]) / 2 - o[1])
          if (d > R) return
          lineFlares.push({ x1: a[0], y1: a[1], x2: z[0], y2: z[1], t: tier.ring.t0 + Math.pow(d / R, RING_POW) * tier.ring.dur })
        })
      }
      // each town the front lands on: its lights go white-hot, then ember; its call-out pops at its
      // biggest substation — the tier's biggest few stay, faintly, until the tier ends
      const big = new Set([...tier.hits].sort((p, q) => q.people - p.people).slice(0, KEEP).map((h) => h.area))
      tier.hits.forEach((h) => {
        let top = null
        h.subs.forEach((s) => {
          const p = xy(s.id)
          if (!p) return
          hits.push({ key: `${key}-h${s.id}`, x: p[0], y: p[1], delay: at(s.t) })
          const load = subById.get(s.id).load_mw || 0
          if (!top || load > top.load) top = { p, load }
        })
        if (!top || !(h.people > 0)) return
        const c = place({
          x: top.p[0],
          y: top.p[1] - 8 * upx,
          t: h.t,
          life: HIT_LIFE,
          keep: big.has(h.area) ? tier.t1 : 0,
          people: h.people,
          kind: 'hit',
          pop: 1 + Math.min(0.5, 0.1 + Math.max(0, Math.log10(h.people) - 3.5) * 0.18),
          lines: [
            { text: h.area, px: 12, weight: 700, color: 'ink' },
            { text: `−${fmt(h.people)} people`, px: calloutPx(h.people), weight: 800, color: 'hot' },
          ],
        })
        if (c) callouts.push(c)
      })
      // the darkness: sparks run along the real lines, the land goes black as it arrives; a wave
      // that takes power from more people gets a small call-out at its main substation
      tier.waves.forEach((wv) => {
        wv.paths.forEach((path) => {
          const pts = path.map(xy).filter(Boolean)
          if (pts.length >= 2) sparks.push({ pts, t0: wv.t0, t1: wv.t1 })
        })
        let main = null
        wv.subs.forEach((id) => {
          const p = xy(id)
          if (!p) return
          darks.push({ key: `${key}-k${id}`, x: p[0], y: p[1], delay: at(wv.t1) })
          const s = subById.get(id)
          if (!main || (s.load_mw || 0) > main.load) main = { p, load: s.load_mw || 0, area: s.area || nameOf(s).replace(/\s+\d+$/, '') }
        })
        const added = wv.hitDelta > 0 ? wv.hitDelta : wv.zoneDelta
        if (!main || !(added > 0)) return
        const c = place({
          x: main.p[0],
          y: main.p[1] - 6 * upx,
          t: wv.t1,
          life: WAVE_LIFE,
          people: added,
          kind: 'wave',
          pop: 1.15,
          lines: [{ text: `+${fmt(added)} · ${main.area}`, px: 13, weight: 800, color: 'dark' }],
        })
        if (c) callouts.push(c)
      })
      tier.darken.forEach((d) => {
        const p = xy(d.id)
        if (p) darks.push({ key: `${key}-k${d.id}`, x: p[0], y: p[1], delay: at(d.t) })
      })
    })
    return { snaps, hits, darks, canvas: { rings, flares, sparks, callouts, lineFlares } }
  }, [fx, lag, subById, branchById, cascade, project, k, host])

  if (!playing || !plan) return aim != null ? <Aim id={aim} k={k} /> : null
  const u = 1 / k // map units per screen px (roughly)
  return (
    <g className="fx bx" pointerEvents="none" aria-hidden="true" ref={attach}>
      {!reduced && host && <BlastCanvas fx={fx} items={plan.canvas} anchor={anchor} host={host} />}
      <defs>
        <radialGradient id="bx-black">
          <stop offset="0" className="bx-black__core" />
          <stop offset="0.45" className="bx-black__mid" />
          <stop offset="1" className="bx-black__edge" />
        </radialGradient>
        <radialGradient id="bx-ember">
          <stop offset="0" className="bx-ember__core" />
          <stop offset="1" className="bx-ember__edge" />
        </radialGradient>
      </defs>
      {plan.hits.map((h) => (
        <g key={h.key} transform={`translate(${h.x} ${h.y})`}>
          <circle className="bx-hit__glow" r={11 * u} fill="url(#bx-ember)" style={{ animationDelay: h.delay }} />
          <circle className="bx-hit" r={2.4 * u} style={{ animationDelay: h.delay }} />
        </g>
      ))}
      {/* the darkness arrives: the land around the light goes black, over its ember */}
      {plan.darks.map((d) => (
        <circle key={d.key} className="bx-dark" cx={d.x} cy={d.y} r={12 / Math.sqrt(k)} fill="url(#bx-black)" style={{ animationDelay: d.delay }} />
      ))}
      {plan.snaps.map((l) => (
        <line key={l.key} className="bx-snap" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} style={{ animationDelay: l.delay }} />
      ))}
    </g>
  )
}

// ------------------------------------------------------------------ the canvas layer
const TAU = Math.PI * 2
const lerp = (a, b, f) => a + (b - a) * f
const clamp01 = (v) => Math.min(1, Math.max(0, v))
const easeOut = (f) => 1 - (1 - f) ** 3
const LINE_FLARE_MS = 700
const FLARE_MS = 1000
const FONT = '"Archivo Variable", system-ui, sans-serif'

// A <canvas> over the map (portaled into GridMap's .map box, under the side panels), drawn per
// frame in map units: the frame's transform is the camera's screen matrix, read from the FX group.
function BlastCanvas({ fx, items, anchor, host }) {
  const ref = useRef(null)

  useEffect(() => {
    const cv = ref.current
    const g = anchor.current
    if (!cv || !g || !host) return undefined
    const ctx = cv.getContext('2d')
    // the sparks' polylines with cumulative lengths, to find the point at a share of the way
    const sparks = items.sparks.map((s) => {
      const cum = [0]
      for (let i = 1; i < s.pts.length; i++) cum.push(cum[i - 1] + Math.hypot(s.pts[i][0] - s.pts[i - 1][0], s.pts[i][1] - s.pts[i - 1][1]))
      return { ...s, cum, len: cum.at(-1) || 1 }
    })
    const end = fx.schedule.total + LINE_LIFE
    let raf = 0
    const frame = () => {
      const ms = performance.now() - fx.startedAt
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const w = Math.round(host.clientWidth * dpr)
      const h = Math.round(host.clientHeight * dpr)
      if (cv.width !== w || cv.height !== h) {
        cv.width = w
        cv.height = h
      }
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, w, h)
      const m = g.getScreenCTM()
      if (m) {
        const box = host.getBoundingClientRect()
        ctx.setTransform(dpr * m.a, dpr * m.b, dpr * m.c, dpr * m.d, dpr * (m.e - box.left), dpr * (m.f - box.top))
        draw(ctx, ms, 1 / (Math.hypot(m.a, m.b) || 1), items, sparks)
      }
      if (ms < end) raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(raf)
  }, [fx, items, host, anchor])

  return createPortal(<canvas ref={ref} className="bx-canvas" aria-hidden="true" />, host)
}

// the point `f` of the way along a spark's polyline
function along(s, f) {
  const d = clamp01(f) * s.len
  let i = 1
  while (i < s.cum.length - 1 && s.cum[i] < d) i++
  const q = (d - s.cum[i - 1]) / (s.cum[i] - s.cum[i - 1] || 1)
  return [lerp(s.pts[i - 1][0], s.pts[i][0], q), lerp(s.pts[i - 1][1], s.pts[i][1], q)]
}

// One frame, in map units (`px` = map units per screen px).
function draw(ctx, ms, px, { rings, flares, callouts, lineFlares }, sparks) {
  // the blast fronts: r = R * u^(1 / RING_POW), the schedule's own law, so each town lands as it's reached
  for (const r of rings) {
    const e = ms - r.t0
    if (e < 0 || e > r.dur) continue
    const u = e / r.dur
    const R = Math.max(r.r * Math.pow(u, 1 / RING_POW), 0.01)
    const a = u < 0.7 ? 1 : 1 - (u - 0.7) / 0.3
    if (!r.faint && R > 0.5) {
      const grad = ctx.createRadialGradient(r.x, r.y, R * 0.62, r.x, r.y, R)
      grad.addColorStop(0, 'rgba(255, 61, 94, 0)')
      grad.addColorStop(0.88, `rgba(255, 61, 94, ${0.2 * a})`)
      grad.addColorStop(1, 'rgba(255, 61, 94, 0)')
      ctx.fillStyle = grad
      ctx.beginPath()
      ctx.arc(r.x, r.y, R, 0, TAU)
      ctx.fill()
    }
    ctx.beginPath()
    ctx.arc(r.x, r.y, R, 0, TAU)
    ctx.lineWidth = (r.faint ? 8 : 16) * px
    ctx.strokeStyle = r.faint ? `rgba(255, 176, 58, ${0.12 * a})` : `rgba(255, 61, 94, ${0.24 * a})`
    ctx.stroke()
    ctx.lineWidth = (r.faint ? 1.2 : 2.4) * px
    ctx.strokeStyle = r.faint ? `rgba(255, 176, 58, ${0.55 * a})` : `rgba(255, 228, 233, ${0.92 * a})`
    ctx.stroke()
  }
  // the failure point flares white
  for (const f of flares) {
    const dur = f.big ? FLARE_MS : FLARE_MS * 0.75
    const e = ms - f.t
    if (e < 0 || e > dur) continue
    const q = e / dur
    const R = (f.big ? 32 : 16) * px * lerp(0.08, 1, easeOut(q))
    const grad = ctx.createRadialGradient(f.x, f.y, 0, f.x, f.y, R)
    grad.addColorStop(0, `rgba(255, 255, 255, ${0.95 * (1 - q)})`)
    grad.addColorStop(0.4, `rgba(255, 214, 222, ${0.6 * (1 - q)})`)
    grad.addColorStop(1, 'rgba(255, 61, 94, 0)')
    ctx.fillStyle = grad
    ctx.beginPath()
    ctx.arc(f.x, f.y, R, 0, TAU)
    ctx.fill()
  }
  // the lines the darkness ran along go dead behind it
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.lineWidth = 2.4 * px
  ctx.strokeStyle = 'rgba(12, 2, 5, 0.6)'
  ctx.beginPath()
  for (const s of sparks) {
    if (ms < s.t0) continue
    const f = Math.min(1, (ms - s.t0) / (s.t1 - s.t0 || 1))
    ctx.moveTo(s.pts[0][0], s.pts[0][1])
    for (let i = 1; i < s.pts.length && s.cum[i] <= f * s.len; i++) ctx.lineTo(s.pts[i][0], s.pts[i][1])
    const e = along(s, f)
    ctx.lineTo(e[0], e[1])
  }
  ctx.stroke()
  // the head of the darkness: a white-hot spark running along each line
  for (const s of sparks) {
    if (ms < s.t0 || ms > s.t1) continue
    const f = (ms - s.t0) / (s.t1 - s.t0 || 1)
    const a = along(s, f - 0.22)
    const b = along(s, f)
    ctx.beginPath()
    ctx.moveTo(a[0], a[1])
    ctx.lineTo(b[0], b[1])
    ctx.lineWidth = 7 * px
    ctx.strokeStyle = 'rgba(255, 61, 94, 0.35)'
    ctx.stroke()
    ctx.lineWidth = 2.4 * px
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.95)'
    ctx.stroke()
  }
  // the overloaded lines the front passes over flare red in its wake
  for (const l of lineFlares) {
    const e = ms - l.t
    if (e < 0 || e > LINE_FLARE_MS) continue
    const a = 1 - e / LINE_FLARE_MS
    ctx.beginPath()
    ctx.moveTo(l.x1, l.y1)
    ctx.lineTo(l.x2, l.y2)
    ctx.lineWidth = 9 * px
    ctx.strokeStyle = `rgba(255, 61, 94, ${0.4 * a})`
    ctx.stroke()
    ctx.lineWidth = 2.4 * px
    ctx.strokeStyle = `rgba(255, 220, 226, ${0.9 * a})`
    ctx.stroke()
  }
  // call-outs: what the blast hit, where it hit it — pop in (bigger for bigger hits), drift, fade
  ctx.textAlign = 'center'
  ctx.lineJoin = 'round'
  if ('fontStretch' in ctx) ctx.fontStretch = 'condensed'
  for (const c of callouts) {
    const e = ms - c.t
    if (e < 0) continue
    const kept = c.keep > c.t + c.life && ms < c.keep + 350
    if (e > c.life && !kept) continue
    const q = Math.min(1, e / c.life)
    const pop = e < 110 ? lerp(0.45, c.pop, e / 110) : e < 330 ? lerp(c.pop, 1, (e - 110) / 220) : 1
    const faint = c.keep > c.t + c.life ? 0.38 : 0
    let alpha = q < 0.65 ? 1 : lerp(1, faint, (q - 0.65) / 0.35)
    if (e >= c.life) alpha = ms < c.keep ? faint : faint * (1 - (ms - c.keep) / 350)
    if (alpha <= 0.01) continue
    const heat = clamp01((e - 90) / 700)
    if (c.lead != null) {
      ctx.globalAlpha = alpha * 0.6
      ctx.beginPath()
      ctx.moveTo(c.x, c.lead + 8 * px)
      ctx.lineTo(c.x, c.y)
      ctx.lineWidth = 1 * px
      ctx.strokeStyle = '#e6edf7'
      ctx.stroke()
    }
    ctx.save()
    ctx.translate(c.x, c.y - 18 * px * easeOut(q))
    ctx.scale(pop, pop)
    ctx.globalAlpha = alpha
    let y = 0
    for (let i = c.lines.length - 1; i >= 0; i--) {
      const l = c.lines[i]
      ctx.font = `${l.weight} ${l.px * px}px ${FONT}`
      ctx.lineWidth = (l.px > 16 ? 4.2 : 3.4) * px
      ctx.strokeStyle = 'rgba(5, 10, 20, 0.95)'
      ctx.strokeText(l.text, 0, y)
      ctx.fillStyle =
        l.color === 'hot'
          ? `rgb(255, ${Math.round(lerp(255, 61, heat))}, ${Math.round(lerp(255, 94, heat))})`
          : l.color === 'line'
            ? `rgb(255, ${Math.round(lerp(255, 150, heat))}, ${Math.round(lerp(255, 160, heat))})`
            : l.color === 'dark'
              ? '#ff8a9b'
              : '#e6edf7'
      ctx.fillText(l.text, 0, y)
      y -= l.px * 1.1 * px
    }
    ctx.restore()
  }
  ctx.globalAlpha = 1
}

// Before the run: the line a row of "Where the people are" points at, lit on the map.
function Aim({ id, k }) {
  const { branchById, subById } = useOverload()
  const { project } = useMapView()
  const b = branchById.get(id)
  const a = b && subById.get(b.from_sub)
  const z = b && subById.get(b.to_sub)
  if (!a || !z) return null
  const [x1, y1] = project(a.lon, a.lat)
  const [x2, y2] = project(z.lon, z.lat)
  return (
    <g className="bx-aim" pointerEvents="none" aria-hidden="true">
      {b.from_sub === b.to_sub ? (
        <circle className="bx-aim__ring" cx={x1} cy={y1} r={9 / k} />
      ) : (
        <>
          <line className="bx-aim__glow" x1={x1} y1={y1} x2={x2} y2={y2} />
          <line className="bx-aim__line" x1={x1} y1={y1} x2={x2} y2={y2} />
        </>
      )}
    </g>
  )
}
