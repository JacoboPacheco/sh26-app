// The incident solution stage's map (SVG inside the map camera; UnlockLayer renders it while the stage is open).
// Quiet first, one focal thing at a time:
//   - where the incident puts people in the dark without a fix: small muted red dots on those substations
//   - the weak point: amber (the line traced, a transformer ringed), named; GREEN once the option on screen raises it
//   - the campus: a light ring at the substation it connects to, with its size under the option on screen
//   - the option on screen: every line and transformer it raises in green, drawing itself in, each with a numbered pin
//     (the same numbers as the rail's list) and its rating before -> after; another site: a green ring there, a thread
//     from here
// Names are placed in screen pixels where they cover no city label and no other name. Sizes are screen pixels divided
// by the zoom, so they stay the same on screen.
import { useMemo } from 'react'
import { useMapView } from '../../GridMap'
import { HEIGHT, WIDTH, citiesFor, fmt } from '../../geo'
import { useOverload } from '../../store'
import { useAim } from './incident'
import { campusOf, mwText, optionsOf, pickSel, titleName } from './incidentModel'

// when an operating rule steps the campus down: "at every hour", or from the first hour it must ("during a heat wave")
function ruleWhen(levels) {
  const lv = (levels || []).slice().sort((a, b) => a.level - b.level)
  const low = lv.filter((x) => !x.full)
  if (!low.length) return 'at no hour'
  if (low.length === lv.length) return 'at every hour'
  return low[0].word || `from ${low[0].name}`
}

