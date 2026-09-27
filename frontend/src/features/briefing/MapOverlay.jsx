import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { citiesFor, project } from '../../geo'
import { textWidth } from '../impact/towns'
import { useOverload } from '../../store'

// What the briefing draws on the live map while the stage is open, inside the map's camera group (portaled into
// `.map-cam`), so it pans and zooms with the map. Sizes are divided by the zoom (w), so a tag or a gauge stays the
// same size on screen at any zoom.
//   lines   [{id, tone: 'hl' | 'over' | 'fix' | 'cool' | 'cut', delay?}]  a branch drawn over the network (a transformer: a ring)
//   ghost   {lat, lon} | null                                          the moved campus, dashed
//   rings   [{center: [lon, lat], key}]                                 a ring on the area being named
//   layerKey  changes when a beat moves on, so what it draws animates in again
//   layer   a beat's own picture (every field optional):
//     key     restarts the layer's animations when it changes
//     lines, ghost   as above
//     spread  {origin: [lon, lat], dots: [[lon, lat, delayMs]], ms}  the blackout reaching outward from where it began
//     hulls   [{key, pts: [[lon, lat]], tone: 'lost' | 'dim' | 'ok', label?, sub?, delay?}]  an area outlined and hatched
//     flow    [{a: [lon, lat], b: [lon, lat], tone: 'base' | 'surge', delay?}]  power moving along a line, a → b
//     still   true: the finished picture (nothing loops)
//     gauge   {at: [lon, lat], from, to, max?, ms?, delay?, appear?, title, sub?, ratingLabel?}  a loading bar pinned beside one element, filling
//             from its level on today's grid past its rating (the rating marked)
//     tags    [{at: [lon, lat], text, sub?, sub2?, tone: 'fix' | 'lost' | 'info', delay?, side?: 'ne' | 'se' | 'nw' | 'sw' | 'w' | 'e'}]
//     marks   [{at: [lon, lat], tone: 'strain' | 'over' | 'fix' | 'site' | 'lost', r?}]  a ring on one place
export default function MapOverlay({ lines = [], ghost = null, rings = [], layerKey = '', layer = null }) {
  const { branchById, subPos, grid } = useOverload()
  const { cam, k } = useCamera()
  const cities = useMemo(() => (grid ? citiesFor(grid) : []), [grid])
  if (!cam) return null
  const w = 1 / Math.max(k, 0.5)
  const L = layer || {}
  const lk = L.key || layerKey
  const all = [...lines.map((l) => ({ ...l, lk: layerKey })), ...(L.lines || []).map((l) => ({ ...l, lk }))]
  const ghostAt = L.ghost || ghost
  const tagSides = layoutTags(L.tags || [], 1 / w, cities)
  return createPortal(
    <g className={`rs-map${L.still ? ' rs-map--still' : ''}`} aria-hidden="true">
      <defs>
        <pattern id="rs-hatch" patternUnits="userSpaceOnUse" width={7 * w} height={7 * w} patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2={7 * w} className="rs-map__hatch" strokeWidth={1.6 * w} />
        </pattern>
        <pattern id="rs-hatch-dim" patternUnits="userSpaceOnUse" width={9 * w} height={9 * w} patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2={9 * w} className="rs-map__hatch rs-map__hatch--dim" strokeWidth={1.1 * w} />
        </pattern>
      </defs>
      {(L.hulls || []).map((h) => (
        <Hull key={`${lk}-${h.key}`} h={h} w={w} />
      ))}
      {L.spread && <Spread key={`${lk}-spread`} s={L.spread} w={w} />}
      {all.map(({ id, tone, delay, lk: key }) => {
        const b = branchById.get(Number(id))
        const a = b && subPos(b.from_sub)
        const z = b && subPos(b.to_sub)
        if (!a || !z) return null
        const [x1, y1] = project(a[0], a[1])
        const [x2, y2] = project(z[0], z[1])
        const style = delay ? { animationDelay: `${delay}ms` } : undefined
        if (x1 === x2 && y1 === y2) {
          // a transformer: both ends at one substation
          return <circle key={`${tone}${id}-${key}`} className={`rs-map__xf rs-map__xf--${tone}`} cx={x1} cy={y1} r={9 * w} strokeWidth={2.5 * w} pathLength="1" style={style} />
        }
        return (
          <line
            key={`${tone}${id}-${key}`}
            className={`rs-map__ln rs-map__ln--${tone}`}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            strokeWidth={(tone === 'hl' || tone === 'over' ? 4 : tone === 'cool' ? 4.4 : tone === 'cut' ? 2.2 : 3.6) * w}
            strokeDasharray={tone === 'cut' ? `${5 * w} ${4 * w}` : undefined}
            pathLength={tone === 'cut' ? undefined : '1'}
            style={style}
          />
        )
      })}
      {(L.flow || []).map((f, i) => {
        const [x1, y1] = project(f.a[0], f.a[1])
        const [x2, y2] = project(f.b[0], f.b[1])
        return (
          <line
            key={`${lk}-flow-${i}`}
            className={`rs-map__flow rs-map__flow--${f.tone || 'base'}`}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            vectorEffect="non-scaling-stroke"
            style={f.delay ? { animationDelay: `0ms, ${f.delay}ms` } : undefined}
          />
        )
      })}
      {ghostAt &&
        (() => {
          const [x, y] = project(ghostAt.lon, ghostAt.lat)
          return (
            <g key={`ghost-${lk}`}>
              <circle className="rs-map__pulse" cx={x} cy={y} r={12 * w} strokeWidth={2 * w} />
              <circle className="rs-map__ghost" cx={x} cy={y} r={12 * w} strokeWidth={2 * w} strokeDasharray={`${4 * w} ${3 * w}`} />
            </g>
          )
        })()}
      {(L.marks || []).map((m, i) => {
        const [x, y] = project(m.at[0], m.at[1])
        return (
          <g key={`${lk}-mark-${i}-${m.tone}`} className={`rs-map__mark rs-map__mark--${m.tone || 'strain'}`}>
            <circle className="rs-map__mark-pulse" cx={x} cy={y} r={(m.r || 14) * w} strokeWidth={1.6 * w} />
            <circle className="rs-map__mark-ring" cx={x} cy={y} r={(m.r || 14) * w} strokeWidth={2.2 * w} />
          </g>
        )
      })}
      {rings.map((r) => {
        const [x, y] = project(r.center[0], r.center[1])
        return <circle key={r.key} className="rs-map__ring" cx={x} cy={y} r={22 * w} strokeWidth={2 * w} />
      })}
      {(L.tags || []).map((t, i) => (
        <Tag key={`${lk}-tag-${i}`} t={t} w={w} side={tagSides[i]} />
      ))}
      {L.gauge && <Gauge key={`${lk}-gauge`} g={L.gauge} w={w} />}
    </g>,
    cam,
  )
}

