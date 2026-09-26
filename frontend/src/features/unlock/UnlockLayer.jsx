// FEATURE: Strengthen the grid, the map layer (owned by the unlock track): SVG inside the map camera.
//
// While mode === 'unlock' and a study for this region is on screen (or learning):
//  - every simulated site as a small dot: pale where a campus of the study's size fits today, amber where
//    it overloads a line (a ring: it would also set off a blackout), GREEN once the plan's upgrades on
//    screen let it connect (it lights up when its step lands);
//  - the structural weak points: an amber halo (sized by importance) on the line or transformer, the line
//    itself traced amber; a weak point the plan has fixed turns green;
//  - the plan's upgrades up to the step on screen, GREEN (the fix), the newest step drawn in; or one
//    Gemini bundle's upgrades when the panel shows it.
// Sizes are map units divided by the zoom, so they stay the same on screen.
import { useMemo } from 'react'
import { useMapView } from '../../GridMap'
import { citiesFor } from '../../geo'
import { useOverload } from '../../store'
import './unlock.css'
import { compact, drawnUpgrades, select, useUnlock } from './unlockStore'

// scroll the panel to what was picked on the map (after the panel re-renders)
const reveal = (id) =>
  requestAnimationFrame(() =>
    document.getElementById(id)?.scrollIntoView({
      block: 'nearest',
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    }),
  )

const HALO_MIN = 6
const HALO_MAX = 17
const LABELS = 4 // the top weak points carry a name (when it fits)

export default function UnlockLayer() {
  const { mode, region, focus, grid } = useOverload()
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

  const labels = on && points ? placeLabels(points, k, project, grid, u.selected) : new Map()
  if (!on || (!sites && !points)) return null
  const stop = (e) => e.stopPropagation()
  const sel = u.selected
  const siteR = 2.6 / k
  const pick = (p) => (e) => {
    stop(e)
    select({ type: 'point', id: p.branch_id })
    reveal(`ul-pt-${p.branch_id}`)
    focus(
      [
        [p.from.lon, p.from.lat],
        [p.to.lon, p.to.lat],
      ],
      [p.mid[1], p.mid[0]],
    )
  }

  return (
    <g className={`ul-layer${u.bundle != null ? ' ul-layer--bundle' : ''}`}>
      {/* weak points under everything: an amber halo and the line traced (their centers are picked on top) */}
      {points?.map((p) => {
        const [x1, y1] = project(p.from.lon, p.from.lat)
        const [x2, y2] = project(p.to.lon, p.to.lat)
        const [cx, cy] = project(p.mid[1], p.mid[0])
        const rr = (HALO_MIN + (HALO_MAX - HALO_MIN) * Math.sqrt(p.importance)) / k
        const done = fixed.has(p.branch_id)
        const on = sel?.type === 'point' && sel.id === p.branch_id
        return (
          <g
            key={p.branch_id}
            className={`ul-wp${done ? ' ul-wp--fixed' : ''}${on ? ' ul-wp--on' : ''}`}
            onPointerDown={stop}
            onPointerUp={stop}
            onClick={pick(p)}
          >
            <title>{`Weak point ${p.rank}: ${p.label.replace(/^the /, '')}. ${p.reason}`}</title>
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
                {p.label.replace(/^the /, '')}
              </text>
            )}
          </g>
        )
      })}
      {/* the upgrades on screen: green; the newest step draws itself in */}
      <g className="ul-ups" aria-hidden="true">
        {ups.map((p) => {
          const newest = u.bundle == null && p.stepAt === n
          const cls = `ul-up${newest ? ' ul-up--new' : ''}${u.bundle != null ? ' ul-up--ai' : ''}`
          if (p.kind === 'transformer') {
            const [x, y] = project(p.from.lon, p.from.lat)
            return <circle key={p.branch_id} className={cls} cx={x} cy={y} r={5.5 / k} />
          }
          const [x1, y1] = project(p.from.lon, p.from.lat)
          const [x2, y2] = project(p.to.lon, p.to.lat)
          return <line key={p.branch_id} className={cls} x1={x1} y1={y1} x2={x2} y2={y2} />
        })}
      </g>
      {/* every simulated site */}
      {sites?.map((s) => {
        const [x, y] = project(s.lon, s.lat)
        const at = unlockedAt.get(s.id)
        const state = s.ok0 ? 'ok' : at != null ? 'unlocked' : s.hit0 == null ? 'untested' : s.hit0 > 0 ? 'blackout' : 'blocked'
        const on = sel?.type === 'site' && sel.id === s.id
        return (
          <g
            key={s.id}
            className={`ul-site ul-site--${state}${at != null && at === n && u.bundle == null ? ' ul-site--new' : ''}${on ? ' ul-site--on' : ''}`}
            transform={`translate(${x} ${y})`}
            onPointerDown={stop}
            onPointerUp={stop}
            onClick={(e) => {
              stop(e)
              select({ type: 'site', id: s.id })
              reveal('ul-sel-card')
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
            <circle className="ul-site__dot" r={siteR} />
            {on && <circle className="ul-site__sel" r={7 / k} />}
          </g>
        )
      })}
      {/* the weak points' centers on top of the sites, so a transformer's own substation still picks the weak point */}
      {points?.map((p) => {
        const [cx, cy] = project(p.mid[1], p.mid[0])
        const done = fixed.has(p.branch_id)
        return (
          <g key={p.branch_id} className={`ul-wp ul-wp--pick${done ? ' ul-wp--fixed' : ''}`} onPointerDown={stop} onPointerUp={stop} onClick={pick(p)}>
            <title>{`Weak point ${p.rank}: ${p.label.replace(/^the /, '')}`}</title>
            <circle className="ul-site__hit" cx={cx} cy={cy} r={6 / k} />
            <circle className="ul-wp__core" cx={cx} cy={cy} r={2.2 / k} />
          </g>
        )
      })}
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
    const text = p.label.replace(/^the /, '')
    const w = text.length * CHAR_W
    const [cx, cy] = project(p.mid[1], p.mid[0])
    const sx = cx * k
    const sy = cy * k
    const r = HALO_MIN + (HALO_MAX - HALO_MIN) * Math.sqrt(p.importance) + 4
    const tries = [
      { anchor: 'start', x: sx + r, y: sy + 4, x0: sx + r, x1: sx + r + w },
      { anchor: 'end', x: sx - r, y: sy + 4, x0: sx - r - w, x1: sx - r },
      {
        anchor: 'middle',
        x: sx,
        y: sy - r - 2,
        x0: sx - w / 2,
        x1: sx + w / 2,
      },
      {
        anchor: 'middle',
        x: sx,
        y: sy + r + 11,
        x0: sx - w / 2,
        x1: sx + w / 2,
      },
    ]
    const spot = tries.map((t) => ({ ...t, y0: t.y - 11, y1: t.y + 3 })).find((t) => !hits(t))
    if (!spot) continue
    boxes.push(spot)
    out.set(p.branch_id, { x: spot.x / k, y: spot.y / k, anchor: spot.anchor })
  }
  return out
}