export default function IncidentLayer({ inc }) {
  const o = useOverload()
  const { k, project } = useMapView()
  const { grid, subPos, branchById } = o
  const r = inc.report
  const aim = useAim()
  const options = useMemo(() => optionsOf(r), [r])
  const sel = pickSel(options, inc.sel)
  const selI = sel?.i ?? null
  const cities = useMemo(() => (grid ? citiesFor(grid) : []), [grid])
  const c = r.case || {}
  const { total, sites, n } = campusOf(r)

  // the substations that lose power without a fix
  const dark = useMemo(() => Object.keys(r.replay?.affected || {}).map((id) => subPos(Number(id))).filter(Boolean), [r, subPos])
  // a storm's downed lines (the case's own trips: a drawn storm or a catastrophe's corridor)
  const downed = useMemo(
    () =>
      (r.replay?.trip || r.case?.trip || [])
        .map((id) => branchById.get(Number(id)))
        .filter((b) => b && b.from_sub !== b.to_sub)
        .map((b) => [subPos(b.from_sub), subPos(b.to_sub)])
        .filter(([a, z]) => a && z),
    [r, branchById, subPos],
  )

  // the option's elements, with their ends
  const els = useMemo(() => {
    const list = sel?.fix?.detail?.list || []
    return list
      .map((x, i) => {
        const b = branchById.get(Number(x.id))
        if (!b) return null
        const a = subPos(b.from_sub)
        const z = subPos(b.to_sub)
        if (!a || !z) return null
        return { n: i + 1, id: Number(x.id), a, z, transformer: b.from_sub === b.to_sub || !!x.transformer, old: x.old_mva, now: x.new_mva }
      })
      .filter(Boolean)
  }, [sel, branchById, subPos])
  const raised = new Set(els.map((e) => e.id))

  // the weak point
  const line = r.root_cause?.line
  const wb = line ? branchById.get(Number(line.id)) : null
  const wa = wb ? subPos(wb.from_sub) : null
  const wz = wb ? subPos(wb.to_sub) : null
  const wpFixed = !!(line && raised.has(Number(line.id)))

  // obstacles for names, in screen pixels: the city labels, then each name as it is placed
  const obstacles = cities.map((ct) => {
    const [x, y] = project(ct.lon, ct.lat)
    return { x0: x * k + 4, x1: x * k + 8 + ct.name.length * 6.6, y0: y * k - 15, y1: y * k - 1, w: 3 }
  })
  const place = (px, py, text, R, prefer = ['right', 'left', 'above', 'below']) => {
    const sx = px * k
    const sy = py * k
    const w = text.length * 6.3 + 6
    const at = {
      right: { anchor: 'start', x: sx + R, y: sy + 4, box: { x0: sx + R, x1: sx + R + w, y0: sy - 8, y1: sy + 7 } },
      left: { anchor: 'end', x: sx - R, y: sy + 4, box: { x0: sx - R - w, x1: sx - R, y0: sy - 8, y1: sy + 7 } },
      above: { anchor: 'middle', x: sx, y: sy - R - 3, box: { x0: sx - w / 2, x1: sx + w / 2, y0: sy - R - 15, y1: sy - R } },
      below: { anchor: 'middle', x: sx, y: sy + R + 12, box: { x0: sx - w / 2, x1: sx + w / 2, y0: sy + R, y1: sy + R + 15 } },
    }
    let best = null
    let bestScore = Infinity
    for (const p of prefer) {
      const t = at[p]
      let s = 0
      for (const ob of obstacles) if (t.box.x0 < ob.x1 && ob.x0 < t.box.x1 && t.box.y0 < ob.y1 && ob.y0 < t.box.y1) s += ob.w
      if (t.box.x0 < 0 || t.box.x1 > WIDTH * k || t.box.y0 < 0 || t.box.y1 > HEIGHT * k) s += 2
      if (s < bestScore) {
        best = t
        bestScore = s
      }
    }
    obstacles.push({ ...best.box, w: 4 })
    return { x: best.x / k, y: best.y / k, anchor: best.anchor }
  }

  // the campus markers first (names must avoid them)
  const campXY = sites.map((s) => project(s.sub_lon ?? c.sub_lon, s.sub_lat ?? c.sub_lat))
  for (const [x, y] of campXY) obstacles.push({ x0: x * k - 12, x1: x * k + 12, y0: y * k - 12, y1: y * k + 12, w: 2 })
  const wpXY = wa ? (wb.from_sub === wb.to_sub ? project(...wa) : project((wa[0] + wz[0]) / 2, (wa[1] + wz[1]) / 2)) : null

  // The option's elements are named in a callout stack beside the cluster (the elements, the weak point and the
  // campus sit a few km apart: pins on top of them would cover each other): a numbered pin (the rail's numbers) and
  // the rating before -> after, a thin leader to the element (a transformer: its ring; a line: its middle).
  const anchorXY = els.map((e) => {
    const [x1, y1] = project(...e.a)
    const [x2, y2] = project(...e.z)
    return e.transformer ? [x1, y1] : [(x1 + x2) / 2, (y1 + y2) / 2]
  })
  const cluster = [...els.flatMap((e) => [project(...e.a), project(...e.z)]), ...campXY, ...(wpXY ? [wpXY] : [])]
  let callouts = []
  let stackRight = true
  if (els.length) {
    const xs = cluster.map((p) => p[0] * k)
    const ys = cluster.map((p) => p[1] * k)
    const bx0 = Math.min(...xs)
    const bx1 = Math.max(...xs)
    const cy = (Math.min(...ys) + Math.max(...ys)) / 2
    stackRight = bx1 + 210 < WIDTH * k || bx0 - 210 < 0
    const gap = els.length > 6 ? 20 : 26
    const sx = stackRight ? bx1 + 48 : bx0 - 48
    const y0 = cy - ((els.length - 1) * gap) / 2
    callouts = els.map((e, i) => {
      const x = sx
      const y = y0 + i * gap
      const w = `${fmt(e.old)} → ${fmt(e.now)} MVA`.length * 6.3 + 22
      obstacles.push({ x0: stackRight ? x - 10 : x - w, x1: stackRight ? x + w : x + 10, y0: y - 10, y1: y + 10, w: 4 })
      return { x: x / k, y: y / k }
    })
  }

  const wpText = line ? `Weak point${wpFixed ? ', raised' : ''}` : ''
  const wpSpot = wpXY ? place(wpXY[0], wpXY[1], wpText, 16, stackRight ? ['left', 'above', 'below', 'right'] : ['right', 'above', 'below', 'left']) : null

  // the campus's label under the option on screen. Several campuses: the first carries its own size and "1 of N"; an
  // option's change is for all of them together (the engine's figures are the case's total), so it says so
  const f = sel?.fix
  const d = f?.detail || {}
  const many = n > 1
  const own = many ? `${mwText(Number(sites[0]?.mw) || 0)} campus, 1 of ${n}` : `${mwText(total)} campus`
  const all = `${n} campuses`
  const campLabel = !sel
    ? own
    : sel.kind === 'rule'
      ? many
        ? `${all} step down ${ruleWhen(d.levels)}`
        : `${own}, steps down ${ruleWhen(d.levels)}`
      : sel.kind === 'onsite'
        ? many
          ? `${all}: ${mwText(d.net_mw || 0)} of ${mwText(total)} from the grid`
          : `${own}, ${mwText(d.net_mw || 0)} from the grid`
        : sel.kind === 'move'
          ? `${own}: not here`
          : f?.kept_mw != null && f.kept_mw < total - 0.5
            ? many
              ? `${all}: ${mwText(f.kept_mw)} of ${mwText(total)}`
              : `${mwText(f.kept_mw)} campus (of ${mwText(total)})`
            : own
  const campSpot = campXY[0] ? place(campXY[0][0], campXY[0][1], campLabel, 14, sel?.kind === 'move' ? ['right', 'above', 'left', 'below'] : ['below', 'right', 'left', 'above']) : null
  // the other campuses: their own size (up to four named; more stay rings)
  const moreSpots = many && n <= 4 ? campXY.slice(1).map(([x, y], i) => ({ ...place(x, y, mwText(Number(sites[i + 1]?.mw) || 0), 11, ['below', 'right', 'left', 'above']), text: mwText(Number(sites[i + 1]?.mw) || 0) })) : []
  const moveTo = sel?.kind === 'move' ? d.sites?.[0] : null
  const moveXY = moveTo ? project(moveTo.lon, moveTo.lat) : null
  const moveText = moveTo ? `${titleName(moveTo.name)}: all ${mwText(total)} fits` : ''
  const moveSpot = moveXY ? place(moveXY[0], moveXY[1], moveText, 14) : null

  return (
    <g className="il" aria-hidden="true">
      {/* the storm's downed lines, then who loses power without a fix (quiet, under everything) */}
      {downed.length > 0 && (
        <g className="il-downed">
          {downed.map(([a, z], i) => {
            const [x1, y1] = project(...a)
            const [x2, y2] = project(...z)
            return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth={1.6 / k} />
          })}
        </g>
      )}
      <g className="il-dark">
        {dark.map(([lon, lat], i) => {
          const [x, y] = project(lon, lat)
          return <circle key={i} cx={x} cy={y} r={2.4 / k} />
        })}
      </g>

      {/* the weak point: amber, or green once the option raises it */}
      {wa && (
        <g className={`il-wp${wpFixed ? ' is-fixed' : ''}`}>
          {wb.from_sub === wb.to_sub ? (
            <circle className="il-wp__ring" cx={wpXY[0]} cy={wpXY[1]} r={10 / k} strokeWidth={2 / k} />
          ) : (
            <line className="il-wp__line" x1={project(...wa)[0]} y1={project(...wa)[1]} x2={project(...wz)[0]} y2={project(...wz)[1]} strokeWidth={4 / k} />
          )}
          <circle className="il-wp__core" cx={wpXY[0]} cy={wpXY[1]} r={2.6 / k} />
          <text className="il-name il-wp__name" x={wpSpot.x} y={wpSpot.y} textAnchor={wpSpot.anchor} fontSize={11.5 / k} strokeWidth={3.5 / k}>
            {wpText}
          </text>
        </g>
      )}

      {/* another site: a thread from here to there */}
      {moveXY && campXY[0] && (
        <line className="il-thread" x1={campXY[0][0]} y1={campXY[0][1]} x2={moveXY[0]} y2={moveXY[1]} strokeWidth={1.4 / k} strokeDasharray={`${5 / k} ${4 / k}`} />
      )}

      {/* the option's elements, drawing in (keyed by the option, so a new pick draws again) */}
      <g key={`ups${selI}`} className="il-ups">
        {/* the element the pointer is on in the rail: a light halo under it */}
        {els
          .filter((e) => e.id === aim)
          .map((e) => {
            const [x1, y1] = project(...e.a)
            const [x2, y2] = project(...e.z)
            return e.transformer ? (
              <circle key={`aim${e.id}`} className="il-aim" cx={x1} cy={y1} r={12 / k} strokeWidth={5 / k} />
            ) : (
              <line key={`aim${e.id}`} className="il-aim" x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth={11 / k} />
            )
          })}
        {els.map((e) => {
          const [x1, y1] = project(...e.a)
          const [x2, y2] = project(...e.z)
          return e.transformer ? (
            <circle key={e.id} className="il-up" cx={x1} cy={y1} r={7 / k} strokeWidth={3 / k} pathLength={1} />
          ) : (
            <line key={e.id} className="il-up" x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth={4 / k} pathLength={1} />
          )
        })}
        {els.map((e, i) => {
          const c0 = callouts[i]
          const [ax, ay] = anchorXY[i]
          // the leader starts at the transformer ring's edge (or the line's middle) and ends at the pin's edge
          const dx = c0.x - ax
          const dy = c0.y - ay
          const len = Math.hypot(dx, dy) || 1
          const from = e.transformer ? 7 / k : 0
          const to = 8 / k
          const side = stackRight ? 1 : -1
          return (
            <g key={`pin${e.id}`} className={`il-pin${e.id === aim ? ' is-aim' : ''}`} style={{ animationDelay: `${0.35 + i * 0.12}s` }}>
              {len > (from + to) * 1.2 && (
                <line className="il-pin__lead" x1={ax + (dx / len) * from} y1={ay + (dy / len) * from} x2={c0.x - (dx / len) * to} y2={c0.y - (dy / len) * to} strokeWidth={1 / k} />
              )}
              <circle cx={c0.x} cy={c0.y} r={7.5 / k} strokeWidth={1.4 / k} />
              <text x={c0.x} y={c0.y + 0.4 / k} fontSize={9.5 / k} textAnchor="middle" dominantBaseline="middle">
                {e.n}
              </text>
              <text
                className="il-name il-pin__name"
                x={c0.x + (side * 12) / k}
                y={c0.y + 4 / k}
                textAnchor={stackRight ? 'start' : 'end'}
                fontSize={11 / k}
                strokeWidth={3.5 / k}
              >
                {`${fmt(e.old)} → ${fmt(e.now)} MVA`}
              </text>
            </g>
          )
        })}
      </g>

      {/* the campus (and any more campuses of the case) */}
      {campXY.map(([x, y], i) => (
        <g key={`camp${i}`} className={`il-camp${sel?.kind === 'move' && i === 0 ? ' is-moved' : ''}`}>
          <circle className="il-camp__ring" cx={x} cy={y} r={(i === 0 ? 9 : 6.5) / k} strokeWidth={2 / k} />
          <circle className="il-camp__core" cx={x} cy={y} r={2.6 / k} />
        </g>
      ))}
      {campSpot && (
        <text className="il-name il-camp__name" x={campSpot.x} y={campSpot.y} textAnchor={campSpot.anchor} fontSize={11.5 / k} strokeWidth={3.5 / k}>
          {campLabel}
        </text>
      )}
      {moreSpots.map((s, i) => (
        <text key={`campn${i}`} className="il-name il-camp__name" x={s.x} y={s.y} textAnchor={s.anchor} fontSize={10.5 / k} strokeWidth={3.5 / k}>
          {s.text}
        </text>
      ))}
      {moveXY && (
        <g key={`move${selI}`} className="il-move">
          <circle className="il-move__ring" cx={moveXY[0]} cy={moveXY[1]} r={9 / k} strokeWidth={2.4 / k} />
          <circle className="il-move__core" cx={moveXY[0]} cy={moveXY[1]} r={2.8 / k} />
          <text className="il-name il-move__name" x={moveSpot.x} y={moveSpot.y} textAnchor={moveSpot.anchor} fontSize={11.5 / k} strokeWidth={3.5 / k}>
            {moveText}
          </text>
        </g>
      )}
    </g>
  )
}
