// The Strengthen map in the capacity view (SVG inside the map camera; UnlockLayer renders it while the view is
// 'capacity'): the campuses numbered in the order the engine connects them (the numbers are a sequence: the same
// campus as the meter's cell and the plan's row), today's in light ink, the ones the budget buys in green, the
// rest faint; the upgrades the budget buys drawn in green along their lines (a transformer as a green ring at its
// substation), the rest faint; and, beyond the budget, the line or transformer that stops the NEXT campus ringed
// in amber with its name. During the build-up each campus drops at its site as its upgrades draw in, a faint
// green thread runs from the campus to each upgrade it paid for (they can sit 150 km apart) and each new upgrade
// pulses once; the picked campus gets the same threads in the interface blue.
// Names (the chokepoint's, the build-up caption) are placed where they cover no city label the map draws and no
// campus marker: tried left, right, above and below in screen pixels, the least crowded spot wins.
// Sizes are screen pixels divided by the zoom, so they stay the same on screen.
import { useMemo } from 'react'
import { useMapView } from '../../GridMap'
import { HEIGHT, WIDTH, citiesFor, fmt } from '../../geo'
import { shortMoney } from './budget'
import { blockOf, ordinal, shortName } from './capacity'

const midOf = (p) => (p.mid ? [p.mid[1], p.mid[0]] : [(p.from.lon + p.to.lon) / 2, (p.from.lat + p.to.lat) / 2])

// every upgrade once, at its last step (a line raised twice keeps its latest rating); `upTo` steps count as built
function upgradesOf(m) {
  const byId = new Map()
  for (const st of m.steps) for (const p of st.projects) byId.set(p.branch_id, { ...p, stepAt: st.n })
  const first = new Map()
  for (const st of m.steps) for (const p of st.projects) if (!first.has(p.branch_id)) first.set(p.branch_id, st.n)
  return [...byId.values()].map((p) => ({ ...p, firstAt: first.get(p.branch_id) }))
}

