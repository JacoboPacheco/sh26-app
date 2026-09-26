import { useEffect, useMemo, useRef } from 'react'
import { project } from '../../geo'
import { useOverload } from '../../store'
import IntroCurtain from './IntroCurtain'
import './flow.css'

// FEATURE: the living grid (flow track).
// A <canvas> over the map (pointer-events: none) that animates electricity as short bright dashes
// moving along every drawn branch. Direction = the sign of view.flow[i] (from -> to positive);
// speed and density grow with |flow| against the branch's rating, clamped to the line's color
// class so the dashes always agree with the SVG line under them. Overloaded lines stream red,
// strained ones amber, tripped ones carry nothing, and lines touching a dark substation fade out.
//
// Mounted by App.jsx through GridMap's `overlay`; it also hosts the intro's curtain (IntroCurtain).
// Map units -> canvas pixels comes from gRef.current.getScreenCTM() read every frame, so the dashes
// follow the camera mid-transition, while panning and while zooming. The grid layers never
// re-render for this: all motion is here.
//
// Budget: ~2,150 segments at <= 4 ms a frame. Geometry is precomputed once per grid in map units;
// each frame transforms the endpoints, clips each segment to the viewport (Liang-Barsky), emits
// only the visible dashes into per-style buffers, and strokes each buffer twice (glow + core).

const LEVELS = 8 // alpha steps, so a fade batches into a few strokes instead of one per segment
const FADE_S = 0.8 // matches the halo transition in index.css
const MARGIN = 16 // CSS px outside the canvas still drawn, so dashes don't pop at the edge
const DPR_MAX = 2
const DIM_HEADROOM = 0.22 // the headroom heatmap dims the lines; the dashes follow

// line class -> [min, max] of the loading ratio used for motion, and the dash color / brightness.
// The map is quiet (index.css): electricity on a calm line is pale and faint, not a highlight;
// color and glow only where something is wrong (amber near the limit, red past it).
const CLASS = {
  'ln--calm': { lo: 0, hi: 0.5, color: 0, alpha: 0.4 },
  'ln--warm': { lo: 0.5, hi: 0.8, color: 0, alpha: 0.6 },
  'ln--hot': { lo: 0.8, hi: 1, color: 1, alpha: 0.9 },
  'ln--over': { lo: 1, hi: 1.6, color: 2, alpha: 1 },
}
const TOKENS = ['--current', '--strain', '--overload'] // color 0, 1, 2
const CORE_W = [
  [0.9, 1.3], // pale current: low kV, high kV (CSS px)
  [1.5, 1.9], // amber
  [2.2, 2.4], // red
]
const GLOW = [0.07, 0.2, 0.28] // the wide faint pass under each dash, per color

// ratio of |flow| to rating -> pixels between dashes, pixels per second, dash length
function motion(u) {
  if (u > 1) {
    const x = Math.min((u - 1) / 0.6, 1)
    return [13, 120 + 70 * x, 7.5]
  }
  const e = Math.pow(u, 0.7)
  return [60 - 44 * e, 8 + 64 * Math.pow(u, 1.5), 2.5 + 4.5 * u]
}

