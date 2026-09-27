import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { money } from '../features/cost/money'
import { useLossRate } from '../features/impact/caseCost'
import { useHospitals } from '../features/hospitals/hospitalsApi'
import { AFTERGLOW_MS, buildFires, drawFires } from '../features/impact/fireFx'
import { byIntensity, leapIntensity } from '../features/impact/intensity'
import { textWidth, useReducedMotion } from '../features/impact/towns'
import { useMapView } from '../GridMap'
import { HEIGHT, WIDTH, fmt, muPerKm } from '../geo'
import { useOverload } from '../store'
import './bomb.css'
import { RING_POW, titleCase } from './cascadeSchedule'

// The blast, in slow motion. While a cascade plays, the schedule (the store's `fx.schedule`,
// shell/cascadeSchedule.js) is drawn without a React render per frame, in two layers:
//   SVG, inside the map (below the labels): every effect laid down ONCE as a CSS animation whose
//     delay is its time in the schedule — the failed line snaps white-hot and dies; every light the
//     blast front passes flashes and settles to red; as the darkness arrives, the land around each light
//     is cross-hatched red (the hatch is this layer's own <pattern>, also used by the base map's dark areas).
//     Kept few: each one repaints the heavy map under it.
//   Canvas, over the map (BlastCanvas): what moves across large areas — the blast front expanding in
//     slow motion, the failure point's flare, the sparks running along the real lines at the head of
//     the darkness, and the quiet labels: "Naples  329,740 people  $281 million" appears the moment the
//     front reaches a town and stays until its step ends (the money is the town's people times the
//     case's cost per person: features/impact/caseCost.js; people alone until the estimate lands).
//     Drawn per frame from the same clock as the counter; on a canvas they don't make the browser
//     repaint the heavy map under them.
//   Fire (features/impact/fireFx.js, drawn by the same canvas): an arc, a white flash and sparks at the
//     instant a line trips, then flames, embers and a smoke plume where it burns, sparks where the front
//     passes a light, smoldering hot spots where the power goes out and a pulsing red cross at each
//     hospital that goes dark. Sizes follow the element (kV, people) AND the incident-relative
//     intensity (features/impact/intensity.js), so a small cascade burns at about half a big one's size.
//     The fire and smolder linger for AFTERGLOW_MS after the replay ends, fading out.
// Paused or scrubbed, both are off and the map shows the exact step (the store's view).
// Sizes: SVG stroke widths are screen px (non-scaling strokes); radii and text are divided by the zoom.

const CROWD_MIN = 5000 // a label under this many people is dropped when there's no room for it
const LABELS_PER_TIER = 6 // the biggest hits of a step get a label; the rest are in the counter and the feed
const LINE_LIFE = 2400 // a failed line's label, at most (it also goes when its step ends)
const FADE_IN = 160
const FADE_OUT = 450
const HATCH_PX = 5 // the dark areas' hatch: screen px between lines at any zoom
const nameOf = (s) => titleCase(s?.name || '')
// GridMap's zoom buckets (GridLayers): its dark areas are 7 / sqrt(bucket) map units, the replay's match them
const bucketOf = (k) => (k < 1.5 ? 1 : k < 2.5 ? 2 : k < 4 ? 3 : k < 6 ? 5 : 8)

// label parts: the town (quiet), its people (red: what is lost) and their share of the cost
const NAME_PX = 12
const FIG_PX = 13
const PART_GAP = 7

