// FEATURE: AI-boom mode — labels on the map (owned by the boom track).
// Contract: default export BoomLayer() — SVG inside the map camera (map units; sizes divided by
// the zoom so the labels stay the same size on screen). GridMap draws the campuses' white squares;
// this adds their size ("1 GW") and, in AI-boom mode, a ring on the line that breaks first.
import { useMapView } from '../../GridMap'
import { useOverload } from '../../store'
import { sizeLabel } from './boomData'
import './boom.css'

export default function BoomLayer() {
  const { extraSites, mode, result, cascade, step, subPos } = useOverload()
  const { k, project } = useMapView()
  if (!extraSites.length) return null
  const u = 1 / k // one screen-ish unit at this zoom

  // the most overloaded line of the combined case: the cascade trips it first
  let first = null
  const worst = mode === 'boom' && !(cascade && step > 0) ? result?.overloaded?.[0] : null
  const campusSubs = new Set(extraSites.map((c) => c.sub))
  if (worst && (result.sites || []).some((s) => campusSubs.has(s.sub))) {
    const a = subPos(worst.from)
    const b = subPos(worst.to)
    if (a && b) {
      const [x1, y1] = project(a[0], a[1])
      const [x2, y2] = project(b[0], b[1])
      first = [(x1 + x2) / 2, (y1 + y2) / 2]
    }
  }

  return (
    <g className="boom-layer" aria-hidden="true">
      {extraSites.map((c) => {
        const [x, y] = project(c.lon, c.lat)
        return (
          // under the square: city names sit above-right of a point, town impacts to its right
          <text key={c.id} className="boom-tag" x={x} y={y + 19 * u} fontSize={11 * u} strokeWidth={3 * u}>
            {sizeLabel(c.mw)}
          </text>
        )
      })}
      {first && (
        <g className="boom-first" transform={`translate(${first[0]} ${first[1]})`}>
          <circle className="boom-first__ring" r={10 * u} />
          <circle className="boom-first__ring boom-first__ring--pulse" r={10 * u} />
          <text className="boom-first__label" x={-12 * u} y={-10 * u} fontSize={11 * u} strokeWidth={3 * u}>
            Breaks first
          </text>
        </g>
      )}
    </g>
  )
}