// ------------------------------------------------------------------ the blackout reaching outward
function Spread({ s, w }) {
  const [ox, oy] = project(s.origin[0], s.origin[1])
  const dots = s.dots.map(([lon, lat, d]) => {
    const [x, y] = project(lon, lat)
    return { x, y, d }
  })
  const maxR = Math.max(24 * w, ...dots.map((p) => Math.hypot(p.x - ox, p.y - oy)))
  return (
    <g className="rs-map__spread" style={{ '--ms': `${s.ms || 3600}ms` }}>
      <circle className="rs-map__front" cx={ox} cy={oy} r={maxR} vectorEffect="non-scaling-stroke" />
      {dots.map((p, i) => (
        <circle key={i} className="rs-map__dark" cx={p.x} cy={p.y} r={4.2 * w} strokeWidth={0.9 * w} style={{ animationDelay: `${Math.round(p.d)}ms` }} />
      ))}
      <circle className="rs-map__origin" cx={ox} cy={oy} r={7 * w} strokeWidth={2 * w} />
    </g>
  )
}

// ------------------------------------------------------------------ an area outlined and hatched
// The convex hull of the area's substations, each padded into a small octagon first so one or two stations still
// make a shape.
function hullOf(points, pad) {
  const pts = []
  for (const [x, y] of points) for (let a = 0; a < 8; a++) pts.push([x + pad * Math.cos((a * Math.PI) / 4), y + pad * Math.sin((a * Math.PI) / 4)])
  pts.sort((p, q) => p[0] - q[0] || p[1] - q[1])
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
  const lo = []
  for (const p of pts) {
    while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], p) <= 0) lo.pop()
    lo.push(p)
  }
  const up = []
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i]
    while (up.length >= 2 && cross(up[up.length - 2], up[up.length - 1], p) <= 0) up.pop()
    up.push(p)
  }
  return lo.slice(0, -1).concat(up.slice(0, -1))
}