export default function FlowCanvas({ gRef }) {
  const { grid, view, headroomOn, headroom, upgrades } = useOverload()
  const canvasRef = useRef(null)
  const geomRef = useRef(null)
  const dynRef = useRef(null)

  // ------------------------------------------------------------ geometry, once per grid (map units)
  const geom = useMemo(() => {
    if (!grid) return null
    const xy = new Map(grid.subs.map((s) => [s.id, project(s.lon, s.lat)]))
    const idx = []
    grid.branches.forEach((b, i) => {
      if (b.from_sub !== b.to_sub && xy.has(b.from_sub) && xy.has(b.to_sub)) idx.push(i)
    })
    const n = idx.length
    const g = {
      n,
      idx: Int32Array.from(idx),
      x1: new Float32Array(n),
      y1: new Float32Array(n),
      x2: new Float32Array(n),
      y2: new Float32Array(n),
      hi: new Uint8Array(n),
      rate: new Float32Array(n),
      bid: new Int32Array(n), // branch id (Fix it's upgrades are keyed by it)
      from: new Int32Array(n),
      to: new Int32Array(n),
      // per-segment state carried across frames
      off: new Float32Array(n), // px travelled, modulo the gap
      alpha: new Float32Array(n), // current brightness, easing toward the target
    }
    idx.forEach((i, j) => {
      const b = grid.branches[i]
      const [ax, ay] = xy.get(b.from_sub)
      const [bx, by] = xy.get(b.to_sub)
      g.x1[j] = ax
      g.y1[j] = ay
      g.x2[j] = bx
      g.y2[j] = by
      g.hi[j] = b.kv >= 300 ? 1 : 0
      g.rate[j] = Math.max(b.rate_mva, 1)
      g.bid[j] = b.id
      g.from[j] = b.from_sub
      g.to[j] = b.to_sub
      g.off[j] = ((j * 0.618034) % 1) * 60 // scatter the starting phase
    })
    return g
  }, [grid])

  // ------------------------------------------------------------ motion, once per view change
  const dimmed = !!(headroomOn && headroom)
  const dyn = useMemo(() => {
    if (!geom || !view) return null
    const { n, idx, rate, bid, from, to } = geom
    const d = {
      dir: new Int8Array(n),
      gap: new Float32Array(n),
      speed: new Float32Array(n),
      dash: new Float32Array(n),
      color: new Uint8Array(n),
      target: new Float32Array(n),
      snap: new Uint8Array(n), // 1 = go dark at once (a tripped line), no fade
    }
    const dark = view.subClasses
    for (let j = 0; j < n; j++) {
      const i = idx[j]
      const cls = view.lineClasses[i]
      const f = view.flow?.[i] || 0
      if (cls === 'ln--tripped') {
        d.snap[j] = 1
        continue
      }
      const c = CLASS[cls] || CLASS['ln--calm']
      if (Math.abs(f) < 0.5) continue // no flow, no direction: nothing moves
      const up = upgrades?.[bid[j]] // Fix it raised this line's rating
      const u = Math.min(Math.max(Math.abs(f) / (up > 0 ? up : rate[j]), c.lo), c.hi)
      const [gap, speed, dash] = motion(u)
      d.dir[j] = f > 0 ? 1 : -1
      d.gap[j] = gap
      d.speed[j] = speed
      d.dash[j] = dash
      d.color[j] = c.color
      const out = dark[from[j]] === 'sub--dark' || dark[to[j]] === 'sub--dark'
      d.target[j] = out ? 0 : c.alpha * (dimmed ? DIM_HEADROOM : 1)
    }
    return d
  }, [geom, view, dimmed, upgrades])

  useEffect(() => {
    geomRef.current = geom
  }, [geom])
  useEffect(() => {
    dynRef.current = dyn
  }, [dyn])

  // ------------------------------------------------------------ the animation loop
  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!ctx) return undefined

    // the palette tokens, read at start and again on each new view (cheap; keeps up with a retheme)
    let colors = []
    const readColors = () => {
      const styles = getComputedStyle(canvas)
      colors = TOKENS.map((t) => styles.getPropertyValue(t).trim() || 'white')
    }
    readColors()

    // one growable coordinate buffer per style: color x kV class x alpha level
    const NB = 3 * 2 * (LEVELS + 1)
    const bufs = Array.from({ length: NB }, () => ({ a: new Float32Array(1024), n: 0 }))
    const push = (b, x1, y1, x2, y2) => {
      if (b.n + 4 > b.a.length) {
        const grown = new Float32Array(b.a.length * 2)
        grown.set(b.a)
        b.a = grown
      }
      const a = b.a
      a[b.n++] = x1
      a[b.n++] = y1
      a[b.n++] = x2
      a[b.n++] = y2
    }

    const P = new Float64Array(4) // clipping scratch, reused every segment
    const Q = new Float64Array(4)

    let size = null // {w, h} in CSS px from the ResizeObserver
    const ro = new ResizeObserver(([entry]) => {
      const r = entry.contentRect
      size = { w: r.width, h: r.height }
    })
    ro.observe(canvas)

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)')
    let raf = 0
    let last = 0
    let running = false
    let ema = 0
    let drawnDyn = null
    let live = null // the motion actually drawn: the view's, plus lines fading out after their flow stopped
    let tLayout = 0 // dev stats: when the CTM + rect reads finished

    function clear() {
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, canvas.width, canvas.height)
    }

    function draw(dt) {
      const g = geomRef.current
      const d = dynRef.current
      const cam = gRef?.current
      if (!g || !d || !cam || !size || size.w < 1 || size.h < 1) return
      const ctm = cam.getScreenCTM()
      if (!ctm) return
      const dpr = Math.min(window.devicePixelRatio || 1, DPR_MAX)
      const W = Math.round(size.w * dpr)
      const H = Math.round(size.h * dpr)
      if (canvas.width !== W || canvas.height !== H) {
        canvas.width = W
        canvas.height = H
      }
      clear()

      // a new view: tripped lines go dark at once (the SVG flashes them), the rest fade; a line whose
      // flow just dropped to zero (inside a region that went dark) keeps its last motion and fades out
      if (drawnDyn !== d) {
        if (!live || live.dir.length !== g.n) {
          live = {
            dir: new Int8Array(g.n),
            gap: new Float32Array(g.n),
            speed: new Float32Array(g.n),
            dash: new Float32Array(g.n),
            color: new Uint8Array(g.n),
            target: new Float32Array(g.n),
          }
        }
        for (let j = 0; j < g.n; j++) {
          live.target[j] = d.target[j]
          if (d.snap[j]) {
            g.alpha[j] = 0
            live.dir[j] = 0
          } else if (d.dir[j]) {
            live.dir[j] = d.dir[j]
            live.gap[j] = d.gap[j]
            live.speed[j] = d.speed[j]
            live.dash[j] = d.dash[j]
            live.color[j] = d.color[j]
          }
        }
        drawnDyn = d
        readColors()
      }

      const rect = canvas.getBoundingClientRect()
      if (import.meta.env.DEV) tLayout = performance.now()
      // map units -> canvas pixels
      const A = ctm.a * dpr
      const B = ctm.b * dpr
      const C = ctm.c * dpr
      const D = ctm.d * dpr
      const E = (ctm.e - rect.left) * dpr
      const F = (ctm.f - rect.top) * dpr
      const m = MARGIN * dpr
      const xmin = -m
      const ymin = -m
      const xmax = W + m
      const ymax = H + m
      const fadeStep = dt / FADE_S

      for (let k = 0; k < NB; k++) bufs[k].n = 0
      const { n, x1, y1, x2, y2, hi, off, alpha } = g
      const { dir, gap, speed, dash, color, target } = live

      for (let j = 0; j < n; j++) {
        // ease brightness toward its target (linear, FADE_S for the full range)
        let a = alpha[j]
        const t = target[j]
        if (a !== t) {
          a = a < t ? Math.min(t, a + fadeStep) : Math.max(t, a - fadeStep)
          alpha[j] = a
        }
        const s = dir[j]
        if (!s) continue
        const gp = gap[j]
        let o = off[j] + speed[j] * dt
        if (o >= gp) o %= gp
        off[j] = o
        const level = Math.round(a * LEVELS)
        if (!level) continue

        // endpoints on the canvas
        const ax = A * x1[j] + C * y1[j] + E
        const ay = B * x1[j] + D * y1[j] + F
        const bx = A * x2[j] + C * y2[j] + E
        const by = B * x2[j] + D * y2[j] + F
        // quick reject: both ends off the same side
        if ((ax < xmin && bx < xmin) || (ax > xmax && bx > xmax) || (ay < ymin && by < ymin) || (ay > ymax && by > ymax)) continue
        const dx = bx - ax
        const dy = by - ay
        const L = Math.sqrt(dx * dx + dy * dy)
        if (L < 1.5) continue

        // Liang-Barsky: the visible part [t0, t1] of the segment
        let t0 = 0
        let t1 = 1
        let ok = true
        P[0] = -dx
        P[1] = dx
        P[2] = -dy
        P[3] = dy
        Q[0] = ax - xmin
        Q[1] = xmax - ax
        Q[2] = ay - ymin
        Q[3] = ymax - ay
        for (let q = 0; q < 4 && ok; q++) {
          const p = P[q]
          if (p === 0) {
            if (Q[q] < 0) ok = false
          } else {
            const r = Q[q] / p
            if (p < 0) {
              if (r > t1) ok = false
              else if (r > t0) t0 = r
            } else if (r < t0) ok = false
            else if (r < t1) t1 = r
          }
        }
        if (!ok || t1 <= t0) continue

        // walk the dashes in the direction of travel, only over the visible stretch
        const ux = dx / L
        const uy = dy / L
        const G = gp * dpr
        const ph = o * dpr
        const dl = dash[j] * dpr
        let ox, oy, vx, vy, sv0, sv1
        if (s > 0) {
          ox = ax
          oy = ay
          vx = ux
          vy = uy
          sv0 = t0 * L
          sv1 = t1 * L
        } else {
          ox = bx
          oy = by
          vx = -ux
          vy = -uy
          sv0 = (1 - t1) * L
          sv1 = (1 - t0) * L
        }
        const buf = bufs[(color[j] * 2 + hi[j]) * (LEVELS + 1) + level]
        const kEnd = Math.floor((Math.min(sv1, L) + dl - ph) / G)
        for (let kk = Math.max(0, Math.ceil((sv0 - ph) / G)); kk <= kEnd; kk++) {
          const head = ph + kk * G
          const lo = head - dl > 0 ? head - dl : 0
          const hiEnd = head < L ? head : L
          if (hiEnd <= lo) continue
          push(buf, ox + vx * lo, oy + vy * lo, ox + vx * hiEnd, oy + vy * hiEnd)
        }
      }

      // stroke each style twice: a wide faint glow, then the bright core
      ctx.globalCompositeOperation = 'lighter'
      ctx.lineCap = 'round'
      let dashes = 0
      for (let k = 0; k < NB; k++) {
        const b = bufs[k]
        if (!b.n) continue
        dashes += b.n / 4
        const level = k % (LEVELS + 1)
        const kv = Math.floor(k / (LEVELS + 1)) % 2
        const col = Math.floor(k / ((LEVELS + 1) * 2))
        const w = CORE_W[col][kv] * dpr
        const al = level / LEVELS
        const arr = b.a
        ctx.beginPath()
        for (let i = 0; i < b.n; i += 4) {
          ctx.moveTo(arr[i], arr[i + 1])
          ctx.lineTo(arr[i + 2], arr[i + 3])
        }
        ctx.strokeStyle = colors[col]
        ctx.globalAlpha = al * GLOW[col]
        ctx.lineWidth = w * 3.4
        ctx.stroke()
        ctx.globalAlpha = al
        ctx.lineWidth = w
        ctx.stroke()
      }
      ctx.globalAlpha = 1
      ctx.globalCompositeOperation = 'source-over'
      return dashes
    }

    // Adaptive rate. Drawing is cheap (< 1 ms), but a canvas that changes every frame makes the
    // browser re-composite what sits above it; a backdrop-filter blur over the map turned that into
    // stalls on this laptop's GPU. Draw at most 60 times a second (also on 120 Hz screens); when the
    // page averages under ~45 fps over 64 frames, draw at 30, and try 60 again after 15 s.
    const intervals = new Float32Array(64)
    let filled = 0
    let lastFrame = 0
    let lastCheck = 0
    let cappedAt = 0
    let fpsCap = 60
    function adapt(now) {
      if (lastFrame) intervals[filled++ % intervals.length] = Math.min(now - lastFrame, 250)
      lastFrame = now
      if (now - lastCheck < 3000 || filled < intervals.length) return
      lastCheck = now
      let sum = 0
      for (let i = 0; i < intervals.length; i++) sum += intervals[i]
      const mean = sum / intervals.length
      if (fpsCap === 60 && mean > 22) {
        fpsCap = 30
        cappedAt = now
      } else if (fpsCap === 30 && now - cappedAt > 15000 && mean < 18) fpsCap = 60
    }

    function frame(now) {
      raf = requestAnimationFrame(frame)
      adapt(now)
      if (now - last < 1000 / fpsCap - 1.5) return // not yet time for the next drawn frame
      const dt = Math.min((now - last) / 1000, 0.1)
      last = now
      const t = performance.now()
      const dashes = draw(dt)
      if (import.meta.env.DEV) {
        const ms = performance.now() - t
        ema = ema ? ema * 0.95 + ms * 0.05 : ms
        window.__flowStats = { ms: ema, last: ms, layout: tLayout ? tLayout - t : 0, dashes, fpsCap }
      }
    }
    function start() {
      if (running || document.hidden || reduce.matches) return
      running = true
      last = performance.now()
      lastFrame = 0
      lastCheck = last
      filled = 0
      raf = requestAnimationFrame(frame)
    }
    function stop() {
      running = false
      cancelAnimationFrame(raf)
    }
    const onVisibility = () => (document.hidden ? stop() : start())
    const onReduce = () => {
      if (reduce.matches) {
        stop()
        clear()
      } else start()
    }
    document.addEventListener('visibilitychange', onVisibility)
    reduce.addEventListener('change', onReduce)
    start()
    return () => {
      stop()
      ro.disconnect()
      document.removeEventListener('visibilitychange', onVisibility)
      reduce.removeEventListener('change', onReduce)
    }
  }, [gRef])

  return (
    <>
      <canvas ref={canvasRef} className="flow-canvas" aria-hidden="true" />
      {/* the opening moment's dark sheet: above the dashes, below the map's own controls */}
      <IntroCurtain />
    </>
  )
}
