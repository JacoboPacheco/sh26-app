import { useMemo } from 'react'
import { useMapView } from '../../GridMap'
import { CITIES } from '../../geo'
import { useOverload } from '../../store'
import './impact.css'
import { homesText, textWidth, useTowns } from './towns'

// Who loses power, on the map: the largest dark towns named where their darkness is (the
// MW-weighted centre of their substations that lost load), in small condensed type with a dark
// halo so they read over the lights. They appear as the cascade reaches them. Sizes are screen
// pixels (divided by the zoom), and labels never overlap each other, the map's city names, or a
// data-center marker — a town whose label can't fit is skipped. A town the map already names
// (Fort Myers, Miami…) gets just its homes, on the same line after the city's name.

const MAX_LABELS = 6
const LOOK_AT = 12 // towns tried, biggest first, until MAX_LABELS fit
const NAME_PX = 13
const HOMES_PX = 11
const LINE_GAP = 2
const CITY_PX = 11 // GridMap's .map-city labels: 11px, weight 500, at (x + 6, y - 4)
const GAP = 7 // px from a town's centre to its label
const PAD = 3 // px kept clear around every label
const SITE_R = 14 // px kept clear around a data-center marker
const FAR_GAP = SITE_R + PAD + 6 // the second ring of spots, for a town under a marker
const CITY_BY_NAME = new Map(CITIES.map((c) => [c.name, c]))

// a line of text's box around its baseline, in px
const lineBox = (x, baseline, w, px) => ({ x0: x, y0: baseline - px * 0.8, x1: x + w, y1: baseline + px * 0.25 })
const hits = (a, b, pad) => a.x0 - pad < b.x1 && b.x0 - pad < a.x1 && a.y0 - pad < b.y1 && b.y0 - pad < a.y1

function placeLabels(towns, k, project, sites) {
  const u = 1 / k // map units per screen px
  const taken = []
  CITIES.forEach((c) => {
    const [x, y] = project(c.lon, c.lat)
    taken.push(lineBox(x / u + 6, y / u - 4, textWidth(c.name, CITY_PX, 500), CITY_PX))
  })
  sites.forEach(([lon, lat]) => {
    const [x, y] = project(lon, lat)
    taken.push({ x0: x / u - SITE_R, y0: y / u - SITE_R, x1: x / u + SITE_R, y1: y / u + SITE_R })
  })
  const free = (b) => !taken.some((o) => hits(b, o, PAD))

  const labels = []
  for (const t of towns.slice(0, LOOK_AT)) {
    if (labels.length >= MAX_LABELS) break
    const homes = homesText(t.homes)
    const wHomes = textWidth(homes, HOMES_PX, 650, 'condensed')
    let label = null

    const city = CITY_BY_NAME.get(t.name)
    if (city) {
      // the map already names this city: its homes follow the name on the same line, or sit under it
      const [cx, cy] = project(city.lon, city.lat).map((v) => v / u)
      const wCity = textWidth(city.name, CITY_PX, 500)
      for (const [x, base] of [
        [cx + 6 + wCity + 5, cy - 4],
        [cx + 6, cy - 4 + CITY_PX * 0.25 + LINE_GAP + HOMES_PX * 0.8],
      ]) {
        const b = lineBox(x, base, wHomes, HOMES_PX)
        if (free(b)) {
          label = { tail: true, x, homesY: base, anchor: 'start', box: b }
          break
        }
      }
    }

    if (!label) {
      const [ax, ay] = project(t.lon, t.lat).map((v) => v / u)
      const wName = textWidth(t.name, NAME_PX, 700, 'condensed')
      const w = Math.max(wName, wHomes)
      const h = NAME_PX + LINE_GAP + HOMES_PX
      // block top-left corners around the centre: right, left, above, below, then the diagonals;
      // then the same ring farther out (clears a data-center marker sitting on the town)
      const spots = [GAP, FAR_GAP].flatMap((g) => [
        [ax + g, ay - h / 2],
        [ax - g - w, ay - h / 2],
        [ax - w / 2, ay - g - h],
        [ax - w / 2, ay + g],
        [ax + g, ay - g - h],
        [ax + g, ay + g],
        [ax - g - w, ay - g - h],
        [ax - g - w, ay + g],
      ])
      for (const [bx, by] of spots) {
        const b = { x0: bx, y0: by, x1: bx + w, y1: by + h }
        if (!free(b)) continue
        // text-anchor follows the side, so each line lines up with the edge nearest the town
        const anchor = bx >= ax ? 'start' : bx + w <= ax ? 'end' : 'middle'
        const x = anchor === 'start' ? bx : anchor === 'end' ? bx + w : bx + w / 2
        label = { tail: false, x, nameY: by + NAME_PX * 0.8, homesY: by + NAME_PX + LINE_GAP + HOMES_PX * 0.8, anchor, box: b }
        break
      }
    }

    if (!label) continue
    taken.push(label.box)
    labels.push({
      key: t.name,
      name: t.name,
      homes,
      tail: label.tail,
      anchor: label.anchor,
      x: label.x * u,
      nameY: label.tail ? null : label.nameY * u,
      homesY: label.homesY * u,
    })
  }
  return labels
}

export default function ImpactLayer() {
  const { k, project } = useMapView()
  const { site, result, extraSites, headroomOn, headroom } = useOverload()
  const towns = useTowns()

  // the data-center markers, where App draws them
  const sites = useMemo(
    () => [
      ...(site ? [[result?.sub_lon ?? site.lon, result?.sub_lat ?? site.lat]] : []),
      ...extraSites.map((s) => [s.lon, s.lat]),
    ],
    [site, result, extraSites],
  )
  const labels = useMemo(() => (towns.length ? placeLabels(towns, k, project, sites) : []), [towns, k, project, sites])

  // the heatmap asks a different question; keep the map to it
  if (!labels.length || (headroomOn && headroom)) return null
  return (
    <g className="impact-labels" aria-hidden="true" style={{ '--halo': `${3.2 / k}px` }}>
      {labels.map((l, i) => (
        <g key={l.key} className="impact-label" style={{ '--i': i }}>
          {!l.tail && (
            <text className="impact-label__name" x={l.x} y={l.nameY} textAnchor={l.anchor} fontSize={NAME_PX / k}>
              {l.name}
            </text>
          )}
          <text className="impact-label__homes" x={l.x} y={l.homesY} textAnchor={l.anchor} fontSize={HOMES_PX / k}>
            {l.homes}
          </text>
        </g>
      ))}
    </g>
  )
}
