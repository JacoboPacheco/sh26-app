import { useMapView } from '../../GridMap'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { selectEntry, useCatalog } from './catalogApi'
import { verdictOf } from './format'
import './catalog.css'

// The real campuses on the map (a GridMap child: SVG inside the camera, map units, sizes divided by
// the zoom). On the national map every campus shows; in a state, that state's. Dot area follows the
// reported MW; color only for trouble (amber = over limit, red = people without power in the model).
// A click selects the campus (DataCenterCard shows it) and doesn't drop a data center there.
// `view` = {k, project, region} when rendered outside GridMap (a preview portal); else the map's own.
export default function CatalogLayer({ view, onPick }) {
  const mapView = useMapView()
  const { k, project, region: mapRegion } = view || mapView
  const o = useOverload()
  const region = mapRegion || o?.region || 'FL'
  const { data, selected } = useCatalog()
  if (!data) return null
  const u = 1 / k
  const shown = data.entries
    .filter((e) => !e.duplicate_of && e.lat != null && e.lon != null && (region === 'US' || e.state === region))
    .sort((a, b) => (b.mw || 0) - (a.mw || 0)) // big ones underneath
  return (
    <g className="cat-layer">
      {shown.map((e) => {
        const [x, y] = project(e.lon, e.lat)
        const v = verdictOf(e.test)
        const r = Math.min(10, Math.max(2.5, 1.5 + Math.sqrt(e.mw || 0) / 9)) * u // screen px: 200 MW ≈ 3, 1 GW ≈ 5, 10 GW = 10
        const on = selected === e.id
        return (
          <g
            key={e.id}
            className={`cat-dot cat-dot--${v.key}${on ? ' cat-dot--on' : ''}`}
            transform={`translate(${x} ${y})`}
            onPointerDown={(ev) => ev.stopPropagation()} // not a map click: no data center dropped here
            onPointerUp={(ev) => ev.stopPropagation()}
            onClick={(ev) => {
              ev.stopPropagation()
              selectEntry(e.id)
              onPick?.(e)
            }}
          >
            <title>{`${e.name} · ${fmt(e.mw || 0)} MW reported · ${v.label}`}</title>
            <circle className="cat-dot__hit" r={Math.max(r, 7 * u)} />
            <circle className="cat-dot__mark" r={r} strokeWidth={1.2 * u} />
            {on && <circle className="cat-dot__ring" r={r + 4 * u} strokeWidth={2 * u} />}
          </g>
        )
      })}
    </g>
  )
}