export default function CapacityLayer({ m, target, shown, playing, selectedN, onPick, grid, gem = false }) {
  const { k, project } = useMapView()
  const ups = useMemo(() => upgradesOf(m), [m])
  const cities = useMemo(() => (grid ? citiesFor(grid) : []), [grid])
  const stop = (e) => e.stopPropagation()
  const today = m.today
  const next = !playing && target < m.steps.length ? m.steps[target] : null
  // what stops the next campus: the limit most of its tried sites hit first, else the upgrade it needs most
  const nextBlock = next ? blockOf(next) : null
  const block = nextBlock || (next ? next.projects[0] || null : null)
  const sel = selectedN ? m.steps[selectedN - 1] : null
  const building = playing && shown > today ? m.steps[shown - 1] : null

  // what a name must not cover, in screen pixels: the map's city labels and the campus markers
  const obstacles = []
  for (const c of cities) {
    const [x, y] = project(c.lon, c.lat)
    obstacles.push({ x0: x * k + 4, x1: x * k + 8 + c.name.length * 6.6, y0: y * k - 15, y1: y * k - 1, w: 3 })
  }
  for (const st of m.steps) {
    const [x, y] = project(st.site.lon, st.site.lat)
    obstacles.push({ x0: x * k - 10, x1: x * k + 10, y0: y * k - 10, y1: y * k + 10, w: 1, n: st.n })
  }
  const blockSpot = block ? placeBlock(block, next.n, !!nextBlock, k, project, obstacles) : null

  const drawUp = (p, cls, key, extra = {}) => {
    if (p.kind === 'transformer' || (p.from.lat === p.to.lat && p.from.lon === p.to.lon)) {
      const [x, y] = project(p.from.lon, p.from.lat)
      return <circle key={key} className={cls} cx={x} cy={y} r={(extra.r || 6) / k} strokeWidth={(extra.w || 2.2) / k} pathLength={1} />
    }
    const [x1, y1] = project(p.from.lon, p.from.lat)
    const [x2, y2] = project(p.to.lon, p.to.lat)
    return <line key={key} className={cls} x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth={(extra.w || 3) / k} pathLength={1} />
  }

  const built = ups.filter((p) => p.firstAt <= shown)
  const later = ups.filter((p) => p.firstAt > shown)

  return (
    <g className={`cl${playing ? ' cl--playing' : ''}`}>
      {/* the upgrades not bought yet: faint, dashed */}
      <g aria-hidden="true">{later.map((p) => drawUp(p, 'cl-up cl-up--later', `l${p.branch_id}`, { w: 1.4 }))}</g>
      {/* the upgrades the budget buys (up to the campus on screen): green, each draws itself in when it lands */}
      <g aria-hidden="true">{built.map((p) => drawUp(p, `cl-up${playing && p.firstAt === shown ? ' cl-up--new' : ''}`, `b${p.branch_id}`))}</g>

      {/* the campus being built: a thread to each upgrade it paid for, and each new upgrade pulses once */}
      {building && building.projects.length > 0 && (
        <g key={`link${building.n}`} aria-hidden="true">
          {building.projects.map((p) => (
            <Thread key={p.branch_id} st={building} p={p} k={k} project={project} cls="cl-link" />
          ))}
          {building.projects.map((p) => {
            const [x, y] = project(...midOf(p))
            return <circle key={`pulse${p.branch_id}`} className="cl-pulse" cx={x} cy={y} r={8 / k} strokeWidth={2 / k} />
          })}
        </g>
      )}

      {/* what stops the next campus beyond the budget: amber, named */}
      {block && <Chokepoint b={block} spot={blockSpot} k={k} project={project} />}

      {/* the campus picked: its own upgrades outlined in the interface blue, threaded to it */}
      {sel && sel.projects.length > 0 && (
        <g className={`cl-sel${sel.n > shown ? ' cl-sel--planned' : ''}`} aria-hidden="true">
          {sel.projects.map((p) => (
            <Thread key={`t${p.branch_id}`} st={sel} p={p} k={k} project={project} cls="cl-sel__link" />
          ))}
          {sel.projects.map((p) => drawUp(p, 'cl-sel__up', `s${p.branch_id}`, { r: 9, w: 6 }))}
        </g>
      )}

      {/* the campuses: the ones not bought first (faint), then the built ones on top */}
      {[...m.steps]
        .sort((a, b) => (a.n <= shown) - (b.n <= shown) || b.n - a.n)
        .map((st) => {
          const n = st.n
          const s = n <= today ? 'today' : n <= shown ? 'bought' : 'later'
          const [x, y] = project(st.site.lon, st.site.lat)
          const r = (s === 'later' ? 6.5 : 8.5) / k
          const isSel = selectedN === n
          const landing = playing && n === shown && n > today
          return (
            <g
              key={n}
              className={`cl-camp cl-camp--${s}${n > target ? ' cl-camp--past' : ''}${isSel ? ' is-sel' : ''}${gem && st.gem ? ' cl-camp--gem' : ''}`}
              transform={`translate(${x} ${y})`}
              onPointerDown={stop}
              onPointerUp={stop}
              onClick={(e) => {
                stop(e)
                onPick(n)
              }}
            >
              <title>
                {gem
                  ? `Gemini’s campus ${n}: ${st.site.area}${st.gem ? ' (not in the engine’s plan)' : st.engineN ? ` (the engine’s ${ordinal(st.engineN)})` : ''}`
                  : `Campus ${n}: ${st.site.area}${n <= today ? ', fits today' : st.free ? ', fits with the upgrades before it' : `, +${shortMoney(st.cost.high)} of upgrades`}`}
              </title>
              <circle className="cl-camp__hit" r={12 / k} />
              {landing && <circle key={`drop${n}`} className="cl-camp__drop" r={r} strokeWidth={1.5 / k} />}
              <circle className="cl-camp__dot" r={r} strokeWidth={(s === 'later' ? 1.2 : 1.5) / k} />
              {gem && st.gem && <circle className="cl-camp__gem" r={r + 3 / k} strokeWidth={2 / k} />}
              {isSel && <circle className="cl-camp__sel" r={r + 3.5 / k} strokeWidth={1.6 / k} />}
              <text className="cl-camp__n" y={0.5 / k} fontSize={(s === 'later' ? 8 : 9.5) / k} dominantBaseline="middle" textAnchor="middle">
                {n}
              </text>
              {isSel && (
                <text className="cl-camp__name" x={(x > WIDTH * 0.62 ? -13 : 13) / k} y={0.5 / k} fontSize={11.5 / k} strokeWidth={3.5 / k} dominantBaseline="middle" textAnchor={x > WIDTH * 0.62 ? 'end' : 'start'}>
                  {st.site.area}
                </text>
              )}
            </g>
          )
        })}

      {/* during the build-up: the campus that just landed, named beside it */}
      {building && <Caption key={building.n} st={building} k={k} project={project} obstacles={obstacles} />}
    </g>
  )
}

// a faint thread from a campus to one upgrade it paid for (a transformer: its substation; a line: its middle)
function Thread({ st, p, k, project, cls }) {
  const [x1, y1] = project(st.site.lon, st.site.lat)
  const [x2, y2] = project(...midOf(p))
  if (Math.hypot(x2 - x1, y2 - y1) * k < 14) return null // on top of the campus: nothing to link
  return <line className={cls} x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth={1.4 / k} strokeDasharray={`${4 / k} ${3 / k}`} />
}

// How crowded a candidate box is (screen pixels): the obstacles it overlaps, weighted (a city label counts more
// than a campus marker), plus a penalty for running off the state's own extent.
function crowd(b, obstacles, k, skipN) {
  let score = 0
  for (const o of obstacles) if ((o.n == null || o.n !== skipN) && b.x0 < o.x1 && o.x0 < b.x1 && b.y0 < o.y1 && o.y0 < b.y1) score += o.w
  if (b.x0 < 0 || b.x1 > WIDTH * k || b.y0 < 0 || b.y1 > HEIGHT * k) score += 2
  return score
}

