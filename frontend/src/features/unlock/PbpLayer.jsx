// "Watch it get built", the map (SVG inside the map camera; UnlockLayer renders it while the play-by-play is open).
// Each beat has its own picture, one focal motion at a time:
//   INTRO ..... today's campuses in light ink; the weak points this build runs into, in AMBER (what stops the next
//               campus named, with its loading on today's grid)
//   PACKAGE ... its lines and transformers draw themselves in GREEN along the real lines, one after another; a weak
//               point it fixes turns from amber to green as its upgrade lands; its campuses drop in, numbered, as the
//               voice reaches them; ONE cost tag pinned at the package ("$18M · 5 upgrades"); earlier packages stay
//               green, quieter
//   CLOSING / FINAL ... the whole plan green, every package's tag, the campuses the budget connects
// Red appears nowhere: nothing here is lost. Sizes are screen pixels divided by the zoom. Paused: every animation
// holds where it is. Reduced motion: each beat shows its finished frame.
import { useMemo } from 'react'
import { useMapView } from '../../GridMap'
import { HEIGHT, WIDTH, citiesFor } from '../../geo'
import { shortMoney } from './budget'
import { anchorOf, beatPackage, midOf, packagesOf, problemsOf } from './pbp'

const LEAD_S = 0.95 // the first upgrade starts drawing once the camera has settled (GridMap EASE_MS 900)
const DRAW_WINDOW_MAX_S = 7