export default function CascadeFX() {
  const { fx: live, playing, subById, branchById, cascade, step, region, view } = useOverload()
  const { k, project } = useMapView()
  const [aim, setAim] = useState(null) // a line the impact panel points at before the run (hover)
  const reduced = useReducedMotion()
  // $ per person hit (null until the case is priced): the labels read it through a ref, so the estimate
  // can land mid-replay without rebuilding anything
  const rate = useLossRate()
  const rateRef = useRef(rate)
  useEffect(() => {
    rateRef.current = rate
  }, [rate])
  // The replay that just ran to its end stays on screen for its afterglow: the fire dies back and the
  // hot spots smolder out (the store clears `fx` the moment the last step lands).
  const [last, setLast] = useState(null)
  if (live && last?.fx !== live) setLast({ fx: live, cascade })
  const finished = !live && !!last && !!cascade && last.cascade === cascade && step >= (cascade.steps?.length || 0)
  const [gone, setGone] = useState(null)
  useEffect(() => {
    if (!finished) return undefined
    const t = setTimeout(() => setGone(last.fx), AFTERGLOW_MS + 400)
    return () => clearTimeout(t)
  }, [finished, last])
  const fx = live || (finished && gone !== last.fx ? last.fx : null)
  const running = !!(live && playing)
  // the region's hospitals, only while a replay is on screen (LAZY: nothing is fetched on load or when the state changes)
  const hospitals = useHospitals(fx ? region : null).data?.hospitals
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

  // The areas already dark when this replay starts (a replay resumed mid-way, a storm that cut places off
  // first): while the replay plays the base map's dark areas are hidden and this layer draws every one
  // (bomb.css), so its hatch never stacks on the base map's. Read once per run, on purpose.
  const darkAtStart = useMemo(
    () => (fx && view ? Object.keys(view.subClasses).filter((id) => view.subClasses[id] === 'sub--dark').map(Number) : []),
    [fx], // eslint-disable-line react-hooks/exhaustive-deps -- the view when this run started, not later steps
  )

  // everything that burns, with its time (features/impact/fireFx.js) — once per run
  const fires = useMemo(() => (fx ? buildFires({ schedule: fx.schedule, subById, branchById, project, hospitals }) : null), [fx, subById, branchById, project, hospitals])

  // everything the replay will draw, positioned in map units, with its time — once per run
  // (and again if the zoom changes: sizes and the labels' spots depend on it)
  const plan = useMemo(() => {
    if (!fx) return null
    const at = (t) => `${Math.round(t - lag)}ms`
    const mu = muPerKm()
    const xy = (id) => {
      const s = subById.get(id)
      return s ? project(s.lon, s.lat) : null
    }
    // Labels must not land on each other: each is nudged up or down from its spot until it's clear of
    // the ones on screen at the same time. Sizes in real screen px: the map box fits the view
    // (WIDTH x HEIGHT map units), then the zoom.
    const base = host ? Math.min(host.clientWidth / WIDTH, host.clientHeight / HEIGHT) || 1 : 1
    const upx = 1 / (k * base)
    const alive = []
    const place = (c, force) => {
      const w = c.wPx * upx + 16 * upx
      const h = 17 * upx
      const t0 = c.t
      const t1 = c.until + FADE_OUT
      const box = (y) => ({ x0: c.x - w / 2, x1: c.x + w / 2, y0: y - h, y1: y + 5 * upx, t0, t1 })
      const free = (b) => !alive.some((a) => a.t0 < b.t1 && b.t0 < a.t1 && a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1)
      // at most LABELS_PER_TIER town labels on screen at once (REVIEW-1 #6: 14 stacked rows mid-replay)
      if (c.kind === 'hit' && !force && alive.filter((a) => a.hit && a.t0 < t1 && t0 < a.t1).length >= LABELS_PER_TIER) return null
      for (const n of [0, -1, 1, -2, 2, -3, 3, -4]) {
        const y = c.y + n * (h + 3 * upx)
        const b = { ...box(y), hit: c.kind === 'hit' }
        if (free(b)) {
          alive.push(b)
          return { ...c, y, lead: n !== 0 ? c.y : null, box: b }
        }
      }
      // no room: a small hit waits for the counter and the feed; a step's biggest hit (and a failed line) is drawn anyway
      if (!force || c.people < CROWD_MIN) return null
      const b = { ...box(c.y), hit: c.kind === 'hit' }
      alive.push(b)
      return { ...c, box: b }
    }
    // ONE label per town (REVIEW-1 #6: the same town twice with different numbers): a town's label reads
    // its running total, and a later hit on a town whose label is still up replaces it on the same spot
    const townSum = new Map()
    const townLabel = new Map()
    const townHit = (area, people, I, x, y, t, until, force) => {
      const sum = (townSum.get(area) || 0) + people
      townSum.set(area, sum)
      const prev = townLabel.get(area)
      if (prev && prev.until + FADE_OUT > t) {
        prev.until = Math.max(prev.t, t - FADE_OUT)
        if (prev.box) prev.box.t1 = t
        const c = { ...hitLabel(area, sum, I), x: prev.x, y: prev.y, lead: prev.lead, t, until: Math.max(until, t) }
        const w = c.wPx * upx + 16 * upx
        const b = { x0: c.x - w / 2, x1: c.x + w / 2, y0: c.y - 17 * upx, y1: c.y + 5 * upx, t0: t, t1: c.until + FADE_OUT, hit: true }
        alive.push(b)
        const out = { ...c, box: b }
        townLabel.set(area, out)
        return out
      }
      const c = place({ ...hitLabel(area, sum, I), x, y, t, until }, force)
      if (c) townLabel.set(area, c)
      return c
    }
    // a hit's label: the town, its people and (once priced) their share of the cost — measured with room for the money
    const hitLabel = (area, people, I) => {
      const fig = byIntensity(I, FIG_PX, FIG_PX + 2)
      const wPx =
        textWidth(area, NAME_PX, 600, 'condensed') + textWidth(`${fmt(people)} people`, fig, 700, 'condensed') + textWidth('$888 million', fig, 700, 'condensed') + 2 * PART_GAP
      return { kind: 'hit', area, people, fig, wPx }
    }
    const snaps = []
    const hits = []
    const darks = []
    const rings = []
    const flares = []
    const sparks = []
    const labels = []
    const lineFlares = []
    const rDark = 7 / Math.sqrt(bucketOf(k))
    const seen = new Set()
    darkAtStart.forEach((id) => {
      const p = xy(id)
      if (!p) return
      seen.add(id)
      darks.push({ key: `d0-${id}`, x: p[0], y: p[1], delay: '-2000ms', r: rDark })
    })
    const dark = (key, id, t) => {
      if (seen.has(id)) return
      const p = xy(id)
      if (!p) return
      seen.add(id)
      darks.push({ key, x: p[0], y: p[1], delay: at(t), r: rDark })
    }
    const total = fx.schedule.incident?.hit ?? 0
    fx.schedule.tiers.forEach((tier) => {
      const key = `s${tier.step}`
      const st = cascade?.steps?.[tier.step - 1]
      const o = tier.origin && project(tier.origin.lon, tier.origin.lat)
      // SNAP: the failed line flashes, with its label at its midpoint
      tier.lines.forEach((bid, i) => {
        const b = branchById.get(bid)
        const a = b && xy(b.from_sub)
        const z = b && xy(b.to_sub)
        if (!a || !z) return
        if (b.from_sub !== b.to_sub) snaps.push({ key: `${key}-${bid}`, x1: a[0], y1: a[1], x2: z[0], y2: z[1], delay: at(tier.t0) })
        if (i > 0) return // a storm's many lines: one label
        const from = subById.get(b.from_sub)
        const what = b.from_sub === b.to_sub ? `${nameOf(from)} transformer` : `${nameOf(from)} → ${nameOf(subById.get(b.to_sub))}`
        const mw = st?.carried?.[0]?.mw
        const more = tier.lines.length > 1 ? ` + ${tier.lines.length - 1} more lines` : ''
        const verb = tier.action === 'shed' ? 'held, customers cut' : tier.action === 'storm' ? 'knocked out' : 'failed'
        const text = `${what}${more} ${verb}${mw ? ` · ${fmt(mw)} MW` : ''}`
        const c = place(
          {
            kind: 'line',
            text,
            x: (a[0] + z[0]) / 2,
            y: (a[1] + z[1]) / 2 - 10 * upx,
            t: tier.t0,
            until: Math.min(tier.t0 + LINE_LIFE, tier.t1),
            people: Infinity,
            wPx: textWidth(text, 12, 600, 'condensed'),
          },
          true,
        )
        if (c) labels.push(c)
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
      // each town the front lands on: its lights flash and settle to red; the step's biggest few get a
      // label at their biggest substation, from the moment the front lands until the step ends
      const top = new Set([...tier.hits].sort((p, q) => q.people - p.people).slice(0, LABELS_PER_TIER).map((h) => h.area))
      const biggest = tier.hits.reduce((m, h) => (!m || h.people > m.people ? h : m), null)
      tier.hits.forEach((h) => {
        let spot = null
        h.subs.forEach((s) => {
          const p = xy(s.id)
          if (!p) return
          hits.push({ key: `${key}-h${s.id}`, x: p[0], y: p[1], delay: at(s.t) })
          const load = subById.get(s.id).load_mw || 0
          if (!spot || load > spot.load) spot = { p, load }
        })
        if (!spot || !(h.people > 0) || !top.has(h.area)) return
        const c = townHit(h.area, h.people, leapIntensity(h.people, total), spot.p[0], spot.p[1] - 8 * upx, h.t, tier.t1, h === biggest)
        if (c) labels.push(c)
      })
      // the darkness: sparks run along the real lines, the land is hatched as it arrives; a wave that
      // takes power from more people gets a label at its main substation
      tier.waves.forEach((wv) => {
        wv.paths.forEach((path) => {
          const pts = path.map(xy).filter(Boolean)
          if (pts.length >= 2) sparks.push({ pts, t0: wv.t0, t1: wv.t1 })
        })
        let main = null
        wv.subs.forEach((id) => {
          dark(`${key}-k${id}`, id, wv.t1)
          const p = xy(id)
          if (!p) return
          const s = subById.get(id)
          if (!main || (s.load_mw || 0) > main.load) main = { p, load: s.load_mw || 0, area: s.area || nameOf(s).replace(/\s+\d+$/, '') }
        })
        const added = wv.hitDelta > 0 ? wv.hitDelta : wv.zoneDelta
        if (!main || !(added > 0)) return
        const c = townHit(main.area, added, leapIntensity(added, total), main.p[0], main.p[1] - 6 * upx, wv.t1, tier.t1, false)
        if (c) labels.push(c)
      })
      tier.darken.forEach((d) => dark(`${key}-k${d.id}`, d.id, d.t))
    })
    return { snaps, hits, darks, canvas: { rings, flares, sparks, labels, lineFlares } }
  }, [fx, lag, subById, branchById, cascade, project, k, host, darkAtStart])

  // the hatch for areas that lose power (always defined: the base map's dark areas use it too, index.css)
  const hs = HATCH_PX / k
  const defs = (
    <defs>
      <pattern id="bx-hatch" patternUnits="userSpaceOnUse" width={hs} height={hs} patternTransform="rotate(45)">
        <rect className="bx-hatch__bg" width={hs} height={hs} />
        <line className="bx-hatch__ln" x1={hs / 2} y1={0} x2={hs / 2} y2={hs} strokeWidth={1.1 / k} />
      </pattern>
    </defs>
  )
  if (!fx || !plan)
    return (
      <>
        {defs}
        {aim != null && <Aim id={aim} k={k} />}
      </>
    )
  const u = 1 / k // map units per screen px (roughly)
  // the same canvas carries on into the afterglow (no remount, so no gap): only the SVG effects stop
  return (
    <>
      {defs}
      <g className={running ? 'fx bx' : 'fx bxa'} pointerEvents="none" aria-hidden="true" ref={attach}>
        {host && <BlastCanvas fx={fx} items={plan.canvas} fires={fires} anchor={anchor} host={host} still={reduced} rateRef={rateRef} />}
        {running && <Sfx plan={plan} u={u} />}
      </g>
    </>
  )
}

// The replay's SVG effects: laid down once, each a CSS animation whose delay is its time in the schedule.
function Sfx({ plan, u }) {
  return (
    <>
      {/* the darkness arrives: the land around each light is hatched red (one group, so overlaps don't stack) */}
      <g className="bx-darks">
        {plan.darks.map((d) => (
          <circle key={d.key} className="bx-dark" cx={d.x} cy={d.y} r={d.r} style={{ animationDelay: d.delay }} />
        ))}
      </g>
      {plan.hits.map((h) => (
        <g key={h.key} transform={`translate(${h.x} ${h.y})`}>
          <circle className="bx-hit__mark" r={7 * u} style={{ animationDelay: h.delay }} />
          <circle className="bx-hit" r={2.2 * u} style={{ animationDelay: h.delay }} />
        </g>
      ))}
      {plan.snaps.map((l) => (
        <line key={l.key} className="bx-snap" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} style={{ animationDelay: l.delay }} />
      ))}
    </>
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
const INK = '#dfe6f0'
const INK_2 = '#a9b6c8'
const LOSS = '#ff4d68'

// A <canvas> over the map (portaled into GridMap's .map box, under the side panels), drawn per
// frame in map units: the frame's transform is the camera's screen matrix, read from the FX group.
function BlastCanvas({ fx, items, fires, anchor, host, still, rateRef }) {
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
    // with fire the canvas carries on past the replay: the flames die back and the hot spots smolder out
    const end = fx.schedule.total + (fires?.any ? AFTERGLOW_MS : LINE_LIFE)
    const o = { total: fx.schedule.total, end, reduced: !!still, cw: 0, ch: 0, dpr: 1, cull: { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0, w: 0, h: 0 }, rateRef, bitmaps: new Map() }
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
        o.cw = w
        o.ch = h
        o.dpr = dpr
        Object.assign(o.cull, { a: m.a, b: m.b, c: m.c, d: m.d, e: m.e - box.left, f: m.f - box.top, w: host.clientWidth, h: host.clientHeight })
        // reduced motion: static fire markers only, no moving layers
        const px = 1 / (Math.hypot(m.a, m.b) || 1)
        if (still) drawFires(ctx, ms, px, fires, o)
        else draw(ctx, ms, px, items, sparks, fires, o)
      }
      if (ms < end) raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(raf)
  }, [fx, items, fires, host, anchor, still, rateRef])

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

// A label drawn once into its own small canvas (text over a soft dark scrim, no outline, no box) and then
// just copied each frame. `parts` = [{text, px, weight, color}] on one line.
function labelBitmap(parts, dpr) {
  const PAD_X = 14
  const PAD_Y = 11
  const c0 = document.createElement('canvas').getContext('2d')
  if ('fontStretch' in c0) c0.fontStretch = 'condensed'
  const widths = parts.map((p) => {
    c0.font = `${p.weight} ${p.px}px ${FONT}`
    return c0.measureText(p.text).width
  })
  const tw = widths.reduce((a, b) => a + b, 0) + PART_GAP * (parts.length - 1)
  const th = Math.max(...parts.map((p) => p.px))
  const W = tw + PAD_X * 2
  const H = th + PAD_Y * 2
  const cv = document.createElement('canvas')
  cv.width = Math.ceil(W * dpr)
  cv.height = Math.ceil(H * dpr)
  const c = cv.getContext('2d')
  c.scale(dpr, dpr)
  // the scrim: a rounded dark shape blurred to nothing at its edges (drawn off-canvas, only its shadow lands)
  const OFF = 10000
  c.shadowColor = 'rgba(3, 7, 14, 0.72)'
  c.shadowBlur = 9 * dpr
  c.shadowOffsetX = OFF * dpr
  c.fillStyle = '#000'
  c.beginPath()
  c.roundRect?.(PAD_X - 5 - OFF, PAD_Y - 3, tw + 10, th + 6, (th + 6) / 2)
  if (!c.roundRect) c.rect(PAD_X - 5 - OFF, PAD_Y - 3, tw + 10, th + 6)
  c.fill()
  c.fill() // twice: a denser middle, the same soft edge
  c.shadowColor = 'transparent'
  c.shadowOffsetX = 0
  c.shadowBlur = 0
  if ('fontStretch' in c) c.fontStretch = 'condensed'
  c.textBaseline = 'alphabetic'
  let x = PAD_X
  const base = PAD_Y + th * 0.82
  parts.forEach((p, i) => {
    c.font = `${p.weight} ${p.px}px ${FONT}`
    c.fillStyle = p.color
    c.fillText(p.text, x, base)
    x += widths[i] + PART_GAP
  })
  return { cv, w: W, h: H, base }
}

// the parts of a label, with the money when the case is priced
function labelParts(l, rate) {
  if (l.kind === 'line') return [{ text: l.text, px: 12, weight: 600, color: INK_2 }]
  const parts = [
    { text: l.area, px: NAME_PX, weight: 600, color: INK },
    { text: `${fmt(l.people)} people`, px: l.fig, weight: 700, color: LOSS },
  ]
  if (rate) parts.push({ text: money(l.people * rate.high), px: l.fig, weight: 700, color: INK })
  return parts
}

// One frame, in map units (`px` = map units per screen px).
function draw(ctx, ms, px, { rings, flares, labels, lineFlares }, sparks, fires, o) {
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
      grad.addColorStop(0.88, `rgba(255, 61, 94, ${0.16 * a})`)
      grad.addColorStop(1, 'rgba(255, 61, 94, 0)')
      ctx.fillStyle = grad
      ctx.beginPath()
      ctx.arc(r.x, r.y, R, 0, TAU)
      ctx.fill()
    }
    ctx.beginPath()
    ctx.arc(r.x, r.y, R, 0, TAU)
    ctx.lineWidth = (r.faint ? 8 : 14) * px
    ctx.strokeStyle = r.faint ? `rgba(255, 176, 58, ${0.1 * a})` : `rgba(255, 61, 94, ${0.2 * a})`
    ctx.stroke()
    ctx.lineWidth = (r.faint ? 1 : 1.8) * px
    ctx.strokeStyle = r.faint ? `rgba(255, 176, 58, ${0.5 * a})` : `rgba(255, 222, 228, ${0.85 * a})`
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
  // fire, sparks, smoke and hot spots (features/impact/fireFx.js), under the labels
  drawFires(ctx, ms, px, fires, o)
  drawLabels(ctx, ms, px, labels, o)
}