function Hull({ h, w }) {
  const xy = (h.pts || []).map(([lon, lat]) => project(lon, lat))
  if (!xy.length) return null
  const poly = hullOf(xy, 9 * w)
  const d = `M${poly.map((p) => `${p[0].toFixed(2)},${p[1].toFixed(2)}`).join('L')}Z`
  const top = poly.reduce((m, p) => (p[1] < m[1] ? p : m), poly[0])
  const tone = h.tone || 'lost'
  return (
    <g className={`rs-map__hull rs-map__hull--${tone}`} style={h.delay ? { animationDelay: `${h.delay}ms` } : undefined}>
      <path d={d} fill={tone === 'ok' ? 'none' : `url(#${tone === 'dim' ? 'rs-hatch-dim' : 'rs-hatch'})`} className="rs-map__hull-fill" />
      <path d={d} className="rs-map__hull-edge" strokeWidth={(tone === 'dim' ? 1.1 : 1.8) * w} fill="none" />
      {h.label && (
        <g transform={`translate(${top[0]} ${top[1] - 6 * w}) scale(${w})`}>
          <text className="rs-map__label" textAnchor="middle" y={h.sub ? -11 : 0}>
            {h.label}
          </text>
          {h.sub && (
            <text className="rs-map__label-sub" textAnchor="middle" y={1}>
              {h.sub}
            </text>
          )}
        </g>
      )}
    </g>
  )
}

// ------------------------------------------------------------------ a pinned tag: what goes here and what it costs
// 'w' / 'e': out beside an area, level with its edge, on a longer leader (CLEAR AREAS: a figure that names an area sits
// outside it, never on it)
const SIDE = { ne: [16, -16, 'start'], se: [16, 20, 'start'], nw: [-16, -16, 'end'], sw: [-16, 20, 'end'], w: [-46, 4, 'end'], e: [46, 4, 'start'] }
// the sides tried, in order, when the one asked for would sit on a town's name
const SIDE_ORDER = ['ne', 'se', 'nw', 'sw', 'e', 'w']

// A tag's box in px around its point (what its text lines cover) on one side.
function tagBox(t, side) {
  const [dx, dy0, anchor] = SIDE[side]
  const dy = t.sub2 && dy0 < 0 ? dy0 - 14 : dy0
  const wMain = textWidth(String(t.text ?? ''), 12.5, 700)
  const wSub = Math.max(t.sub ? textWidth(String(t.sub), 10, 600) : 0, t.sub2 ? textWidth(String(t.sub2), 10, 600) : 0)
  const width = Math.max(wMain, wSub)
  const base = dy - (t.sub ? 3 : -3) // the main line's baseline
  const y0 = base - 10
  const y1 = t.sub2 ? dy + 25 : t.sub ? dy + 12 : base + 3
  return anchor === 'start' ? { x0: dx, x1: dx + width, y0, y1 } : { x0: dx - width, x1: dx, y0, y1 }
}

// The sides for a layer's tags: each the one asked for, unless its text would cover a town's name on the map or an
// earlier tag (the fix slide's "$6.4M-$13.2M" over "Fort Myers"); then the first side that clears them, else the
// side asked for. Boxes are in screen px around the camera group's origin.
function layoutTags(tags, k, cities) {
  const names = cities.map((c) => {
    const [cx, cy] = project(c.lon, c.lat)
    const x0 = cx * k + 6
    const yb = cy * k - 4
    return { x0, x1: x0 + textWidth(c.name, 11, 500), y0: yb - 9, y1: yb + 3 }
  })
  const hit = (a, b) => a.x0 - 2 < b.x1 && b.x0 - 2 < a.x1 && a.y0 - 2 < b.y1 && b.y0 - 2 < a.y1
  const placed = []
  return tags.map((t) => {
    const [x, y] = project(t.at[0], t.at[1])
    const want = SIDE[t.side] ? t.side : 'ne'
    const at = (side) => {
      const b = tagBox(t, side)
      return { x0: x * k + b.x0, x1: x * k + b.x1, y0: y * k + b.y0, y1: y * k + b.y1 }
    }
    const clear = (side) => {
      const b = at(side)
      return !names.some((n) => hit(b, n)) && !placed.some((p) => hit(b, p))
    }
    const side = clear(want) ? want : SIDE_ORDER.find((s) => s !== want && clear(s)) || want
    placed.push(at(side))
    return side
  })
}

function Tag({ t, w, side }) {
  const [x, y] = project(t.at[0], t.at[1])
  const [dx, dy0, anchor] = SIDE[side]
  // a third line (sub2) lifts a tag above its point (below it on a south side stays as is)
  const dy = t.sub2 && dy0 < 0 ? dy0 - 14 : dy0
  return (
    <g className={`rs-map__tag rs-map__tag--${t.tone || 'info'}`} transform={`translate(${x} ${y}) scale(${w})`} style={{ animationDelay: `${t.delay || 0}ms` }}>
      <circle className="rs-map__tag-dot" r="2.6" />
      <line className="rs-map__tag-lead" x1="0" y1="0" x2={dx * 0.8} y2={dy0 * 0.8} />
      <text className="rs-map__tag-text" x={dx} y={dy - (t.sub ? 3 : -3)} textAnchor={anchor}>
        {t.text}
      </text>
      {t.sub && (
        <text className="rs-map__tag-sub" x={dx} y={dy + 9} textAnchor={anchor}>
          {t.sub}
        </text>
      )}
      {t.sub2 && (
        <text className="rs-map__tag-sub" x={dx} y={dy + 22.5} textAnchor={anchor}>
          {t.sub2}
        </text>
      )}
    </g>
  )
}

