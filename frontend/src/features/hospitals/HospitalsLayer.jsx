// Hospitals on the map (SVG inside the camera, map units): every OpenStreetMap hospital the
// synthetic model reaches, as a small cross. Quiet by default — pale and faint — and colored only
// when it means something: red when its area lost most of its power (on backup, an estimate),
// amber when its area has partial outages. The hospital picked in the list gets a ring and its name.
//
// Mount as a GridMap child: <HospitalsLayer />. `view` ({k, project}) replaces the map's context
// when the layer is portaled into the map from outside GridMap's tree (Preview.jsx).
// Crosses never take the pointer: dropping a campus, panning and drawing a storm work through them.
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useMapView } from '../../GridMap'
import { useOverload } from '../../store'
import './hospitals.css'
import { pickHospital, useHospitalStatus, usePickedHospital } from './hospitalsApi'

const ARM = { ok: 2.1, strained: 3.2, backup: 3.6 } // half the cross, in screen px
const ORDER = { ok: 0, strained: 1, backup: 2 } // trouble drawn last, on top

export default function HospitalsLayer({ view }) {
  const ctx = useMapView()
  const { k, project } = view || ctx
  const o = useOverload()
  const st = useHospitalStatus()
  const picked = usePickedHospital()

  // Start over / a new state: forget the picked hospital
  const resetCount = o?.resetCount
  const seen = useRef(resetCount)
  useEffect(() => {
    if (resetCount === seen.current) return
    seen.current = resetCount
    pickHospital(null)
  }, [resetCount])

  const glyphs = useMemo(
    () =>
      (st.all || [])
        .filter((h) => h.in_model)
        .map((h) => {
          const [x, y] = project(h.lon, h.lat)
          return { id: h.id, x, y, status: h.status }
        })
        .sort((a, b) => ORDER[a.status] - ORDER[b.status]),
    [st.all, project],
  )

  // the heatmap asks a different question; keep the map to it
  if (!st.ready || (o?.headroomOn && o?.headroom)) return null
  const pick = picked && picked.region === st.region ? st.all.find((h) => h.id === picked.id) : null
  // the data-center markers, where the map draws them: the picked name goes on the side away from them
  const sites = [
    ...(o?.site ? [[o.result?.sub_lon ?? o.site.lon, o.result?.sub_lat ?? o.site.lat]] : []),
    ...(o?.extraSites || []).map((s) => [s.lon, s.lat]),
  ]

  return (
    <g className="hx-layer" aria-hidden="true" pointerEvents="none">
      <Crosses glyphs={glyphs} k={k} />
      {pick && <Picked h={pick} k={k} project={project} sites={sites} />}
    </g>
  )
}

// A few hundred crosses, memoized so the picked ring doesn't re-render them.
const Crosses = memo(function Crosses({ glyphs, k }) {
  return (
    <g className="hx-crosses" strokeWidth={1.3 / k}>
      {glyphs.map((g) => {
        const a = ARM[g.status] / k
        return (
          <g key={g.id} className={`hx hx--${g.status}`} transform={`translate(${g.x} ${g.y})`}>
            {g.status === 'backup' && <circle className="hx-glow" r={7 / k} />}
            {/* a dark edge so a colored cross reads over amber and red lines */}
            {g.status !== 'ok' && <path className="hx-under" d={`M${-a} 0H${a}M0 ${-a}V${a}`} strokeWidth={3.4 / k} />}
            <path className="hx-cross" d={`M${-a} 0H${a}M0 ${-a}V${a}`} />
          </g>
        )
      })}
    </g>
  )
})

// The picked hospital: a ring and its name, on the left when a data-center marker sits just to its right.
const CLEAR_PX = 170

function Picked({ h, k, project, sites }) {
  const [x, y] = project(h.lon, h.lat)
  const label = h.status === 'backup' ? 'on backup power' : h.status === 'strained' ? 'partial outages' : null
  const left = sites.some(([lon, lat]) => {
    const [sx, sy] = project(lon, lat)
    const dx = (sx - x) * k
    return dx > -12 && dx < CLEAR_PX && Math.abs((sy - y) * k) < 28
  })
  const tx = (left ? -12 : 12) / k
  const anchor = left ? 'end' : 'start'
  const halo = { '--halo': `${3 / k}px` }
  // a dark card behind the name, measured after layout: town labels and the "hit" counts sit in
  // the same places, and a halo alone left the two texts on top of each other
  const textRef = useRef(null)
  const [box, setBox] = useState(null)
  useLayoutEffect(() => {
    try {
      const b = textRef.current?.getBBox()
      const next = b && b.width ? { x: b.x, y: b.y, w: b.width, h: b.height } : null
      setBox((cur) => (cur && next && cur.x === next.x && cur.y === next.y && cur.w === next.w && cur.h === next.h ? cur : next))
    } catch {
      setBox(null) // getBBox throws while the SVG isn't rendered (e.g. display: none)
    }
  }, [h.name, label, k, tx, anchor])
  const pad = 4 / k
  return (
    <g className={`hx-pick hx-pick--${h.status}`} transform={`translate(${x} ${y})`}>
      {box && (
        <rect
          className="hx-pick__card"
          x={box.x - pad}
          y={box.y - pad}
          width={box.w + 2 * pad}
          height={box.h + 2 * pad}
          rx={4 / k}
          strokeWidth={1 / k}
        />
      )}
      <circle className="hx-pick__ring" r={9 / k} strokeWidth={1.2 / k} />
      <g ref={textRef}>
        <text className="hx-pick__name" x={tx} y={-2 / k} fontSize={12 / k} textAnchor={anchor} style={halo}>
          {h.name}
        </text>
        {label && (
          <text className="hx-pick__status" x={tx} y={11 / k} fontSize={10.5 / k} textAnchor={anchor} style={halo}>
            {label} (estimate)
          </text>
        )}
      </g>
    </g>
  )
}