const leastCrowded = (tries, obstacles, k, skipN) => {
  let best = tries[0]
  let bestScore = Infinity
  for (const t of tries) {
    const sc = crowd(t.box, obstacles, k, skipN)
    if (sc < bestScore) {
      best = t
      bestScore = sc
    }
  }
  return best
}

// Where the chokepoint's two-line name goes: left, right, above or below its ring, the least crowded spot (ties
// keep this order: the map names cities to the right of their light, so left first).
function placeBlock(b, n, most, k, project, obstacles) {
  const [cx, cy] = project(...midOf(b))
  const sx = cx * k
  const sy = cy * k
  const name = shortName(b)
  const sub = most ? `stops the ${ordinal(n)} campus` : `needed for the ${ordinal(n)} campus`
  const w = Math.max(name.length * 6.6, sub.length * 6)
  const R = 16
  const tries = [
    { anchor: 'end', x: sx - R, y: sy - 2, box: { x0: sx - R - w, x1: sx - R, y0: sy - 14, y1: sy + 15 } },
    { anchor: 'start', x: sx + R, y: sy - 2, box: { x0: sx + R, x1: sx + R + w, y0: sy - 14, y1: sy + 15 } },
    { anchor: 'middle', x: sx, y: sy - R - 16, box: { x0: sx - w / 2, x1: sx + w / 2, y0: sy - R - 28, y1: sy - R + 1 } },
    { anchor: 'middle', x: sx, y: sy + R + 11, box: { x0: sx - w / 2, x1: sx + w / 2, y0: sy + R, y1: sy + R + 29 } },
    { anchor: 'end', x: sx - 8, y: sy + R + 11, box: { x0: sx - 8 - w, x1: sx - 8, y0: sy + R, y1: sy + R + 29 } },
    { anchor: 'start', x: sx + 8, y: sy + R + 11, box: { x0: sx + 8, x1: sx + 8 + w, y0: sy + R, y1: sy + R + 29 } },
  ]
  return { ...leastCrowded(tries, obstacles, k), name, sub }
}

function Chokepoint({ b, spot, k, project }) {
  const [cx, cy] = project(...midOf(b))
  const line = b.kind === 'line' && !(b.from.lat === b.to.lat && b.from.lon === b.to.lon)
  const [x1, y1] = project(b.from.lon, b.from.lat)
  const [x2, y2] = project(b.to.lon, b.to.lat)
  return (
    <g className="cl-block" aria-hidden="true">
      {line && <line className="cl-block__line" x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth={2.6 / k} />}
      <circle className="cl-block__ring" cx={cx} cy={cy} r={11 / k} strokeWidth={1.6 / k} />
      <circle className="cl-block__core" cx={cx} cy={cy} r={2.6 / k} />
      <text className="cl-block__label" x={spot.x / k} y={spot.y / k} fontSize={11.5 / k} strokeWidth={3.5 / k} textAnchor={spot.anchor}>
        {spot.name}
        <tspan className="cl-block__sub" x={spot.x / k} dy={13 / k} fontSize={10.5 / k}>
          {spot.sub}
        </tspan>
      </text>
    </g>
  )
}

// The campus that just landed, named beside it: above or below its marker, to the side with more room first,
// whichever covers the fewest city labels and other markers.
function Caption({ st, k, project, obstacles }) {
  const [x, y] = project(st.site.lon, st.site.lat)
  const lead = st.projects[0]
  const text = st.free ? 'fits with the upgrades so far' : `+${shortMoney(st.cost.high)}, ${shortName(lead)}${st.projects.length > 1 ? ` +${fmt(st.projects.length - 1)}` : ''}`
  const w = (st.site.area.length + 1 + text.length) * 6.4
  const sx = x * k
  const sy = y * k
  const side = x < WIDTH * 0.55 ? 1 : -1
  const tries = [
    [side, -1],
    [-side, -1],
    [side, 1],
    [-side, 1],
  ].map(([dx, dy]) => {
    const tx = sx + 21 * dx
    const ty = sy + (dy < 0 ? -21 : 29)
    return { dx, dy, box: { x0: dx > 0 ? tx : tx - w, x1: dx > 0 ? tx + w : tx, y0: ty - 11, y1: ty + 3 } }
  })
  const best = leastCrowded(tries, obstacles, k, st.n)
  const dx = best.dx / k
  const up = best.dy < 0
  return (
    <g className="cl-cap" aria-hidden="true" transform={`translate(${x} ${y})`}>
      <line className="cl-cap__tick" x1={10 * dx} y1={(up ? -10 : 10) / k} x2={18 * dx} y2={(up ? -18 : 18) / k} strokeWidth={1 / k} />
      <text className="cl-cap__text" x={21 * dx} y={(up ? -21 : 29) / k} textAnchor={best.dx < 0 ? 'end' : 'start'} fontSize={11.5 / k} strokeWidth={3.5 / k}>
        <tspan className="cl-cap__n">{st.site.area}</tspan> {text}
      </text>
    </g>
  )
}
