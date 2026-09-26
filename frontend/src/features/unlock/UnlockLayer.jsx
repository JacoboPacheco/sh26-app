// FEATURE: Strengthen the grid, the map layer (owned by the unlock track): SVG inside the map camera.
//
// The capacity view (the page's answer: campuses connected at once) draws CapacityLayer. The site-by-site view,
// and a study still learning, draw this:
// While mode === 'unlock' and a study for this region is on screen (or learning):
//  - every simulated site as a small dot: pale where a campus of the study's size fits today, amber where
//    it overloads a line (filled: it would also set off a blackout), GREEN once the plan's upgrades on
//    screen let it connect (a thin ring spreads once when its step lands);
//  - the structural weak points: an amber halo (sized by importance) on the line or transformer, the line
//    itself traced amber; a weak point the plan has fixed turns green;
//  - the plan's upgrades up to the step on screen, GREEN (the fix): each draws itself in when it lands; during
//    the build-up the step being built carries a short caption; or one Gemini bundle's upgrades when shown;
//  - the package picked in the table outlined, dashed when the budget doesn't buy it yet.
// Sizes are map units divided by the zoom, so they stay the same on screen.
import { useMemo } from 'react'
import { useMapView } from '../../GridMap'
import { WIDTH, citiesFor, fmt } from '../../geo'
import { useOverload } from '../../store'
import CapacityLayer from './CapacityLayer'
import { pickCampus } from './capacityPick'
import './unlock.css'
import { capNow, capTargetOf, compact, drawnUpgrades, gemNow, select, useUnlock } from './unlockStore'

const HALO_MIN = 6
const HALO_MAX = 17
const LABELS = 3 // the top weak points carry a name (when it fits)
const nameOf = (p) => p.short || p.label.replace(/^the /, '')