// The labels: quiet text on a soft scrim, in screen px. Each appears as the front reaches its town, stays
// until its step ends, then fades; a label nudged off its town keeps a hairline back to it.
function drawLabels(ctx, ms, px, labels, o) {
  const rate = o.rateRef?.current || null
  const m = o.cull
  const dpr = o.dpr
  const toScreen = (x, y) => [m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f]
  for (const l of labels) {
    const e = ms - l.t
    // the last step's labels hand over to the map's own town labels (ImpactLayer) the moment the replay ends
    const last = l.until >= o.total - 1
    if (e < 0 || ms > l.until + (last ? 0 : FADE_OUT)) continue
    const alpha = e < FADE_IN ? e / FADE_IN : ms > l.until ? 1 - (ms - l.until) / FADE_OUT : 1
    if (alpha <= 0.01) continue
    const [sx, sy] = toScreen(l.x, l.y)
    if (sx < -300 || sy < -60 || sx > m.w + 300 || sy > m.h + 60) continue
    const parts = labelParts(l, rate)
    const id = `${parts.map((p) => p.text).join('|')}@${dpr}`
    let bm = o.bitmaps.get(id)
    if (!bm) {
      bm = labelBitmap(parts, dpr)
      o.bitmaps.set(id, bm)
    }
    if (l.lead != null) {
      ctx.globalAlpha = alpha * 0.45
      ctx.beginPath()
      ctx.moveTo(l.x, l.lead + 3 * px)
      ctx.lineTo(l.x, l.y + 2 * px)
      ctx.lineWidth = 1 * px
      ctx.strokeStyle = INK_2
      ctx.stroke()
    }
    ctx.save()
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.globalAlpha = alpha
    ctx.drawImage(bm.cv, sx - bm.w / 2, sy - bm.base, bm.w, bm.h)
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
          <line className="bx-aim__under" x1={x1} y1={y1} x2={x2} y2={y2} />
          <line className="bx-aim__line" x1={x1} y1={y1} x2={x2} y2={y2} />
        </>
      )}
    </g>
  )
}
