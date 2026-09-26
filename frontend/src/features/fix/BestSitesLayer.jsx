// FEATURE: best sites — pins on the map (owned by the fix track).
// Contract: default export BestSitesLayer() — SVG inside the map camera.
//
// While mode === 'fix': numbered pins at the best sites for the current size (green: they take it;
// amber: nowhere does, these are the roomiest), and the branches the fix upgrades (dashed while
// proposed, solid once applied). A few dozen elements at most; drawn at constant screen size.
import { useOverload } from '../../store'
import { CITIES, project as projectLonLat } from '../../geo'
import { useMapView } from '../../GridMap'
import './fix.css'
import { shownSites, useBestSites, useFix, useHoverSite } from './fixStore'

export default function BestSitesLayer() {
  const { mode, mw, loadFactor, caseBody, upgrades, branchById, subById, region } = useOverload()
  const { k, project } = useMapView()
  const on = mode === 'fix'
  const best = useBestSites(mw, loadFactor, on, region)
  const fix = useFix(caseBody)
  const hover = useHoverSite()
  if (!on) return null

  const { list, fits } = shownSites(best.data)
  const pos = (subId) => {
    const s = subById.get(subId)
    return s ? project(s.lon, s.lat) : null
  }

  // upgraded branches: the found fix's (in its order, dashed until applied), then any other applied ones
  const ups = new Map()
  const cur = upgrades || {}
  if (fix.status === 'done') {
    fix.data.upgrades.forEach((u) => {
      ups.set(u.id, { from: u.from, to: u.to, added: u.added_mva, applied: Number(cur[u.id]) >= u.new_mva - 0.05 })
    })
  }
  Object.entries(cur).forEach(([id, mva]) => {
    const b = branchById.get(Number(id))
    if (b && !ups.has(b.id)) ups.set(b.id, { from: b.from_sub, to: b.to_sub, added: mva - b.rate_mva, applied: true })
  })
  const drawn = [...ups.entries()]
    .map(([id, u]) => ({ id, ...u, a: pos(u.from), b: pos(u.to) }))
    .filter((u) => u.a && u.b)
  const labels = ups.size <= 6 ? placeLabels(drawn, k) : []

  return (
    <g className="fix-layer" aria-hidden="true">
      <g className="fix-ups-map">
        {drawn.map((u) => {
          const cls = `fix-up-line${u.applied ? '' : ' fix-up-line--proposed'}`
          // a transformer (both ends in one substation): a ring around it
          return u.from === u.to ? (
            <circle key={u.id} className={cls} cx={u.a[0]} cy={u.a[1]} r={9 / k} />
          ) : (
            <line key={u.id} className={cls} x1={u.a[0]} y1={u.a[1]} x2={u.b[0]} y2={u.b[1]} />
          )
        })}
        {labels.map((l) => (
          <text key={l.id} className="fix-up-label" x={l.x} y={l.y} fontSize={11 / k} strokeWidth={3 / k}>
            {l.text}
          </text>
        ))}
      </g>
      <g className={`fix-pins${fits ? '' : ' fix-pins--closest'}`}>
        {list.map((s) => {
          const [x, y] = project(s.lon, s.lat)
          const lift = 16 / k
          return (
            <g key={s.sub} className={`fix-pin${hover === s.sub ? ' fix-pin--hover' : ''}`} transform={`translate(${x} ${y})`}>
              <circle className="fix-pin__ring" r={10 / k} />
              <line className="fix-pin__stem" x1={0} y1={0} x2={0} y2={-lift + 7 / k} />
              <circle className="fix-pin__foot" r={1.8 / k} />
              <g className="fix-pin__head" transform={`translate(0 ${-lift})`}>
                <circle className="fix-pin__dot" r={7.5 / k} />
                <text className="fix-pin__n" y={0.5 / k} fontSize={9.5 / k}>
                  {s.rank}
                </text>
              </g>
            </g>
          )
        })}
      </g>
    </g>
  )
}

// "+661 MVA" beside each upgrade, nudged down (in screen pixels) until no two labels overlap —
// a transformer and the line leaving it sit almost on top of each other.
// The map's city names (GridMap draws them at x + 6px, baseline y - 4px, 11px type) count as taken.
const LABEL_H = 14
function placeLabels(drawn, k) {
  const placed = CITIES.map((c) => {
    const [x, y] = projectLonLat(c.lon, c.lat)
    return { sx: x * k + 6, sy: y * k - 4, w: c.name.length * 6.4 }
  })
  drawn
    .filter((u) => u.added > 0)
    .map((u) => {
      // right of the line's east end (or of a transformer's ring), away from the site and its lines
      const end = u.a[0] >= u.b[0] ? u.a : u.b
      const [x, y] = u.from === u.to ? [u.a[0] + 13 / k, u.a[1] - 2 / k] : [end[0] + 13 / k, end[1] - 2 / k]
      const text = `+${Math.round(u.added).toLocaleString('en-US')} MVA`
      return { id: u.id, text, sx: x * k, sy: y * k, w: text.length * 6.6 }
    })
    .sort((p, q) => p.sy - q.sy)
    .forEach((l) => {
      const hits = (p) => l.sx < p.sx + p.w && p.sx < l.sx + l.w && Math.abs(p.sy - l.sy) < LABEL_H
      for (let n = 0; n < 12 && placed.some(hits); n++) {
        l.sy += LABEL_H
      }
      placed.push(l)
    })
  return placed.filter((l) => l.id != null).map((l) => ({ id: l.id, text: l.text, x: l.sx / k, y: l.sy / k }))
}