export default function UnlockLayer() {
  const o = useOverload()
  const { mode, region, focus, grid } = o
  const { k, project } = useMapView()
  const u = useUnlock()
  const on = mode === 'unlock' && u.region === region
  const r = on && u.status === 'done' ? u.result : null
  const sites = r ? r.sites : on ? u.partial?.sites : null
  const points = r ? r.points : on ? u.partial?.points : null
  const n = r ? Math.min(u.shown, r.steps.length) : 0

  const ups = useMemo(() => drawnUpgrades(r, n, u.bundle), [r, n, u.bundle])
  const unlockedAt = useMemo(() => {
    const m = new Map()
    if (!r) return m
    if (u.bundle != null) r.ai.bundles[u.bundle]?.newly.forEach((s) => m.set(s.id, 0))
    else r.steps.slice(0, n).forEach((st) => st.newly.forEach((s) => m.set(s.id, st.n)))
    return m
  }, [r, n, u.bundle])
  const fixed = useMemo(() => new Set(ups.map((p) => p.branch_id)), [ups])
  const selStep = r && u.selected?.type === 'step' && u.bundle == null ? r.steps[u.selected.id - 1] : null
  const building = r && u.playing && n > 0 ? r.steps[n - 1] : null

  const cap = r && u.view === 'capacity' ? capNow(u) : null
  const labels = on && points && !cap ? placeLabels(points, k, project, grid, u.selected) : new Map()
  // Gemini's verified plan ("Show it" on the Strengthen page): its campuses and raises, its new sites outlined in blue
  const gem = cap ? gemNow(u) : null
  if (gem)
    return (
      <CapacityLayer
        m={gem}
        target={gem.steps.length}
        shown={gem.steps.length}
        playing={false}
        selectedN={null}
        onPick={(n) => {
          const st = gem.steps[n - 1]
          if (st) o.focus([[st.site.lon, st.site.lat]])
        }}
        grid={grid}
        gem
      />
    )
  if (cap)
    return (
      <CapacityLayer
        m={cap}
        target={capTargetOf(u)}
        shown={Math.min(u.capShown, cap.steps.length)}
        playing={u.capPlaying}
        selectedN={u.selected?.type === 'cap' ? u.selected.id : null}
        onPick={(n) => pickCampus(o, cap, n)}
        grid={grid}
      />
    )
  if (!on || (!sites && !points)) return null
  const stop = (e) => e.stopPropagation()
  const sel = u.selected
  const siteR = 2.6 / k
  const pick = (p) => (e) => {
    stop(e)
    select({ type: 'point', id: p.branch_id })
    focus(
      [
        [p.from.lon, p.from.lat],
        [p.to.lon, p.to.lat],
      ],
      [p.mid[1], p.mid[0]],
    )
  }
  const drawUp = (p, cls, key) => {
    if (p.kind === 'transformer') {
      const [x, y] = project(p.from.lon, p.from.lat)
      return <circle key={key} className={cls} cx={x} cy={y} r={5.5 / k} />
    }
    const [x1, y1] = project(p.from.lon, p.from.lat)
    const [x2, y2] = project(p.to.lon, p.to.lat)
    return <line key={key} className={cls} x1={x1} y1={y1} x2={x2} y2={y2} />
  }

  return (
    <g className={`ul-layer${u.bundle != null ? ' ul-layer--bundle' : ''}${u.playing ? ' ul-layer--playing' : ''}`}>
      {/* weak points under everything: an amber halo and the line traced (their centers are picked on top) */}
      {points?.map((p) => {
        const [x1, y1] = project(p.from.lon, p.from.lat)
        const [x2, y2] = project(p.to.lon, p.to.lat)
        const [cx, cy] = project(p.mid[1], p.mid[0])
        const rr = (HALO_MIN + (HALO_MAX - HALO_MIN) * Math.sqrt(p.importance)) / k
        const done = fixed.has(p.branch_id)
        const isOn = sel?.type === 'point' && sel.id === p.branch_id
        return (
          <g key={p.branch_id} className={`ul-wp${done ? ' ul-wp--fixed' : ''}${isOn ? ' ul-wp--on' : ''}`} onPointerDown={stop} onPointerUp={stop} onClick={pick(p)}>
            <title>{`Weak point ${p.rank}: ${nameOf(p)}. ${p.reason}`}</title>
            <circle className="ul-wp__halo" cx={cx} cy={cy} r={rr} />
            {p.kind === 'line' && <line className="ul-wp__line" x1={x1} y1={y1} x2={x2} y2={y2} />}
            {labels.has(p.branch_id) && (
              <text
                className="ul-wp__label"
                x={labels.get(p.branch_id).x}
                y={labels.get(p.branch_id).y}
                textAnchor={labels.get(p.branch_id).anchor}
                fontSize={11 / k}
                strokeWidth={3 / k}
              >
                {nameOf(p)}
              </text>
            )}
          </g>
        )
      })}
      {/* the upgrades on screen: green; each draws itself in when it mounts (the step that just landed) */}
      <g className="ul-ups" aria-hidden="true">
        {ups.map((p) => drawUp(p, `ul-up${u.bundle == null && p.stepAt === n ? ' ul-up--new' : ''}${u.bundle != null ? ' ul-up--ai' : ''}`, p.branch_id))}
      </g>
      {/* the package picked in the table: outlined; dashed while the budget doesn't buy it */}
      {selStep && (
        <g className={`ul-sel${selStep.n > n ? ' ul-sel--planned' : ''}`} aria-hidden="true">
          {selStep.projects.map((p) => drawUp(p, 'ul-sel__up', `s${p.branch_id}`))}
        </g>
      )}
      {/* every simulated site */}
      {sites?.map((s) => {
        const [x, y] = project(s.lon, s.lat)
        const atStep = unlockedAt.get(s.id)
        const state = s.ok0 ? 'ok' : atStep != null ? 'unlocked' : s.hit0 == null ? 'untested' : s.hit0 > 0 ? 'blackout' : 'blocked'
        const isOn = sel?.type === 'site' && sel.id === s.id
        return (
          <g
            key={s.id}
            className={`ul-site ul-site--${state}${isOn ? ' ul-site--on' : ''}`}
            transform={`translate(${x} ${y})`}
            onPointerDown={stop}
            onPointerUp={stop}
            onClick={(e) => {
              stop(e)
              select({ type: 'site', id: s.id })
            }}
          >
            <title>
              {s.area}
              {state === 'ok'
                ? ': takes the campus today'
                : state === 'unlocked'
                  ? ': the upgrades let it connect'
                  : state === 'blackout'
                    ? `: a campus here would hit ~${compact(s.hit0)} people (estimate)`
                    : state === 'untested'
                      ? ': being simulated'
                      : ': overloads a line'}
            </title>
            <circle className="ul-site__hit" r={5 / k} />
            {state === 'unlocked' && <circle key={`ring${atStep}`} className="ul-site__ring" r={9 / k} />}
            <circle className="ul-site__dot" r={siteR} />
            {isOn && <circle className="ul-site__sel" r={7 / k} />}
          </g>
        )
      })}
      {/* the weak points' centers on top of the sites, so a transformer's own substation still picks the weak point */}
      {points?.map((p) => {
        const [cx, cy] = project(p.mid[1], p.mid[0])
        const done = fixed.has(p.branch_id)
        return (
          <g key={p.branch_id} className={`ul-wp ul-wp--pick${done ? ' ul-wp--fixed' : ''}`} onPointerDown={stop} onPointerUp={stop} onClick={pick(p)}>
            <title>{`Weak point ${p.rank}: ${nameOf(p)}`}</title>
            <circle className="ul-site__hit" cx={cx} cy={cy} r={6 / k} />
            <circle className="ul-wp__core" cx={cx} cy={cy} r={2.2 / k} />
          </g>
        )
      })}
      {/* during the build-up: what the step being built is, beside it */}
      {building && <StepCaption key={building.n} st={building} k={k} project={project} />}
    </g>
  )
}