// ------------------------------------------------------------------ a loading bar pinned beside one element
// It fills from the element's loading on today's grid (`from`) to its loading with the new load (`to`), at one steady
// pace: the part up to the rating in the strain color, past it in red. The rating is the marked line at 100 %.
const BAR = 156
function Gauge({ g, w }) {
  const [x, y] = project(g.at[0], g.at[1])
  const max = Math.max(g.max || 160, g.to + 10)
  const from = Math.max(0, Number(g.from) || 0)
  const to = Math.max(from, Number(g.to) || 0)
  const ms = g.ms || 2400
  const delay = g.delay || 0
  const X = (p) => (Math.min(p, max) / max) * BAR
  const span = Math.max(to - from, 0.001)
  // the part up to the rating: from min(from,100) to min(to,100); past it: from max(from,100) to to
  const baseEnd = Math.min(to, 100)
  const baseStart = Math.min(from, 100)
  const baseT = Math.max(0, (baseEnd - baseStart) / span)
  const overStart = Math.max(from, 100)
  const overT0 = Math.max(0, (overStart - from) / span)
  const over = to > 100
  const rating = X(100)
  return (
    <g className="rs-map__gauge" transform={`translate(${x} ${y}) scale(${w})`} style={{ animationDelay: `${g.appear || 0}ms` }}>
      <line className="rs-map__gauge-lead" x1="0" y1="0" x2="20" y2="-26" />
      <g transform="translate(20 -84)">
        <rect className="rs-map__gauge-box" x="0" y="0" width={BAR + 52} height="62" rx="5" />
        <text className="rs-map__gauge-title" x="12" y="16">
          {g.title}
        </text>
        {g.sub && (
          <text className="rs-map__gauge-sub" x="12" y="28">
            {g.sub}
          </text>
        )}
        <g transform="translate(12 36)">
          <rect className="rs-map__gauge-track" x="0" y="0" width={BAR} height="9" rx="2" />
          {baseEnd > 0 && (
            <rect
              className="rs-map__gauge-fill"
              x="0"
              y="0"
              width={X(baseEnd)}
              height="9"
              style={{ '--s0': baseEnd ? baseStart / baseEnd : 0, animationDuration: `${Math.max(1, ms * baseT)}ms`, animationDelay: `${delay}ms` }}
            />
          )}
          {over && (
            <rect
              className="rs-map__gauge-over"
              x={rating}
              y="0"
              width={X(to) - rating}
              height="9"
              style={{
                '--s0': overStart > 100 ? (overStart - 100) / (to - 100) : 0,
                animationDuration: `${Math.max(1, ms * (1 - overT0))}ms`,
                animationDelay: `${delay + ms * overT0}ms`,
              }}
            />
          )}
          <line className="rs-map__gauge-was" x1={X(from)} y1="-3" x2={X(from)} y2="12" />
          <line className="rs-map__gauge-rating" x1={rating} y1="-5" x2={rating} y2="14" />
          <text className="rs-map__gauge-tick" x={rating} y="23" textAnchor="middle">
            {g.ratingLabel || 'rating'}
          </text>
          <text className="rs-map__gauge-from" x={X(from)} y="23" textAnchor="middle" style={{ opacity: Math.abs(X(from) - rating) < 26 ? 0 : 1 }}>
            {Math.round(from)}%
          </text>
          <text
            className={`rs-map__gauge-to${over ? ' rs-map__gauge-to--over' : ''}`}
            x={BAR + 4}
            y="8"
            textAnchor="start"
            style={{ animationDelay: `${delay + ms}ms` }}
          >
            {Math.round(to)}%
          </text>
        </g>
      </g>
    </g>
  )
}

// The map's camera group and its current zoom (read off its transform), found once the grid has
// loaded and found again if the map remounts.
function useCamera() {
  const [cam, setCam] = useState(null)
  const [k, setK] = useState(1)
  useEffect(() => {
    let obs = null
    let el = null
    const check = () => {
      if (el?.isConnected) return
      obs?.disconnect()
      el = document.querySelector('.map-cam')
      setCam(el)
      if (!el) return
      const cur = el
      const read = () => setK(Number(/scale\(([\d.]+)\)/.exec(cur.style.transform || cur.getAttribute('transform') || '')?.[1]) || 1)
      read()
      obs = new MutationObserver(read)
      obs.observe(cur, { attributes: true, attributeFilter: ['style', 'transform'] })
    }
    check()
    const t = setInterval(check, 400)
    return () => {
      clearInterval(t)
      obs?.disconnect()
    }
  }, [])
  return { cam, k }
}