export default function PbpLayer({ m, u, grid, reduced = false }) {
  const { k, project } = useMapView()
  const script = u.pbpScript
  const slides = script?.slides || []
  const slide = slides[u.pbpBeat] || null
  const pkgs = useMemo(() => packagesOf(m, script), [m, script])
  const cities = useMemo(() => (grid ? citiesFor(grid) : []), [grid])
  const K = pkgs.length
  const final = !!u.pbpEnd
  const cur = beatPackage(slide, K, final)
  const target = script?.bought?.n ?? m.today
  const problems = useMemo(() => problemsOf(m, script, target), [m, script, target])
  const today = m.today
  const shown = Math.min(u.capShown, m.steps.length)
  const intro = !final && (!slide || slide.kind === 'intro')
  const now = cur >= 1 && cur <= K ? pkgs[cur - 1] : null
  const whole = cur > K // the closing and the final frame: the whole plan

  // the package drawing now: its upgrades one after another over the first half of its beat
  const est = slide?.est_s?.en || 8
  const gap = now ? Math.min(0.7, Math.max(0.1, Math.min(DRAW_WINDOW_MAX_S, est * 0.55) / Math.max(1, now.projects.length))) : 0
  const delayOf = new Map(now ? now.projects.map((p, i) => [p.branch_id, LEAD_S + i * gap]) : [])

  // every upgrade already in (earlier packages; all of them in the closing), each line once at its latest rating
  const done = new Map()
  for (const pk of pkgs) {
    if (pk.i >= cur && !whole) break
    for (const p of pk.projects) done.set(p.branch_id, { p, i: pk.i })
  }
  if (now) for (const p of now.projects) done.delete(p.branch_id)

  const line = (p, cls, key, style, w = 3) => {
    if (p.kind === 'transformer' || (p.from.lat === p.to.lat && p.from.lon === p.to.lon)) {
      const [x, y] = project(p.from.lon, p.from.lat)
      return <circle key={key} className={`${cls} pbp-up--ring`} cx={x} cy={y} r={6.5 / k} strokeWidth={(w * 0.8) / k} pathLength={1} style={style} />
    }
    const [x1, y1] = project(p.from.lon, p.from.lat)
    const [x2, y2] = project(p.to.lon, p.to.lat)
    // a short line (inside one town) is a few pixels at a state-wide zoom: a small mark at its middle keeps every
    // upgrade visible; it lands with the line
    const [mx, my] = project(...midOf(p))
    return (
      <g key={key}>
        <line className={cls} x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth={w / k} pathLength={1} style={style} />
        <circle className={`pbp-up__mark${cls.includes('--past') ? ' is-past' : ''}`} cx={mx} cy={my} r={3.2 / k} style={style} />
      </g>
    )
  }

  // the cost tags: one per package played so far, pinned at its costliest upgrade (a short leader to it), on the side
  // that covers the fewest city names, campuses and other tags (screen pixels)
  const boxes = []
  for (const c of cities) {
    const [x, y] = project(c.lon, c.lat)
    boxes.push({ x0: x * k + 4, x1: x * k + 8 + c.name.length * 6.6, y0: y * k - 15, y1: y * k - 1, w: 2 })
  }
  for (const st of m.steps.slice(0, shown)) {
    const [x, y] = project(st.site.lon, st.site.lat)
    boxes.push({ x0: x * k - 11, x1: x * k + 11, y0: y * k - 11, y1: y * k + 11, w: 2 })
  }
  const tags = []
  for (const pk of pkgs) {
    const at = anchorOf(pk)
    if ((!whole && pk.i > cur) || !at) continue
    const [ax, ay] = project(at[0], at[1])
    const text = `${shortMoney(pk.cost_high)} · ${pk.upgrades} ${pk.upgrades === 1 ? 'upgrade' : 'upgrades'}`
    const w = text.length * 6.7 + 18
    const sx0 = ax * k
    const sy0 = ay * k
    const tries = [
      [0, -34],
      [w / 2 + 26, -14],
      [-(w / 2 + 26), -14],
      [0, 36],
      [w / 2 + 26, 18],
      [-(w / 2 + 26), 18],
      [0, -62],
      [0, 64],
    ].map(([dx, dy]) => ({ sx: sx0 + dx, sy: sy0 + dy, box: { x0: sx0 + dx - w / 2, x1: sx0 + dx + w / 2, y0: sy0 + dy - 12, y1: sy0 + dy + 10 } }))
    let best = tries[0]
    let bestScore = Infinity
    for (const t of tries) {
      let sc = 0
      for (const o of boxes) if (t.box.x0 < o.x1 && o.x0 < t.box.x1 && t.box.y0 < o.y1 && o.y0 < t.box.y1) sc += o.w
      if (t.box.x0 < 0 || t.box.x1 > WIDTH * k || t.box.y0 < 0 || t.box.y1 > HEIGHT * k) sc += 1
      // the whole state (the closing, the final frame: the camera at rest) has the beats strip over its top
      if (whole && t.box.y0 < HEIGHT * 0.16) sc += 6
      if (sc < bestScore) {
        best = t
        bestScore = sc
      }
    }
    boxes.push({ ...best.box, w: 5 })
    tags.push({ pk, text, w, sx: best.sx, sy: best.sy, ax: sx0, ay: sy0 })
  }

  const latest = !reduced && now && shown >= now.first ? m.steps[shown - 1] : null

  return (
    <g className={`pbp-layer${u.capTalk ? '' : ' is-paused'}${reduced ? ' is-still' : ''}`} aria-hidden="true">
      {/* the upgrades already in: green, quieter while a later package draws */}
      <g>{[...done.values()].map(({ p }) => line(p, `pbp-up${whole ? '' : ' pbp-up--past'}`, `d${p.branch_id}`, undefined, whole ? 3 : 2.4))}</g>

      {/* the weak points: amber until the package that raises one lands (then green) */}
      {problems.map((pb, j) => {
        const fixed = pb.fixed_in != null && (pb.fixed_in < cur || whole)
        const fixing = !fixed && pb.fixed_in != null && pb.fixed_in === cur
        const [mx, my] = project(...midOf(pb))
        const isLine = pb.kind !== 'transformer' && !(pb.from.lat === pb.to.lat && pb.from.lon === pb.to.lon)
        const [x1, y1] = project(pb.from.lon, pb.from.lat)
        const [x2, y2] = project(pb.to.lon, pb.to.lat)
        const style = fixing ? { animationDelay: `${(delayOf.get(pb.branch_id) ?? LEAD_S) + 0.5}s` } : undefined
        const labelled = intro && j < 3
        const left = mx > WIDTH * 0.6
        return (
          <g key={`wp${pb.branch_id}`} className={`pbp-wp${fixed ? ' is-fixed' : ''}${fixing ? ' is-fixing' : ''}${j === 0 ? ' is-first' : ''}`}>
            {isLine && <line className="pbp-wp__line" x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth={3.4 / k} style={style} />}
            <circle className="pbp-wp__ring" cx={mx} cy={my} r={(j === 0 ? 12 : 9) / k} strokeWidth={1.6 / k} style={style} />
            <circle className="pbp-wp__core" cx={mx} cy={my} r={2.6 / k} style={style} />
            {labelled && (
              <text
                className="pbp-wp__label"
                x={mx + ((left ? -1 : 1) * (j === 0 ? 17 : 14)) / k}
                y={my + 4 / k}
                fontSize={(j === 0 ? 12.5 : 11) / k}
                strokeWidth={3.5 / k}
                textAnchor={left ? 'end' : 'start'}
              >
                {pb.short}
                {pb.base_pct != null && (
                  <tspan className="pbp-wp__pct" x={mx + ((left ? -1 : 1) * (j === 0 ? 17 : 14)) / k} dy={14 / k} fontSize={10.5 / k}>
                    {`${pb.base_pct} % of its rating today${j === 0 ? `, stops campus ${pb.stops}` : ''}`}
                  </tspan>
                )}
              </text>
            )}
          </g>
        )
      })}

      {/* the package drawing now: one line after another, along the real lines */}
      {now && (
        <g key={`now${cur}`}>
          {now.projects.map((p) => line(p, 'pbp-up pbp-up--draw', `n${p.branch_id}`, reduced ? undefined : { animationDelay: `${delayOf.get(p.branch_id)}s` }, 3.4))}
        </g>
      )}

      {/* the campuses connected so far: today's in light ink, the upgrades' in green; each drops in once */}
      {m.steps.slice(0, shown).map((st) => {
        const n = st.n
        const [x, y] = project(st.site.lon, st.site.lat)
        const r = 8.5 / k
        return (
          <g key={`c${n}`} className={`pbp-camp pbp-camp--${n <= today ? 'today' : 'built'}`} transform={`translate(${x} ${y})`}>
            {n > today && !reduced && <circle className="pbp-camp__drop" r={r} strokeWidth={1.6 / k} />}
            <circle className="pbp-camp__dot" r={r} strokeWidth={1.5 / k} />
            <text className="pbp-camp__n" y={0.5 / k} fontSize={9.5 / k} dominantBaseline="middle" textAnchor="middle">
              {n}
            </text>
          </g>
        )
      })}

      {/* the campus that just went in, named beside it */}
      {latest && latest.n > today && <CampusName key={`name${latest.n}`} st={latest} k={k} project={project} />}

      {/* one cost tag per package */}
      {tags.map(({ pk, text, w, sx, sy, ax, ay }) => (
        <g
          key={`t${pk.i}`}
          className={`pbp-tag${pk.i === cur && !whole ? ' is-now' : whole ? '' : ' is-past'}`}
          transform={`translate(${sx / k} ${sy / k}) scale(${1 / k})`}
          style={pk.i === cur && !whole && !reduced ? { animationDelay: `${LEAD_S + 0.5}s` } : undefined}
        >
          <line className="pbp-tag__lead" x1={0} y1={0} x2={ax - sx} y2={ay - sy} />
          <rect className="pbp-tag__box" x={-w / 2} y={-12} width={w} height={22} rx={4} />
          <text className="pbp-tag__text" x={0} y={3.5} fontSize={12} textAnchor="middle">
            {text}
          </text>
        </g>
      ))}
    </g>
  )
}

function CampusName({ st, k, project }) {
  const [x, y] = project(st.site.lon, st.site.lat)
  const left = x > WIDTH * 0.6
  return (
    <text className="pbp-name" x={x + ((left ? -1 : 1) * 14) / k} y={y - 12 / k} fontSize={11.5 / k} strokeWidth={3.5 / k} textAnchor={left ? 'end' : 'start'}>
      {st.site.area}
    </text>
  )
}