function StepCaption({ st, k, project }) {
  const lead = [...st.projects].sort((a, b) => (a.weak_point || 99) - (b.weak_point || 99) || b.cost.high - a.cost.high)[0]
  const [x, y] = project(lead.mid?.[1] ?? (lead.from.lon + lead.to.lon) / 2, lead.mid?.[0] ?? (lead.from.lat + lead.to.lat) / 2)
  const text = `${nameOf(lead)}${st.projects.length > 1 ? ` +${st.projects.length - 1}` : ''} · +${fmt(st.newly_count)} ${st.newly_count === 1 ? 'site' : 'sites'}`
  // east of the middle the caption reads to the left, so it never runs off the map's edge
  const left = x > WIDTH * 0.55
  const d = (left ? -1 : 1) / k
  return (
    <g className="ul-cap" aria-hidden="true" transform={`translate(${x} ${y})`}>
      <line className="ul-cap__tick" x1={0} y1={0} x2={14 * d} y2={-14 / k} />
      <text className="ul-cap__text" x={17 * d} y={-17 / k} textAnchor={left ? 'end' : 'start'} fontSize={11.5 / k} strokeWidth={3.5 / k}>
        <tspan className="ul-cap__n">Step {st.n}</tspan> {text}
      </text>
    </g>
  )
}

// Names for the top weak points (and the selected one), each tried right, left, above and below its halo in
// screen pixels until it overlaps no other name and no city label the map draws; skipped otherwise.
const CHAR_W = 6.3
function placeLabels(points, k, project, grid, selected) {
  const boxes = (grid ? citiesFor(grid) : []).map((c) => {
    const [x, y] = project(c.lon, c.lat)
    return {
      x0: x * k + 6,
      x1: x * k + 6 + c.name.length * 6.6,
      y0: y * k - 15,
      y1: y * k - 2,
    }
  })
  const hits = (b) => boxes.some((o) => b.x0 < o.x1 && o.x0 < b.x1 && b.y0 < o.y1 && o.y0 < b.y1)
  const out = new Map()
  const wanted = points.filter((p, i) => i < LABELS || (selected?.type === 'point' && selected.id === p.branch_id))
  for (const p of wanted) {
    const text = nameOf(p)
    const w = text.length * CHAR_W
    const [cx, cy] = project(p.mid[1], p.mid[0])
    const sx = cx * k
    const sy = cy * k
    const r = HALO_MIN + (HALO_MAX - HALO_MIN) * Math.sqrt(p.importance) + 4
    const tries = [
      { anchor: 'start', x: sx + r, y: sy + 4, x0: sx + r, x1: sx + r + w },
      { anchor: 'end', x: sx - r, y: sy + 4, x0: sx - r - w, x1: sx - r },
      { anchor: 'middle', x: sx, y: sy - r - 2, x0: sx - w / 2, x1: sx + w / 2 },
      { anchor: 'middle', x: sx, y: sy + r + 11, x0: sx - w / 2, x1: sx + w / 2 },
    ]
    const spot = tries.map((t) => ({ ...t, y0: t.y - 11, y1: t.y + 3 })).find((t) => !hits(t))
    if (!spot) continue
    boxes.push(spot)
    out.set(p.branch_id, { x: spot.x / k, y: spot.y / k, anchor: spot.anchor })
  }
  return out
}
