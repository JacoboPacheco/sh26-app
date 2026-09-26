import { useMemo } from 'react'
import { useMapView } from '../../GridMap'
import { useOverload } from '../../store'
import { useAreaList, useAreaUi } from './areaStore'
import './town.css'

// On the map (a GridMap child, inside the camera): the open area's lights ringed in the accent with
// its name over them, and the campus of the stress test under the pointer in the card. Sizes are
// screen pixels (divided by the zoom). `view` ({k, project}) overrides the map's context when the
// layer is portaled into the camera from outside GridMap (Preview.jsx).
export default function AreaLayer({ view }) {
  const ctx = useMapView()
  const { k, project } = view || ctx
  const o = useOverload()
  const ui = useAreaUi()
  const list = useAreaList(o.region)
  const gridRegion = o.grid?.meta?.region || 'FL'
  const on = ui.region === o.region && gridRegion === o.region

  const area = on && ui.slug ? list.data?.find((a) => a.slug === ui.slug) : null
  const dots = useMemo(
    () =>
      (area?.sub_ids || [])
        .map((id) => o.subById.get(id))
        .filter(Boolean)
        .map((s) => project(s.lon, s.lat)),
    [area, o.subById, project, gridRegion], // eslint-disable-line react-hooks/exhaustive-deps -- gridRegion: the projection changed
  )
  const hc = on ? ui.hoverCase : null
  if (!area && !hc) return null

  const [lx, ly] = area ? project(area.lon, area.lat) : [0, 0]
  return (
    <g className="area-layer" pointerEvents="none" aria-hidden="true">
      {dots.map(([x, y], i) => (
        <circle key={i} className="area-layer__ring" cx={x} cy={y} r={5 / k} strokeWidth={1.4 / k} />
      ))}
      {area && (
        <text className="area-layer__label" x={lx} y={ly - 12 / k} fontSize={14 / k} strokeWidth={3.5 / k} textAnchor="middle">
          {area.name}
        </text>
      )}
      {hc && <CaseMark hc={hc} k={k} project={project} />}
    </g>
  )
}

function CaseMark({ hc, k, project }) {
  const [x, y] = project(hc.lon, hc.lat)
  return (
    <g className="area-layer__case" transform={`translate(${x} ${y})`}>
      <circle className="area-layer__case-ring" r={13 / k} strokeWidth={1.2 / k} />
      <circle className="area-layer__case-dot" r={3.5 / k} />
    </g>
  )
}
