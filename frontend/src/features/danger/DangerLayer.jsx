// FEATURE: danger zones on the map (owned by the danger track).
// Contract: default export DangerLayer({view?}) — SVG inside the map camera (a GridMap child).
//
// While the danger toggle is on: a soft red circle on each zone's substation, its size growing with
// the people hit (square-root scale, so area follows people), the top few labeled with the town and
// a compact count. A click drops the campus there (at the size the zones were found for) so the
// cascade can be run and watched. Sizes are in map units divided by the zoom, like the other layers,
// so the zones stay the same size on screen. While a cascade plays the zones step back.
// `view` = {k, project} when rendered outside GridMap (the preview portals it into the camera).
import { useMemo } from 'react'
import { useMapView } from '../../GridMap'
import { citiesFor } from '../../geo'
import { useOverload } from '../../store'
import './danger.css'
import { compact, isPlacedAt, setDangerHover, useDangerUi, useDangerZones, useResetOff } from './dangerStore'
import { usePlaceZone } from './usePlaceZone'

const LABELS = 8 // the top zones carry a label (the hovered one too)
const R_MIN = 7 // map units at zoom 1 (the fix pins are 10, the campus ring 16)
const R_MAX = 30
const PULSE = 3 // the top zones breathe (not under reduced motion)

export default function DangerLayer({ view }) {
  const mapView = useMapView()
  const { k, project } = view || mapView
  const { grid, site, cascade, cascading } = useOverload()
  const { hover } = useDangerUi()
  const dz = useDangerZones()
  const placeZone = usePlaceZone()
  useResetOff()

  const zones = useMemo(() => (dz.enabled && dz.data && !dz.data.already_failing ? dz.data.zones : []), [dz.enabled, dz.data])
  const drawn = useMemo(() => {
    const max = Math.max(1, ...zones.map((z) => z.people_hit))
    return zones.map((z, i) => {
      const [x, y] = project(z.lon, z.lat)
      return { z, i, x, y, rMap: R_MIN + (R_MAX - R_MIN) * Math.sqrt(Math.max(z.people_hit, 0) / max) }
    })
    // project changes with the region's projection, which comes with a new grid
  }, [zones, project, grid]) // eslint-disable-line react-hooks/exhaustive-deps
  const labels = useMemo(() => placeLabels(drawn, k, hover, grid, project), [drawn, k, hover, grid, project])

  if (!drawn.length) return null
  const quiet = !!(cascade || cascading) // the cascade is the show now
  const size = dz.stale ? null : dz.data.mw
  const zoneClass = (z) => `dz-zone${hover === z.id ? ' dz-zone--hover' : ''}${isPlacedAt(site, z) ? ' dz-zone--placed' : ''}`
  const handlers = (z) => ({
    onPointerDown: (e) => e.stopPropagation(), // not a map click: the zone places the campus itself
    onPointerUp: (e) => e.stopPropagation(),
    onClick: (e) => {
      e.stopPropagation()
      placeZone(z, size)
    },
    onPointerEnter: () => setDangerHover(z.id),
    onPointerLeave: () => setDangerHover(null),
  })

  return (
    <g className={`dz-layer${quiet ? ' dz-layer--quiet' : ''}${dz.stale ? ' dz-layer--stale' : ''}`} aria-hidden="true">
      <defs>
        <radialGradient id="dz-glow">
          <stop offset="0" className="dz-glow-core" />
          <stop offset="0.55" className="dz-glow-mid" />
          <stop offset="1" className="dz-glow-edge" />
        </radialGradient>
      </defs>
      {/* the glows, biggest underneath; then every zone's center on top, so each stays clickable */}
      {drawn.map(({ z, i, x, y, rMap }) => {
        const r = rMap / k
        return (
          <g key={z.id} className={zoneClass(z)} transform={`translate(${x} ${y})`} {...handlers(z)}>
            <title>{`${z.area}: ~${compact(z.people_hit)} people hit (estimate). Click to put the campus here.`}</title>
            <circle className="dz-glow" r={r} fill="url(#dz-glow)" />
            {i < PULSE && !quiet && <circle className="dz-pulse" r={r} />}
            <circle className="dz-ring" r={r} />
          </g>
        )
      })}
      {drawn.map(({ z, x, y }) => (
        <g key={z.id} className={zoneClass(z)} transform={`translate(${x} ${y})`} {...handlers(z)}>
          <circle className="dz-hit" r={6 / k} />
          <circle className="dz-core" r={2.2 / k} />
        </g>
      ))}
      {!quiet && (
        <g className="dz-labels">
          {labels.map((l) => (
            <text key={l.id} className="dz-label" x={l.x} y={l.y} fontSize={11 / k} strokeWidth={3 / k} textAnchor={l.anchor}>
              <tspan className="dz-label__area">{l.area}</tspan>
              <tspan className="dz-label__n" dx={4 / k}>
                {l.n}
              </tspan>
            </text>
          ))}
        </g>
      )}
    </g>
  )
}

// Labels for the top zones (and the hovered one), each tried right, left, above and below its circle
// in screen pixels until it overlaps no other label and no city name the map draws; skipped otherwise.
const CHAR_W = 6.4
const LINE_H = 13
function placeLabels(drawn, k, hover, grid, project) {
  const boxes = (grid ? citiesFor(grid) : []).map((c) => {
    const [x, y] = project(c.lon, c.lat)
    return { x0: x * k + 6, x1: x * k + 6 + c.name.length * 6.2, y0: y * k - 4 - 11, y1: y * k - 4 }
  })
  const hits = (b) => boxes.some((o) => b.x0 < o.x1 && o.x0 < b.x1 && b.y0 < o.y1 && o.y0 < b.y1)
  const out = []
  const wanted = drawn.filter((d) => d.i < LABELS || d.z.id === hover).sort((a, b) => (b.z.id === hover) - (a.z.id === hover) || a.i - b.i)
  for (const d of wanted) {
    const n = compact(d.z.people_hit)
    const w = (d.z.area.length + n.length + 1) * CHAR_W
    const sx = d.x * k
    const sy = d.y * k
    const r = d.rMap // the circle's radius in screen units (map units x k / k)
    const tries = [
      { anchor: 'start', x: sx + r + 5, y: sy + 4, x0: sx + r + 5, x1: sx + r + 5 + w },
      { anchor: 'end', x: sx - r - 5, y: sy + 4, x0: sx - r - 5 - w, x1: sx - r - 5 },
      { anchor: 'middle', x: sx, y: sy - r - 5, x0: sx - w / 2, x1: sx + w / 2 },
      { anchor: 'middle', x: sx, y: sy + r + 14, x0: sx - w / 2, x1: sx + w / 2 },
    ]
    const spot = tries.map((t) => ({ ...t, y0: t.y - LINE_H + 2, y1: t.y + 3 })).find((t) => !hits(t))
    if (!spot) continue
    boxes.push(spot)
    out.push({ id: d.z.id, area: d.z.area, n, anchor: spot.anchor, x: spot.x / k, y: spot.y / k })
  }
  return out
}
